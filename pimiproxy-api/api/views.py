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

from api.config_builder import build_config

# ── Paths (mounted volume) ────────────────────────────────────────────────────
PIMIPROXY_DIR  = Path("/pimiproxy")
CONFIG_JSON    = PIMIPROXY_DIR / ".pimiproxy-config.json"
CONFIG_YAML    = PIMIPROXY_DIR / "config" / "config.yaml"
META_JSON      = PIMIPROXY_DIR / ".pimiproxy-meta.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_cfg() -> dict:
    if not CONFIG_JSON.exists():
        return {}
    return json.loads(CONFIG_JSON.read_text())


def _write_cfg(cfg: dict):
    CONFIG_JSON.write_text(json.dumps(cfg, indent=2))


def _rebuild_and_restart(cfg: dict):
    """Rebuild config.yaml from cfg dict and restart the mihomo container."""
    CONFIG_YAML.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_YAML.write_text(build_config(cfg))
    subprocess.run(["docker", "restart", "mihomo"],
                   capture_output=True, timeout=30)


def _api_secret() -> str:
    cfg = _read_cfg()
    return cfg.get("api_secret", "pimiproxy")


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

@csrf_exempt
@require_http_methods(["POST"])
def login_view(request):
    body = _json_body(request)
    if body.get("password") == _api_secret():
        request.session["authenticated"] = True
        request.session.save()
        return ok()
    return err("Invalid password", 401)


@csrf_exempt
@require_http_methods(["POST"])
def logout_view(request):
    request.session.flush()
    return ok()


# ── Status ────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def status_view(request):
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "mihomo"],
        capture_output=True, text=True, timeout=10,
    )
    running = result.stdout.strip() == "true"

    cfg = _read_cfg()
    sub_proxies = cfg.get("subscription_proxies_yaml", "")
    proxy_count = sub_proxies.count("- name:") if sub_proxies else 0

    meta = {}
    if META_JSON.exists():
        try:
            meta = json.loads(META_JSON.read_text())
        except Exception:
            pass

    return ok({
        "state":          "running" if running else "stopped",
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

    cfg = _read_cfg()
    groups = cfg.get("proxy_groups", [])
    if any(g["name"] == name for g in groups):
        return err(f"Group '{name}' already exists")

    groups.append(group)
    cfg["proxy_groups"] = groups
    _write_cfg(cfg)
    _rebuild_and_restart(cfg)
    return ok({"group": groups[-1]})


@csrf_exempt
@require_http_methods(["DELETE"])
def group_detail_view(request, name):
    cfg = _read_cfg()
    groups = cfg.get("proxy_groups", [])
    new_groups = [g for g in groups if g["name"] != name]
    if len(new_groups) == len(groups):
        return err(f"Group '{name}' not found", 404)
    cfg["proxy_groups"] = new_groups
    _write_cfg(cfg)
    _rebuild_and_restart(cfg)
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

    cfg = _read_cfg()
    rules = cfg.get("rules", [])
    rules.append(rule_str)
    cfg["rules"] = rules
    _write_cfg(cfg)
    _rebuild_and_restart(cfg)
    return ok({"rule": rule_str})


@csrf_exempt
@require_http_methods(["DELETE"])
def rule_detail_view(request, index):
    cfg = _read_cfg()
    rules = cfg.get("rules", [])
    if index < 0 or index >= len(rules):
        return err(f"Rule index {index} out of range", 404)
    removed = rules.pop(index)
    cfg["rules"] = rules
    _write_cfg(cfg)
    _rebuild_and_restart(cfg)
    return ok({"removed": removed})


@csrf_exempt
@require_http_methods(["PUT"])
def rules_reorder_view(request):
    body = _json_body(request)
    order = body.get("order", [])
    cfg = _read_cfg()
    rules = cfg.get("rules", [])
    if sorted(order) != list(range(len(rules))):
        return err("order must be a permutation of rule indices")
    cfg["rules"] = [rules[i] for i in order]
    _write_cfg(cfg)
    _rebuild_and_restart(cfg)
    return ok()


# ── Provider URL ──────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def provider_view(request):
    body = _json_body(request)
    url = body.get("url", "").strip()
    cfg = _read_cfg()
    cfg["subscription_url"] = url
    _write_cfg(cfg)
    return ok()


# ── Refresh ───────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def refresh_view(request):
    cfg = _read_cfg()
    url = cfg.get("subscription_url", "")
    if not url:
        return err("No provider URL configured")

    headers = {"User-Agent": "Clash-Verge/1.0 (clash-verge-rev; mihomo)"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return err(f"Fetch failed: {exc}")

    # Extract YAML blocks
    def _extract(text: str, key: str) -> str:
        pattern = rf'^({key}:.*?)(?=\n\S|\Z)'
        m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
        return m.group(1).rstrip() if m else ""

    cfg["subscription_proxies_yaml"]  = _extract(content, "proxies")
    cfg["subscription_groups_yaml"]   = _extract(content, "proxy-groups")
    cfg["subscription_rules_yaml"]    = _extract(content, "rules")

    _write_cfg(cfg)
    _rebuild_and_restart(cfg)
    return ok({"proxy_count": content.count("- name:")})


# ── Refresh Settings ──────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def refresh_settings_view(request):
    body = _json_body(request)
    cfg = _read_cfg()

    if "auto_refresh_enabled" in body:
        cfg["auto_refresh_enabled"] = bool(body["auto_refresh_enabled"])

    if "refresh_interval_days" in body:
        try:
            days = int(body["refresh_interval_days"])
            if days < 1:
                return err("refresh_interval_days must be at least 1")
            cfg["refresh_interval_days"] = days
        except (TypeError, ValueError):
            return err("refresh_interval_days must be an integer")

    _write_cfg(cfg)
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

    cfg = _read_cfg()
    if proxy_groups is not None:
        cfg["proxy_groups"] = proxy_groups
    if rules is not None:
        cfg["rules"] = rules
    _write_cfg(cfg)
    _rebuild_and_restart(cfg)
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
