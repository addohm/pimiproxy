import ipaddress
import json
import re
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import yaml

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from api.config_builder import (
    CONFIG_CANDIDATE, build_config, config_lock, engine_health,
    fetch_subscription, install_config, load_config, mihomo_test_cmd_docker,
    update_config, wait_for_controller, write_config,
)

# ── Paths (mounted volume) ────────────────────────────────────────────────────
PIMIPROXY_DIR  = Path("/pimiproxy")
CONFIG_JSON    = PIMIPROXY_DIR / ".pimiproxy-config.json"
CONFIG_YAML    = PIMIPROXY_DIR / "config" / "config.yaml"
META_JSON      = PIMIPROXY_DIR / ".pimiproxy-meta.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_cfg() -> dict:
    return load_config(CONFIG_JSON)


def _update_cfg(mutate):
    """
    Locked read-modify-write of the config file, shared with the host CLI
    and cron via pimiproxy_common. mutate(cfg) edits in place; it may return
    an error response to abort without writing. Returns (cfg, None) on
    success or (None, response) on abort.
    """
    result = update_config(CONFIG_JSON, mutate)
    if isinstance(result, dict):
        return result, None
    return None, result


def _test_cmd(cfg: dict) -> list:
    """mihomo -t via a throwaway container. The -v path must be the *host*
    path of the config dir (the daemon resolves it), not our mount."""
    host_cfg_dir = Path(cfg.get("install_dir", "/opt/pimiproxy")) / "config"
    image = cfg.get("engine_image") or "metacubex/mihomo:latest"
    return mihomo_test_cmd_docker(image, host_cfg_dir, CONFIG_CANDIDATE)


