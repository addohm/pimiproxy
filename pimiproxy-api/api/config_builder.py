"""
config_builder.py — self-contained Mihomo config.yaml generator.

Mirrors the build_config() logic from pimiproxy-core.py so the API
container can rebuild config.yaml without depending on the host script.
Keep this in sync with pimiproxy-core.py : build_config().
"""

import re


def yaml_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_config(cfg: dict) -> str:
    api_port         = cfg["api_port"]
    api_secret       = cfg["api_secret"]
    mixed_port       = cfg["mixed_port"]
    subscription_url = cfg.get("subscription_url", "")

    lines = []

    def L(s=""):
        lines.append(s)

    L("# Mihomo config — managed by pimiproxy")
    L("# Edit via the pimiproxy web UI or run:  sudo python3 pimiproxy-core.py configure")
    L()
    L(f"external-controller: 0.0.0.0:{api_port}")
    L(f'secret: "{api_secret}"')
    L()
    L(f"mixed-port: {mixed_port}")
    L("routing-mark: 666")
    L("allow-lan: true")
    L("mode: rule")
    L("log-level: info")
    L("ipv6: false")
    L("geo-auto-update: false")
    L()
    L("tun:")
    L("  enable: true")
    L("  stack: gvisor")
    L("  device: mihomo")
    L("  auto-route: true")
    L("  auto-detect-interface: true")
    L("  dns-hijack:")
    L("    - any:53")
    L()
    L("dns:")
    L("  enable: true")
    L("  listen: 0.0.0.0:53")
    L("  enhanced-mode: fake-ip")
    L("  nameserver:")
    L("    - 223.5.5.5")
    L("    - 119.29.29.29")
    L("  fallback:")
    L("    - 8.8.8.8")
    L("    - 1.1.1.1")
    L("  fallback-filter:")
    L("    geoip: false")
    L("  fake-ip-filter:")
    for entry in [
        "*.lan", "*.local",
        "connectivitycheck.gstatic.com", "connectivitycheck.android.com",
        "captive.apple.com", "*.msftconnecttest.com", "*.msftncsi.com",
        "time.android.com", "time.facebook.com", "time1.google.com",
        "pool.ntp.org", "+.pool.ntp.org",
        "stun.l.google.com",
        "+.meta.com", "+.oculus.com", "+.oculuscdn.com",
        "+.facebook.com", "+.fbcdn.net", "+.facebook.net",
        "+.fb.me", "+.facebook-hardware.com", "+.messenger.com",
        "+.instagram.com", "+.cdninstagram.com",
    ]:
        L(f'    - "{entry}"')
    L()

    # ── Two-layer merge: provider (base) + user (always on top) ───────────────
    sub_proxies = cfg.get("subscription_proxies_yaml", "")
    sub_groups  = cfg.get("subscription_groups_yaml", "")
    sub_rules   = cfg.get("subscription_rules_yaml", "")
    user_groups = cfg.get("proxy_groups", [])
    user_rules  = cfg.get("rules", [])

    # Proxies
    if sub_proxies:
        L()
        for line in sub_proxies.splitlines():
            L(line)
        L()
    elif subscription_url:
        L()
        L("proxies: []")
        L()

    _sub_proxy_names = re.findall(
        r'^\s*-\s*name:\s*["\']?([^"\'\n]+?)["\']?\s*$',
        sub_proxies, re.MULTILINE,
    ) if sub_proxies else []

    _sub_group_names = re.findall(
        r'^\s*-\s*name:\s*["\']?([^"\'\n]+?)["\']?\s*$',
        sub_groups, re.MULTILINE,
    ) if sub_groups else []

    # Helper: write a group dict as YAML
    def _write_group(g: dict):
        L(f'  - name: "{g["name"]}"')
        L(f'    type: {g["type"]}')
        if "use" in g:
            L("    use:")
            for u in g["use"]:
                L(f"      - {u}")
        if "proxies" in g:
            L("    proxies:")
            for p in g["proxies"]:
                L(f"      - {yaml_quote(p)}")
        if "url"       in g: L(f'    url: {g["url"]}')
        if "interval"  in g: L(f'    interval: {g["interval"]}')
        if "tolerance" in g: L(f'    tolerance: {g["tolerance"]}')

    # Proxy groups
    if sub_groups:
        L()
        for line in sub_groups.splitlines():
            L(line)
        L('  - name: "🧭 Meta Quest"')
        L('    type: select')
        L('    proxies:')
        L('      - "DIRECT"')
        if _sub_group_names:
            L(f'      - {yaml_quote(_sub_group_names[0])}')
        for name in _sub_proxy_names:
            L(f'      - {yaml_quote(name)}')
        for g in user_groups:
            _write_group(g)
        L()
    else:
        if subscription_url and not user_groups:
            user_groups = [{"name": "Proxy", "type": "select", "proxies": ["DIRECT"]}]
        L("proxy-groups:")
        for g in user_groups:
            _write_group(g)
        L('  - name: "🧭 Meta Quest"')
        L('    type: select')
        L('    proxies:')
        L('      - "DIRECT"')
        L('      - "Provider"')
        L()

    # Quest rules (always injected first)
    _quest_rules = [
        "DOMAIN-SUFFIX,meta.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,oculus.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,oculuscdn.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,facebook.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,fbcdn.net,🧭 Meta Quest",
        "DOMAIN-SUFFIX,facebook.net,🧭 Meta Quest",
        "DOMAIN-SUFFIX,instagram.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,lesmills.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,lesmillsplus.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,lesmillsondemand.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,brightcove.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,brightcove.net,🧭 Meta Quest",
        "DOMAIN-SUFFIX,bcovlive.io,🧭 Meta Quest",
        "DOMAIN-SUFFIX,boltdns.net,🧭 Meta Quest",
        "DOMAIN-SUFFIX,odderslab.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,cognitive3d.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,facebook-hardware.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,unity3d.com,🧭 Meta Quest",
        "DOMAIN-SUFFIX,golfplusvr.com,🧭 Meta Quest",
    ]

    if sub_rules:
        rule_indent = "  "
        for line in sub_rules.splitlines():
            s = line.strip()
            if s and s != "rules:" and s.startswith("-"):
                rule_indent = " " * (len(line) - len(line.lstrip()))
                break
        L()
        L("rules:")
        for r in _quest_rules:
            L(f"{rule_indent}- {r}")
        for r in user_rules:
            L(f"{rule_indent}- {r}")
        for line in sub_rules.splitlines():
            if line.strip() == "rules:":
                continue
            L(line)
        L()
    else:
        if not user_rules:
            if subscription_url or user_groups:
                user_rules = [
                    "DOMAIN-SUFFIX,lan,DIRECT",
                    "DOMAIN-SUFFIX,local,DIRECT",
                    "IP-CIDR,192.168.0.0/16,DIRECT",
                    "IP-CIDR,10.0.0.0/8,DIRECT",
                    "IP-CIDR,172.16.0.0/12,DIRECT",
                    "MATCH,Proxy",
                ]
            else:
                user_rules = ["MATCH,DIRECT"]
        L("rules:")
        for r in _quest_rules:
            L(f"  - {r}")
        for r in user_rules:
            L(f"  - {r}")
        L()

    return "\n".join(lines)
