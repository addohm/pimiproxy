import json
import re
import subprocess
import urllib.request
from pathlib import Path

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from api.config_builder import build_config

# ── Paths (mounted volume) ────────────────────────────────────────────────────
PIMIPROXY_DIR  = Path("/pimiproxy")
CONFIG_JSON    = PIMIPROXY_DIR / ".pimiproxy-config.json"
CONFIG_YAML    = PIMIPROXY_DIR / "config" / "config.yaml"


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

    return ok({
        "state":       "running" if running else "stopped",
        "proxy_count": proxy_count,
        "provider_url": cfg.get("subscription_url", ""),
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

    PREVIEW_LIMIT = 20
    return ok({
        "user_groups":       cfg.get("proxy_groups", []),
        "user_rules":        user_rules,
        "sub_group_names":   sub_group_names,
        "sub_rule_count":    len(sub_rule_lines),
        "sub_rules_preview": sub_rule_lines[:PREVIEW_LIMIT],
        "proxy_names":       proxy_names,
        "provider_url":      cfg.get("subscription_url", ""),
    })


# ── Groups ────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def groups_view(request):
    body = _json_body(request)
    name = body.get("name", "").strip()
    if not name:
        return err("name is required")
    gtype = body.get("type", "select")
    proxies = body.get("proxies", ["DIRECT"])

    cfg = _read_cfg()
    groups = cfg.get("proxy_groups", [])
    if any(g["name"] == name for g in groups):
        return err(f"Group '{name}' already exists")

    groups.append({"name": name, "type": gtype, "proxies": proxies})
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