def _restart_engine(cfg: dict):
    """docker restart mihomo, then wait until its controller answers.
    Returns (True, version) or (False, reason)."""
    try:
        r = subprocess.run(["docker", "restart", "mihomo"],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, "docker restart timed out"
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip() or f"exit {r.returncode}"
        return False, f"docker restart failed: {detail}"
    return wait_for_controller(_get_gateway(), cfg.get("api_port", "9090"),
                               cfg.get("api_secret", ""), timeout=20)


def _commit(mutate):
    """
    The one path for changes that affect the engine. Under the shared
    config lock: re-read → mutate(cfg) → build config.yaml candidate →
    `mihomo -t` → only then persist the JSON and swap config.yaml in.
    Then restart and wait for the controller to answer.

    Returns (cfg, None) on success, or (cfg_or_None, error_response):
      - mutate() returned a response        → nothing written
      - mihomo rejected the candidate (422) → nothing written
      - restart / controller failed   (502) → config written, engine down
    """
    with config_lock(CONFIG_JSON):
        cfg = load_config(CONFIG_JSON)
        abort = mutate(cfg)
        if abort is not None:
            return None, abort
        valid, why = install_config(CONFIG_YAML.parent, build_config(cfg), _test_cmd(cfg))
        if not valid:
            return None, err(f"Mihomo rejected the new config, nothing was changed: {why}", 422)
        write_config(CONFIG_JSON, cfg)
    up, why = _restart_engine(cfg)
    if not up:
        return cfg, err(f"Config saved, but Mihomo did not come back up: {why}", 502)
    return cfg, None


def _api_secret() -> str | None:
    """Return the configured API secret, or None if config/secret is missing.
    Callers must treat None as "deny all" — never fall back to a default."""
    cfg = _read_cfg()
    secret = cfg.get("api_secret")
    if not isinstance(secret, str) or not secret:
        return None
    return secret


def _json_body(request) -> dict:
    try:
        return json.loads(request.body)
    except Exception:
        return {}


def ok(data=None) -> JsonResponse:
    return JsonResponse(data or {"ok": True})


def err(msg: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": msg}, status=status)


# ── Auth ──────────────────────────────────────────────────────────────────────

# In-process login throttle. Keyed by client IP. Two Gunicorn workers means
# up to 2x the nominal rate per IP — acceptable for a LAN tool, and still
# makes brute-forcing the api_secret impractical.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SEC   = 60
_LOGIN_LOCKOUT_SEC  = 300
_login_attempts: dict[str, list[float]] = {}
_login_lockouts: dict[str, float] = {}
_login_lock = threading.Lock()


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _login_throttle_check(ip: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds). allowed=False means locked out."""
    now = time.time()
    with _login_lock:
        until = _login_lockouts.get(ip, 0.0)
        if until > now:
            return False, int(until - now) + 1
        if until and until <= now:
            _login_lockouts.pop(ip, None)
            _login_attempts.pop(ip, None)
    return True, 0


def _login_record_failure(ip: str) -> int:
    """Record a failed attempt. Returns remaining attempts before lockout (0 = now locked)."""
    now = time.time()
    cutoff = now - _LOGIN_WINDOW_SEC
    with _login_lock:
        attempts = [t for t in _login_attempts.get(ip, []) if t > cutoff]
        attempts.append(now)
        _login_attempts[ip] = attempts
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            _login_lockouts[ip] = now + _LOGIN_LOCKOUT_SEC
            _login_attempts.pop(ip, None)
            return 0
        return _LOGIN_MAX_ATTEMPTS - len(attempts)


def _login_reset(ip: str):
    with _login_lock:
        _login_attempts.pop(ip, None)
        _login_lockouts.pop(ip, None)


@csrf_exempt
@require_http_methods(["POST"])
def login_view(request):
    ip = _client_ip(request)
    allowed, retry_after = _login_throttle_check(ip)
    if not allowed:
        resp = err(f"Too many failed attempts. Try again in {retry_after}s.", 429)
        resp["Retry-After"] = str(retry_after)
        return resp

    secret = _api_secret()
    if secret is None:
        return err("Server misconfigured: api_secret is not set", 503)
    body = _json_body(request)
    submitted = body.get("password")
    if isinstance(submitted, str) and submitted and submitted == secret:
        _login_reset(ip)
        request.session["authenticated"] = True
        request.session.save()
        return ok()

    _login_record_failure(ip)
    return err("Invalid password", 401)


@csrf_exempt
@require_http_methods(["POST"])
def logout_view(request):
    request.session.flush()
    return ok()


# ── Status ────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def status_view(request):
    cfg = _read_cfg()

    # Container state from the runtime, liveness from the engine's own
    # controller. "state" stays for older UI builds but now means healthy,
    # not merely "a container exists".
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", "mihomo"],
        capture_output=True, text=True, timeout=10,
    )
    proc_state = result.stdout.strip() if result.returncode == 0 else "missing"
    health = engine_health(proc_state, proc_state == "running",
                           cfg.get("api_port", "9090"), cfg.get("api_secret", ""),
                           cfg.get("mixed_port", "7890"), host=_get_gateway())
    sub_proxies = cfg.get("subscription_proxies_yaml", "")
    proxy_count = sub_proxies.count("- name:") if sub_proxies else 0

    meta = {}
    if META_JSON.exists():
        try:
            meta = json.loads(META_JSON.read_text())
        except Exception:
            pass

    return ok({
        "state":          "running" if health["healthy"] else "stopped",
        "engine":         health,
        "proxy_count":    proxy_count,
        "provider_url":   cfg.get("subscription_url", ""),
        "dashboard_port": meta.get("dashboard_port", "8080"),
        "dashboard":      meta.get("dashboard", "metacubexd"),
    })


# ── Config ────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def config_view(request):
    cfg = _read_cfg()

    sub_groups = cfg.get("subscription_groups_yaml", "")
    sub_rules  = cfg.get("subscription_rules_yaml", "")
    sub_proxies = cfg.get("subscription_proxies_yaml", "")

    sub_group_names = re.findall(
        r'^\s*-\s*name:\s*["\']?([^"\'\n]+?)["\']?\s*$',
        sub_groups, re.MULTILINE,
    ) if sub_groups else []

    proxy_names = re.findall(
        r'^\s*-\s*name:\s*["\']?([^"\'\n]+?)["\']?\s*$',
        sub_proxies, re.MULTILINE,
    ) if sub_proxies else []

    sub_rule_lines = [
        l.strip().lstrip("- ")
        for l in sub_rules.splitlines()
        if l.strip() and l.strip() != "rules:"
    ] if sub_rules else []

    def _parse_rule(r):
        """Convert a rule string like 'DOMAIN-SUFFIX,example.com,DIRECT' into a dict."""
        if isinstance(r, dict):
            return r
        parts = r.split(",", 2)
        if len(parts) == 3:
            return {"rule_type": parts[0], "value": parts[1], "target": parts[2]}
        if len(parts) == 2:
            return {"rule_type": parts[0], "value": "", "target": parts[1]}
        return {"rule_type": r, "value": "", "target": ""}

    raw_rules = cfg.get("rules", [])
    user_rules = [_parse_rule(r) for r in raw_rules]

    return ok({
        "user_groups":           cfg.get("proxy_groups", []),
        "user_rules":            user_rules,
        "sub_group_names":       sub_group_names,
        "system_group_names":    ["🧭 Meta Quest"],
        "sub_rule_count":        len(sub_rule_lines),
        "sub_rules":             sub_rule_lines,
        "proxy_names":           proxy_names,
        "provider_url":          cfg.get("subscription_url", ""),
        "auto_refresh_enabled":  cfg.get("auto_refresh_enabled", True),
        "refresh_interval_days": cfg.get("refresh_interval_days", 1),
    })


# ── Groups ────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def groups_view(request):
    body = _json_body(request)
    name = body.get("name", "").strip()
    if not name:
        return err("name is required")
    gtype   = body.get("type", "select")
    proxies = body.get("proxies", ["DIRECT"])

    group = {"name": name, "type": gtype, "proxies": proxies}
    # url-test / fallback / load-balance require a health-check URL and interval
    if gtype in ("url-test", "fallback", "load-balance"):
        group["url"]      = "https://www.gstatic.com/generate_204"
        group["interval"] = 300
        if gtype == "url-test":
            group["tolerance"] = 50

    def _apply(cfg):
        groups = cfg.get("proxy_groups", [])
        if any(g.get("name") == name for g in groups):
            return err(f"Group '{name}' already exists")
        groups.append(group)
        cfg["proxy_groups"] = groups

    cfg, failure = _commit(_apply)
    if failure:
        return failure
    return ok({"group": group})


@csrf_exempt
@require_http_methods(["DELETE"])
def group_detail_view(request, name):
    def _apply(cfg):
        groups = cfg.get("proxy_groups", [])
        new_groups = [g for g in groups if g.get("name") != name]
        if len(new_groups) == len(groups):
            return err(f"Group '{name}' not found", 404)
        cfg["proxy_groups"] = new_groups

    cfg, failure = _commit(_apply)
    if failure:
        return failure
    return ok()


# ── Rules ─────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def rules_view(request):
    body = _json_body(request)
    # Accept either a raw rule string or structured {rule_type, value, target}
    if "rule" in body:
        rule_str = body["rule"].strip()
    else:
        rule_type = body.get("rule_type", "").strip()
        value     = body.get("value", "").strip()
        target    = body.get("target", "").strip()
        if not rule_type or not target:
            return err("rule_type and target are required")
        if value:
            rule_str = f"{rule_type},{value},{target}"
        else:
            rule_str = f"{rule_type},{target}"

    def _apply(cfg):
        cfg.setdefault("rules", []).append(rule_str)

    cfg, failure = _commit(_apply)
    if failure:
        return failure
    return ok({"rule": rule_str})


@csrf_exempt
@require_http_methods(["DELETE"])
def rule_detail_view(request, index):
    removed = {}

    def _apply(cfg):
        rules = cfg.get("rules", [])
        if index < 0 or index >= len(rules):
            return err(f"Rule index {index} out of range", 404)
        removed["rule"] = rules.pop(index)
        cfg["rules"] = rules

    cfg, failure = _commit(_apply)
    if failure:
        return failure
    return ok({"removed": removed["rule"]})


@csrf_exempt
@require_http_methods(["PUT"])
def rules_reorder_view(request):
    body = _json_body(request)
    order = body.get("order", [])
    def _apply(cfg):
        rules = cfg.get("rules", [])
        if sorted(order) != list(range(len(rules))):
            return err("order must be a permutation of rule indices")
        cfg["rules"] = [rules[i] for i in order]

    cfg, failure = _commit(_apply)
    if failure:
        return failure
    return ok()


# ── Provider URL ──────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def provider_view(request):
    body = _json_body(request)
    url = body.get("url", "").strip()
    def _apply(cfg):
        cfg["subscription_url"] = url

    _update_cfg(_apply)
    return ok()


# ── Refresh ───────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def refresh_view(request):
    cfg = _read_cfg()
    url = cfg.get("subscription_url", "")
    if not url:
        return err("No provider URL configured")

    # Same fetch + parse as the CLI/cron path (User-Agent, block extraction,
    # GEOIP rule stripping) so a UI refresh and a CLI refresh are identical.
    blocks = fetch_subscription(url)
    if not blocks:
        return err("Fetch failed or no proxies: block in the provider response "
                   "(see pimiproxy-api container logs)")

    # The fetch above can take up to 30 s. Re-read under the lock and write
    # only the subscription keys, so anything edited meanwhile (provider
    # URL, rules, groups) is preserved rather than overwritten with the
    # snapshot taken before the fetch.
    def _apply(cfg):
        cfg["subscription_proxies_yaml"] = blocks.get("proxies", "")
        cfg["subscription_groups_yaml"]  = blocks.get("proxy-groups", "")
        cfg["subscription_rules_yaml"]   = blocks.get("rules", "")

    cfg, failure = _commit(_apply)
    if failure:
        return failure
    return ok({"proxy_count": cfg["subscription_proxies_yaml"].count("- name:")})


# ── Refresh Settings ──────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def refresh_settings_view(request):
    body = _json_body(request)
    # Validate before touching the file
    days = None
    if "refresh_interval_days" in body:
        try:
            days = int(body["refresh_interval_days"])
        except (TypeError, ValueError):
            return err("refresh_interval_days must be an integer")
        if days < 1:
            return err("refresh_interval_days must be at least 1")

    def _apply(cfg):
        if "auto_refresh_enabled" in body:
            cfg["auto_refresh_enabled"] = bool(body["auto_refresh_enabled"])
        if days is not None:
            cfg["refresh_interval_days"] = days

    _update_cfg(_apply)
    return ok()


# ── Check URL ─────────────────────────────────────────────────────────────────

def _get_gateway() -> str:
    """Return the Docker bridge gateway IP so the API container can reach
    host-network services (Mihomo) that are not on the Docker bridge."""
    try:
        with open('/proc/net/route') as fh:
            for line in fh:
                fields = line.strip().split()
                if len(fields) >= 3 and fields[1] == '00000000':
                    gw_int = int(fields[2], 16)
                    return socket.inet_ntoa(gw_int.to_bytes(4, 'little'))
    except Exception:
        pass
    return '172.17.0.1'


def _rule_matches(rule: dict, domain: str) -> bool:
    rt  = rule["rule_type"].upper()
    val = rule.get("value", "").lower()
    if rt == "DOMAIN":
        return domain == val
    if rt == "DOMAIN-SUFFIX":
        return domain == val or domain.endswith("." + val)
    if rt == "DOMAIN-KEYWORD":
        return bool(val) and val in domain
    if rt == "MATCH":
        return True
    if rt in ("IP-CIDR", "IP-CIDR6"):
        try:
            return ipaddress.ip_address(domain) in ipaddress.ip_network(val, strict=False)
        except ValueError:
            return False
    return False


@csrf_exempt
@require_http_methods(["POST"])
def check_view(request):
    body = _json_body(request)
    input_str = body.get("url", "").strip()
    if not input_str:
        return err("url is required")

    try:
        parsed = urlparse(input_str if "://" in input_str else f"http://{input_str}")
        domain = (parsed.hostname or input_str).lower()
    except Exception:
        domain = input_str.lower()

    cfg = _read_cfg()
    gateway    = _get_gateway()
    mixed_port = cfg.get("mixed_port", "7890")
    api_port   = cfg.get("api_port",   "9090")
    api_secret = cfg.get("api_secret", "")

    # ── Probe through Mihomo in a background thread ───────────────────────────
    probe_result = {}

    def _probe():
        proxy_url = f"http://{gateway}:{mixed_port}"
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
        try:
            req = urllib.request.Request(f"http://{domain}", method="HEAD")
            req.add_header("User-Agent", "pimiproxy-check/1.0")
            with opener.open(req, timeout=10) as resp:
                probe_result["ok"]     = resp.status < 500
                probe_result["status"] = f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            probe_result["ok"]     = e.code < 500
            probe_result["status"] = f"HTTP {e.code}"
        except Exception as e:
            probe_result["ok"]    = False
            probe_result["error"] = str(e)[:120]

    t = threading.Thread(target=_probe, daemon=True)
    t.start()

    # ── Poll Mihomo /connections every 100 ms while probe is in-flight ───────
    mihomo_conn = {}
    deadline = time.time() + 4.0
    while time.time() < deadline:
        time.sleep(0.1)
        try:
            mreq = urllib.request.Request(
                f"http://{gateway}:{api_port}/connections",
                headers={"Authorization": f"Bearer {api_secret}"},
            )
            with urllib.request.urlopen(mreq, timeout=2) as resp:
                data = json.loads(resp.read())
            for conn in reversed(data.get("connections") or []):
                host = conn.get("metadata", {}).get("host", "")
                if domain in host or host.endswith(domain):
                    mihomo_conn = {
                        "rule":    conn.get("rule", ""),
                        "payload": conn.get("rulePayload", ""),
                        "chains":  conn.get("chains") or [],
                    }
                    break
        except Exception:
            pass
        if mihomo_conn.get("rule"):
            break

    t.join(timeout=12)

    # ── Build rule-match response ─────────────────────────────────────────────
    if mihomo_conn.get("rule"):
        # Mihomo told us exactly what matched
        rule_type = mihomo_conn["rule"]
        payload   = mihomo_conn["payload"]
        chains    = mihomo_conn["chains"]          # e.g. ["🌐 Google/Youtube", "[REALITY]..."]
        target    = chains[0] if chains else ""
        rule_str  = f"{rule_type},{payload},{target}" if payload else f"{rule_type},{target}"
        response  = {
            "matched":    True,
            "domain":     domain,
            "rule":       rule_str,
            "rule_type":  rule_type,
            "value":      payload,
            "target":     target,
            "chains":     chains,
            "source":     "mihomo",
        }
    else:
        # Fall back to static rule parsing (covers DOMAIN/DOMAIN-SUFFIX/KEYWORD/MATCH)
        def _parse_rule(r):
            if isinstance(r, dict):
                return r
            parts = r.split(",", 2)
            if len(parts) == 3:
                return {"rule_type": parts[0], "value": parts[1], "target": parts[2]}
            if len(parts) == 2:
                return {"rule_type": parts[0], "value": "", "target": parts[1]}
            return {"rule_type": r, "value": "", "target": ""}

        user_rules = [_parse_rule(r) for r in cfg.get("rules", [])]
        sub_rules  = []
        for line in cfg.get("subscription_rules_yaml", "").splitlines():
            line = line.strip().lstrip("- ")
            if line and line != "rules:":
                sub_rules.append(_parse_rule(line))

        matched = source = None
        for rule in user_rules:
            if _rule_matches(rule, domain):
                matched, source = rule, "user"
                break
        if not matched:
            for rule in sub_rules:
                if _rule_matches(rule, domain):
                    matched, source = rule, "provider"
                    break

        if matched:
            rule_str = (
                f"{matched['rule_type']},{matched['value']},{matched['target']}"
                if matched["value"] else f"{matched['rule_type']},{matched['target']}"
            )
            response = {
                "matched":   True,
                "domain":    domain,
                "rule":      rule_str,
                "rule_type": matched["rule_type"],
                "value":     matched["value"],
                "target":    matched["target"],
                "chains":    [],
                "source":    source,
            }
        else:
            response = {
                "matched": False,
                "domain":  domain,
                "note":    "No static rule matched. GEOIP/GEOSITE rules require a live connection through Mihomo.",
            }

    # ── Attach connection test result ─────────────────────────────────────────
    if probe_result.get("ok"):
        response["connection_ok"]     = True
        response["connection_status"] = probe_result.get("status", "OK")
    elif "ok" in probe_result:
        response["connection_ok"]    = False
        response["connection_error"] = probe_result.get("error", "Failed")

    return ok(response)

# ── Import ───────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def import_user_config_view(request):
    content_type = request.content_type or ""
    try:
        if "application/json" in content_type:
            data = json.loads(request.body)
        else:
            data = yaml.safe_load(request.body)
    except Exception:
        return err("Could not parse file — expected YAML or JSON")

    if not isinstance(data, dict):
        return err("File must be a YAML/JSON object with proxy_groups and/or rules keys")

    proxy_groups = data.get("proxy_groups")
    rules = data.get("rules")

    if proxy_groups is None and rules is None:
        return err("File must contain at least one of: proxy_groups, rules")
    if proxy_groups is not None and not isinstance(proxy_groups, list):
        return err("proxy_groups must be a list")
    if rules is not None and not isinstance(rules, list):
        return err("rules must be a list")

    # Validate each entry so a malformed import is rejected cleanly rather than
    # raising KeyError inside build_config (which, before config validation was
    # added, would persist the bad group and then fail every rebuild incl. cron).
    if proxy_groups is not None:
        for i, g in enumerate(proxy_groups):
            if not isinstance(g, dict) or not g.get("name") or not g.get("type"):
                return err(f"proxy_groups[{i}] must be an object with non-empty 'name' and 'type'")
    if rules is not None:
        for i, r in enumerate(rules):
            if not isinstance(r, str) or not r.strip():
                return err(f"rules[{i}] must be a non-empty string")

    def _apply(cfg):
        if proxy_groups is not None:
            cfg["proxy_groups"] = proxy_groups
        if rules is not None:
            cfg["rules"] = rules

    cfg, failure = _commit(_apply)
    if failure:
        return failure
    return ok({"imported_groups": len(proxy_groups or []), "imported_rules": len(rules or [])})


# ── Export ────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def export_user_config_view(request):
    cfg = _read_cfg()
    data = {
        "proxy_groups": cfg.get("proxy_groups", []),
        "rules":        cfg.get("rules", []),
    }
    content = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    response = HttpResponse(content, content_type="application/x-yaml")
    response["Content-Disposition"] = 'attachment; filename="pimiproxy-user-config.yaml"'
    return response


@require_http_methods(["GET"])
def export_provider_yaml_view(request):
    cfg = _read_cfg()
    proxies = cfg.get("subscription_proxies_yaml", "")
    groups  = cfg.get("subscription_groups_yaml",  "")
    rules   = cfg.get("subscription_rules_yaml",   "")
    if not (proxies or groups or rules):
        return err("No provider data cached — run a refresh first", 404)
    parts = ["# Provider subscription cache — exported from pimiproxy"]
    url = cfg.get("subscription_url", "")
    if url:
        parts.append(f"# Source: {url}")
    parts.append("")
    if proxies: parts.append(proxies.rstrip())
    if groups:  parts.append(groups.rstrip())
    if rules:   parts.append(rules.rstrip())
    response = HttpResponse("\n".join(parts) + "\n", content_type="application/x-yaml")
    response["Content-Disposition"] = 'attachment; filename="pimiproxy-provider.yaml"'
    return response


@require_http_methods(["GET"])
def export_final_config_view(request):
    if not CONFIG_YAML.exists():
        return err("config.yaml not found", 404)
    response = HttpResponse(CONFIG_YAML.read_text(), content_type="application/x-yaml")
    response["Content-Disposition"] = 'attachment; filename="pimiproxy-config.yaml"'
    return response
