#!/usr/bin/env python3
"""
pimiproxy-ultralite — install / uninstall / configure
Docker-free variant: installs mihomo as a native systemd service.
Supports Debian, Ubuntu, Fedora, Arch, openSUSE, Alpine, and derivatives.
Run as root: sudo python3 pimiproxy-ultralite.py [install|uninstall|configure]
"""

import os
import re
import sys
import shutil
import subprocess
import json
import threading
import platform
import itertools
from pathlib import Path

# =============================================================================
# REQUIRE ROOT
# =============================================================================
if os.geteuid() != 0:
    sys.exit(
        "Run as root:  sudo python3 pimiproxy-ultralite.py [install|uninstall|configure]"
    )


# =============================================================================
# COLOUR HELPERS
# =============================================================================
class C:
    RED    = "\033[0;31m"
    GREEN  = "\033[0;32m"
    CYAN   = "\033[0;36m"
    YELLOW = "\033[1;33m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    NC     = "\033[0m"


def info(msg):
    print(f"{C.GREEN}  ✔{C.NC}  {msg}")


def step(msg):
    print(f"\n{C.CYAN}{C.BOLD}▸ {msg}{C.NC}")


def warn(msg):
    print(f"{C.YELLOW}  ⚠{C.NC}  {msg}")


def banner(msg, colour=C.CYAN):
    print(f"\n{colour}{C.BOLD}{'─'*42}")
    print(f"  {msg}")
    print(f"{'─'*42}{C.NC}\n")


def die(msg):
    print(f"{C.RED}  ✖  {msg}{C.NC}", file=sys.stderr)
    sys.exit(1)


def yaml_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def confirm(msg, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    raw  = input(f"{C.YELLOW}  ?{C.NC}  {msg} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw == "y"


def ask(prompt, default=""):
    hint = f" [{default}]" if default else ""
    raw  = input(f"  {prompt}{hint}: ").strip()
    return raw if raw else default


def ask_secret(prompt, default=""):
    import getpass
    hint = f" [{default}]" if default else ""
    raw  = getpass.getpass(f"  {prompt}{hint}: ").strip()
    return raw if raw else default


def choose(prompt, options, default=1):
    print(f"  {prompt}")
    for i, opt in enumerate(options, 1):
        marker = " (default)" if i == default else ""
        print(f"    {i}) {opt}{marker}")
    while True:
        raw = input(f"  Selection [1-{len(options)}]: ").strip()
        if raw == "":
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        warn(f"Please enter a number between 1 and {len(options)}.")


# =============================================================================
# SPINNER
# =============================================================================
class Spinner:
    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, msg: str):
        self.msg   = msg
        self._stop = threading.Event()
        self._t    = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            print(f"\r  {C.CYAN}{frame}{C.NC}  {self.msg}", end="", flush=True)
            self._stop.wait(0.1)
        print(f"\r{' ' * (len(self.msg) + 10)}\r", end="", flush=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._t.join()


# =============================================================================
# SHELL HELPERS
# =============================================================================
def run(cmd, check=True, capture=False, silent=False, cwd=None):
    kwargs = dict(check=check)
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"]           = True
    elif silent:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    if isinstance(cmd, str):
        kwargs["shell"] = True
    if cwd is not None:
        kwargs["cwd"] = cwd
    return subprocess.run(cmd, **kwargs)


def run_ok(cmd):
    return run(cmd, check=False, silent=True).returncode == 0


def has_aes_hardware() -> bool:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, _, val = line.partition(":")
            if key.strip() in ("flags", "Features"):
                if "aes" in val.split():
                    return True
    except Exception:
        pass
    return False


# =============================================================================
# HOSTNAME
# =============================================================================
_DEFAULT_HOSTNAMES = {"raspberrypi", "localhost", "debian", "ubuntu", "alarm"}


def current_hostname() -> str:
    if shutil.which("hostname"):
        return run(["hostname"], capture=True).stdout.strip()
    if shutil.which("hostnamectl"):
        r = run(["hostnamectl", "--static"], capture=True, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    try:
        return Path("/etc/hostname").read_text().strip()
    except FileNotFoundError:
        return "localhost"


def set_hostname(new_name: str):
    old = current_hostname()
    run(["hostnamectl", "set-hostname", new_name])

    hosts = Path("/etc/hosts")
    txt   = hosts.read_text()
    if "127.0.1.1" in txt:
        hosts.write_text("\n".join(
            f"127.0.1.1\t{new_name}" if l.startswith("127.0.1.1") else l
            for l in txt.splitlines()))
    else:
        with hosts.open("a") as f:
            f.write(f"\n127.0.1.1\t{new_name}\n")

    cloud_cfg = Path("/etc/cloud/cloud.cfg")
    if cloud_cfg.exists():
        c = cloud_cfg.read_text()
        cloud_cfg.write_text(
            c.replace("preserve_hostname: false", "preserve_hostname: true"))
        info("cloud-init: preserve_hostname set.")

    info(f"Hostname: {old} -> {new_name}")


def dhcp_renew():
    result = run(["ip", "route", "show", "default"], capture=True, check=False)
    iface  = None
    if result and result.returncode == 0:
        for token in result.stdout.split():
            if token not in ("default", "via", "dev", "proto", "metric",
                             "onlink", "src", "scope", "link"):
                try:
                    import ipaddress
                    ipaddress.ip_address(token)
                except ValueError:
                    if not any(c in token for c in (".", ":")):
                        iface = token
                        break

    if not iface:
        warn("Could not detect default network interface — skipping DHCP renew.")
        return

    with Spinner(f"Renewing DHCP lease on {iface}"):
        if shutil.which("dhcpcd"):
            r = run(["dhcpcd", "--rebind", iface], check=False, silent=True)
        elif shutil.which("dhclient"):
            r = run(["dhclient", iface], check=False, silent=True)
        elif shutil.which("nmcli"):
            r = run(["nmcli", "device", "reapply", iface], check=False, silent=True)
        else:
            warn("No supported DHCP client found (dhcpcd/dhclient/nmcli).")
            return

    if r.returncode == 0:
        info("DHCP renewed — router should pick up the new hostname shortly.")
    else:
        warn("DHCP renew returned non-zero — router may not update immediately.")


def do_set_hostname():
    if len(sys.argv) < 3 or not sys.argv[2].strip():
        die("Usage: sudo python3 pimiproxy-ultralite.py hostname <new-hostname>")
    name = sys.argv[2].strip()
    step(f"Setting hostname to '{name}'")
    set_hostname(name)

    with Spinner("Restarting avahi-daemon"):
        result = run(["systemctl", "restart", "avahi-daemon"], check=False, silent=True)
    if result.returncode == 0:
        info("avahi-daemon restarted — .local will resolve to the new hostname.")
    else:
        warn("avahi-daemon not running — .local won't update until it starts.")

    dhcp_renew()
    if confirm("Reboot now to ensure the router fully registers the new hostname?",
               default=False):
        run(["reboot"])


# =============================================================================
# DISTRO DETECTION & PACKAGE MANAGEMENT
# =============================================================================
def detect_distro() -> str:
    os_release = Path("/etc/os-release")
    if os_release.exists():
        text = os_release.read_text().lower()
        if any(k in text for k in ("fedora", "rhel", "centos", "rocky", "alma")):
            return "fedora"
        if any(k in text for k in ("arch", "manjaro", "endeavour")):
            return "arch"
        if "opensuse" in text or "suse" in text:
            return "suse"
        if "alpine" in text:
            return "alpine"
    if shutil.which("apt-get"):  return "debian"
    if shutil.which("dnf") or shutil.which("yum"): return "fedora"
    if shutil.which("pacman"):   return "arch"
    if shutil.which("zypper"):   return "suse"
    if shutil.which("apk"):      return "alpine"
    return "debian"


def pkg_update(distro: str):
    if distro == "debian":
        run(["apt-get", "update", "-qq"], silent=True)
    elif distro == "arch":
        run(["pacman", "-Sy", "--noconfirm"], silent=True)
    elif distro == "suse":
        run(["zypper", "refresh"], silent=True)
    elif distro == "alpine":
        run(["apk", "update"], silent=True)
    # fedora: dnf is self-refreshing


def pkg_install(distro: str, *packages):
    with Spinner(f"Installing {', '.join(packages)}"):
        if distro == "debian":
            run(["apt-get", "install", "-y", "-qq", "--no-install-recommends",
                 *packages], silent=True)
        elif distro == "fedora":
            run(["dnf", "install", "-y", "--quiet", *packages], silent=True)
        elif distro == "arch":
            run(["pacman", "-S", "--noconfirm", "--needed", *packages], silent=True)
        elif distro == "suse":
            run(["zypper", "install", "-y", *packages], silent=True)
        elif distro == "alpine":
            run(["apk", "add", "--no-cache", *packages], silent=True)


def pkg_remove(distro: str, *packages):
    with Spinner(f"Removing {', '.join(packages)}"):
        if distro == "debian":
            run(["apt-get", "remove", "-y", *packages], silent=True, check=False)
        elif distro == "fedora":
            run(["dnf", "remove", "-y", *packages], silent=True, check=False)
        elif distro == "arch":
            run(["pacman", "-R", "--noconfirm", *packages], silent=True, check=False)
        elif distro == "suse":
            run(["zypper", "remove", "-y", *packages], silent=True, check=False)
        elif distro == "alpine":
            run(["apk", "del", *packages], silent=True, check=False)


# =============================================================================
# SUBSCRIPTION HELPERS
# =============================================================================
def extract_yaml_blocks(content: str) -> dict:
    target_keys  = {"proxies", "proxy-groups", "rules"}
    lines        = content.splitlines(keepends=True)
    blocks       = {}
    current_key  = None
    current_start = None

    for i, line in enumerate(lines):
        if (line and not line[0].isspace() and not line.startswith('#')
                and not line.startswith('-') and ':' in line):
            key = line.split(':')[0].strip()
            if current_key in target_keys:
                blocks[current_key] = "".join(lines[current_start:i]).rstrip()
            current_key   = key if key in target_keys else None
            current_start = i   if current_key       else None

    if current_key in target_keys:
        blocks[current_key] = "".join(lines[current_start:]).rstrip()

    return blocks


def fetch_subscription(url: str) -> dict:
    import urllib.request
    info("Fetching subscription...")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Clash-Verge/1.0 (clash-verge-rev; mihomo)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        warn(f"Could not fetch subscription: {e}")
        return {}

    blocks = extract_yaml_blocks(content)

    if "proxies" not in blocks:
        warn("No proxies: block found in subscription response.")
        warn("Confirm the URL returns a Clash/Mihomo-compatible config.")
        return {}

    count = blocks["proxies"].count("- name:")
    info(f"Found {count} proxies.")

    if "proxy-groups" in blocks:
        info("Found proxy-groups from provider — will use instead of defaults.")

    if "rules" in blocks:
        rules_lines = blocks["rules"].splitlines(keepends=True)
        filtered    = [l for l in rules_lines if "GEOIP," not in l.upper()]
        removed     = len(rules_lines) - len(filtered)
        if removed:
            info(f"Removed {removed} GEOIP rules (require MMDB — covered by IP-CIDR rules).")
        blocks["rules"] = "".join(filtered).rstrip()
        rule_count = blocks["rules"].count("    - ")
        info(f"Found {rule_count} rules from provider — will use instead of defaults.")

    return blocks


def save_subscription(install_path, url: str) -> bool:
    blocks = fetch_subscription(url)
    if not blocks:
        return False

    ext_cfg_path = install_path / ".pimiproxy-config.json"
    cfg = json.loads(ext_cfg_path.read_text()) if ext_cfg_path.exists() else {}
    cfg["subscription_proxies_yaml"] = blocks.get("proxies",      "")
    cfg["subscription_groups_yaml"]  = blocks.get("proxy-groups", "")
    cfg["subscription_rules_yaml"]   = blocks.get("rules",        "")
    ext_cfg_path.write_text(json.dumps(cfg, indent=2))
    return True


# =============================================================================
# CONFIG GENERATION
# =============================================================================
def build_config(cfg: dict) -> str:
    api_port         = cfg["api_port"]
    api_secret       = cfg["api_secret"]
    mixed_port       = cfg["mixed_port"]
    subscription_url = cfg.get("subscription_url", "")

    lines = []
    def L(s=""):
        lines.append(s)

    L("# Mihomo config — managed by pimiproxy-ultralite")
    L("# Edit via:  sudo python3 pimiproxy-ultralite.py configure")
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
            "*.lan.lan", "*.local.lan", "*.lan.localdomain", "*.local.localdomain",
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

    sub_proxies = cfg.get("subscription_proxies_yaml", "")
    sub_groups  = cfg.get("subscription_groups_yaml",  "")
    sub_rules   = cfg.get("subscription_rules_yaml",   "")
    user_groups = cfg.get("proxy_groups", [])
    user_rules  = cfg.get("rules", [])

    # ── proxies ───────────────────────────────────────────────────────────────
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
        sub_proxies, re.MULTILINE) if sub_proxies else []

    _sub_group_names = re.findall(
        r'^\s*-\s*name:\s*["\']?([^"\'\n]+?)["\']?\s*$',
        sub_groups, re.MULTILINE) if sub_groups else []

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

    # ── proxy-groups ──────────────────────────────────────────────────────────
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
        _quest_proxies = ["DIRECT"] + [g["name"] for g in user_groups]
        L('  - name: "🧭 Meta Quest"')
        L('    type: select')
        L('    proxies:')
        for p in _quest_proxies:
            L(f'      - {yaml_quote(p)}')
        L()

    # ── rules ─────────────────────────────────────────────────────────────────
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


# =============================================================================
# SYSTEMD SERVICE
# =============================================================================
def build_mihomo_service(install_dir: str) -> str:
    lines = []
    def L(s=""):
        lines.append(s)

    L("[Unit]")
    L("Description=Mihomo proxy — managed by pimiproxy-ultralite")
    L("After=network.target")
    L()
    L("[Service]")
    L("Type=simple")
    L(f"ExecStart=/usr/local/bin/mihomo -d {install_dir}/config")
    L("Restart=unless-stopped")
    L("RestartSec=3")
    L("LimitNOFILE=65535")
    L("AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE")
    L("CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE")
    L()
    L("[Install]")
    L("WantedBy=multi-user.target")

    return "\n".join(lines)


BYPASS_SERVICE_PATH = Path("/etc/systemd/system/pimiproxy-bypass.service")


# =============================================================================
# LOGIN BANNER
# =============================================================================
BANNER_PATH = Path("/etc/profile.d/pimiproxy-banner.sh")


def build_banner(cfg: dict, meta: dict) -> str:
    hostname   = meta.get("hostname",   "pimiproxy")
    api_port   = cfg.get("api_port",    meta.get("api_port",   "9090"))
    mixed_port = cfg.get("mixed_port",  meta.get("mixed_port", "7890"))
    cfg_file   = f"{meta.get('install_dir', '/opt/pimiproxy-lite')}/config/config.yaml"
    aes_ok     = has_aes_hardware()

    lines = []
    def L(s=""):
        lines.append(s)

    L("#!/usr/bin/env bash")
    L("# pimiproxy-ultralite login banner — managed by pimiproxy-ultralite.py")
    L("[[ $- == *i* ]] || return")
    L()
    L("_G=$'\\033[0;32m'  # green")
    L("_R=$'\\033[0;31m'  # red")
    L("_Y=$'\\033[1;33m'  # yellow")
    L("_C=$'\\033[0;36m'  # cyan")
    L("_B=$'\\033[1m'     # bold")
    L("_D=$'\\033[2m'     # dim")
    L("_N=$'\\033[0m'     # reset")
    L("_S='  ──────────────────────────────────────────'")
    L()
    L('printf "\\n${_C}${_B}  pimiproxy-ultralite${_N}\\n%s\\n" "$_S"')
    L()
    L("# ── Mihomo service state ─────────────────────────────────────────────")
    L("if systemctl is-active --quiet mihomo 2>/dev/null; then")
    L("  printf '  Proxy engine    %s\\n' \"${_G}●  running${_N}\"")
    L("else")
    L("  printf '  Proxy engine    %s\\n' \"${_R}●  stopped${_N}\"")
    L("fi")
    L()
    L("printf '  TUN mode        %s\\n' \"${_G}●  always on${_N}\"")
    L()
    L("# ── Proxy count ──────────────────────────────────────────────────────")
    L(f"_COUNT=$(grep -c '- name:' '{cfg_file}' 2>/dev/null || echo 0)")
    L('if [[ "$_COUNT" -gt 0 ]]; then')
    L("  printf '  Proxies loaded  %s\\n' \"${_D}${_COUNT}${_N}\"")
    L("fi")
    L()
    L("# ── AES hardware acceleration ─────────────────────────────────────────")
    if aes_ok:
        L("printf '  AES hardware    %s\\n' \"${_G}●  available${_N}\"")
    else:
        L("printf '  AES hardware    %s\\n' \"${_Y}○  software fallback — higher latency${_N}\"")
    L()
    L('printf "%s\\n" "$_S"')
    L('_PI_IP=$(hostname -I 2>/dev/null | awk \'{print $1}\' || ip -4 addr show scope global 2>/dev/null | awk \'/inet /{split($2,a,"/"); print a[1]; exit}\')')
    L('_GW=$(ip route 2>/dev/null | awk \'/^default/{print $3; exit}\')')
    L('_IFACE=$(ip route 2>/dev/null | awk \'/^default/{print $5; exit}\')')
    L('_CIDR=$(ip -4 addr show "$_IFACE" 2>/dev/null | awk \'/inet /{split($2,a,"/"); print a[2]; exit}\')')
    L('printf "  ${_Y}Tip:${_N} Some apps (Unity VR games etc.) bypass the system proxy.\\n"')
    L('printf "  Route all device traffic through this device instead:\\n"')
    L('printf "  Set the device WiFi to ${_B}static${_N} with these values:\\n"')
    L('printf "    IP address  →  pick any unused IP on your subnet\\n"')
    L('printf "    Subnet      →  /%s\\n" "${_CIDR:-24}"')
    L('printf "    Gateway     →  %s  ${_D}(this device)${_N}\\n" "${_PI_IP:-<device-ip>}"')
    L('printf "    DNS 1       →  %s  ${_D}(this device)${_N}\\n" "${_PI_IP:-<device-ip>}"')
    L('printf "    DNS 2       →  %s  ${_D}(your router, fallback)${_N}\\n" "${_GW:-<router-ip>}"')
    L()
    L('printf "%s\\n" "$_S"')
    L(f'printf "  ${{_C}}API${{_N}}        →  http://{hostname}.lan:{api_port}\\n"')
    L(f'printf "  ${{_B}}${{_C}}Proxy${{_N}}      →  ${{_B}}{hostname}.lan:{mixed_port}  (HTTP/SOCKS5)${{_N}}\\n"')
    L('printf "%s\\n" "$_S"')
    L('printf "  %s\\n" "${_D}pimiproxy-ultralite.py [status|logs|configure|debug]${_N}"')
    L('printf "%s\\n\\n" "$_S"')

    return "\n".join(lines) + "\n"


# =============================================================================
# MIHOMO BINARY FINDER
# =============================================================================
def _host_arch() -> str:
    return platform.machine()  # aarch64, armv7l, x86_64


def _find_mihomo_binary() -> "Path | None":
    arch       = _host_arch()
    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / "bin" / f"mihomo-{arch}",
        script_dir / "bin" / "mihomo",
        Path("/opt/pimiproxy-lite/bin") / f"mihomo-{arch}",
        Path("/usr/local/bin/mihomo"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


# =============================================================================
# METADATA
# =============================================================================
META_PATH = Path("/opt/pimiproxy-lite/.pimiproxy-meta.json")


def write_meta(data: dict):
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(data, indent=2))


def read_meta() -> dict:
    if META_PATH.exists():
        return json.loads(META_PATH.read_text())
    warn("No metadata found — using defaults.")
    return {
        "install_dir": "/opt/pimiproxy-lite",
        "hostname":    "pimiproxy",
        "api_port":    "9090",
        "mixed_port":  "7890",
        "api_secret":  "pimiproxy",
        "arch":        _host_arch(),
    }


# =============================================================================
# STATUS
# =============================================================================
def do_status():
    import time
    import urllib.request
    import urllib.error

    meta        = read_meta()
    install_dir = Path(meta["install_dir"])
    mixed_port  = meta.get("mixed_port", "7890")
    api_port    = meta.get("api_port",   "9090")
    hostname    = meta.get("hostname",   "pimiproxy")

    banner("pimiproxy-ultralite status", C.CYAN)

    # ── mihomo service ────────────────────────────────────────────────────────
    step("Mihomo service")
    result = run(["systemctl", "is-active", "mihomo"], capture=True, check=False)
    state  = result.stdout.strip()
    if state == "active":
        info("mihomo: running")
    else:
        warn(f"mihomo: {state}")

    # ── TUN ────────────────────────────────────────────────────────────────────
    step("TUN")
    if Path("/sys/class/net/mihomo").exists():
        info("TUN interface: up — all traffic tunneled through Mihomo.")
    else:
        warn("TUN interface: down — is the Mihomo service running?")

    # ── provider ──────────────────────────────────────────────────────────────
    step("Provider")
    ext_cfg_path = install_dir / ".pimiproxy-config.json"
    if ext_cfg_path.exists():
        cfg   = json.loads(ext_cfg_path.read_text())
        url   = cfg.get("subscription_url", "")
        if url:
            info(f"URL: {url}")
            count = cfg.get("subscription_proxies_yaml", "").count("- name:")
            info(f"Proxies loaded: {count}")
        else:
            info("No provider URL configured.")
    else:
        warn("Config not found — is pimiproxy-ultralite installed?")

    # ── proxy connectivity test ───────────────────────────────────────────────
    step("Proxy connectivity test")
    proxy_addr = f"http://127.0.0.1:{mixed_port}"
    tests      = [
        ("HTTP ", "http://www.gstatic.com/generate_204"),
        ("HTTPS", "https://www.gstatic.com/generate_204"),
    ]
    handler = urllib.request.ProxyHandler({"http": proxy_addr, "https": proxy_addr})
    opener  = urllib.request.build_opener(handler)
    all_ok  = True
    for label, test_url in tests:
        try:
            t0 = time.monotonic()
            with Spinner(f"Testing {label.strip()}"):
                with opener.open(test_url, timeout=10) as resp:
                    status = resp.status
                    ms     = (time.monotonic() - t0) * 1000
            info(f"{label}  {test_url}  ->  {status}  ({ms:.0f}ms)")
        except Exception as exc:
            warn(f"{label}  {test_url}  ->  FAILED  ({exc})")
            all_ok = False
    if all_ok:
        info("Proxy is working correctly.")
    else:
        warn("Proxy test failed — check Mihomo logs:")
        warn("  sudo python3 pimiproxy-ultralite.py logs")

    # ── hardware ──────────────────────────────────────────────────────────────
    step("Hardware")
    if has_aes_hardware():
        info("AES hardware acceleration: available.")
    else:
        warn("AES hardware acceleration: not available — software encryption, higher latency.")

    # ── endpoints ─────────────────────────────────────────────────────────────
    print()
    print(f"  API    ->  http://{hostname}.lan:{api_port}")
    print(f"  Proxy  ->  0.0.0.0:{mixed_port}  (HTTP/SOCKS5)")
    print()


# =============================================================================
# LOGS
# =============================================================================
def do_logs():
    step("Logs: mihomo  (Ctrl-C to stop)")
    try:
        run(["journalctl", "-u", "mihomo", "-f", "--lines=50"], check=False)
    except KeyboardInterrupt:
        print()


# =============================================================================
# RESTART
# =============================================================================
def do_restart():
    step("Restarting mihomo")
    run(["systemctl", "restart", "mihomo"])
    info("Done.")


# =============================================================================
# DEBUG URL
# =============================================================================
def do_debug():
    import urllib.request
    import urllib.error

    banner("pimiproxy-ultralite debug", C.CYAN)
    print("  Fetches a subscription URL using the Clash-Verge User-Agent and")
    print("  shows exactly what your provider returns.")
    print()

    if len(sys.argv) > 2:
        url = sys.argv[2].strip()
        info(f"URL: {url}")
    else:
        url = input("  Paste your provider URL: ").strip()

    if not url:
        die("No URL provided.")

    step("Sending request")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Clash-Verge/1.0 (clash-verge-rev; mihomo)"})
        with Spinner("Waiting for response"):
            with urllib.request.urlopen(req, timeout=30) as resp:
                status       = resp.status
                content_type = resp.headers.get("Content-Type", "(none)")
                content      = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        die(f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        die(f"Request failed: {e.reason}")
    except Exception as e:
        die(f"Request failed: {e}")

    info(f"Status       : {status}")
    info(f"Content-Type : {content_type}")
    info(f"Response size: {len(content):,} bytes / {len(content.splitlines())} lines")

    print()
    blocks = extract_yaml_blocks(content)
    if blocks:
        if "proxies" in blocks:
            info(f"Proxies      : {blocks['proxies'].count('- name:')}")
        else:
            warn("No 'proxies:' block found — provider may require a different URL or token.")
        if "proxy-groups" in blocks:
            info(f"Proxy groups : {blocks['proxy-groups'].count('- name:')}")
        if "rules" in blocks:
            info(f"Rules        : {blocks['rules'].count('    - ')}")
        info("Response looks like a valid Clash/Mihomo config.")
    else:
        warn("Response does not appear to contain valid Clash/Mihomo YAML.")
        warn("Check the URL and confirm your provider supports Clash/Mihomo clients.")

    out_path = Path("raw_debug_output.yaml")
    out_path.write_text(content, encoding="utf-8")
    info(f"Full response saved to: {out_path.resolve()}")

    print()
    print(f"  {C.BOLD}── First 20 lines ──────────────────────────{C.NC}")
    for line in content.splitlines()[:20]:
        print(f"  {line}")
    print()


# =============================================================================
# CONFIGURE MODE
# =============================================================================
def do_configure():
    banner("pimiproxy-ultralite configure", C.CYAN)

    meta         = read_meta()
    install_dir  = Path(meta["install_dir"])
    cfg_path     = install_dir / "config" / "config.yaml"
    ext_cfg_path = install_dir / ".pimiproxy-config.json"

    if not cfg_path.exists():
        die(f"Config not found at {cfg_path}. Has pimiproxy-ultralite been installed?")

    if ext_cfg_path.exists():
        cfg = json.loads(ext_cfg_path.read_text())
    else:
        cfg = {
            "api_port":    meta.get("api_port",   "9090"),
            "api_secret":  meta.get("api_secret", "pimiproxy"),
            "mixed_port":  meta.get("mixed_port", "7890"),
            "install_dir": meta["install_dir"],
            "subscription_url":          "",
            "proxy_groups":              [],
            "rules":                     [],
            "subscription_proxies_yaml": "",
            "subscription_groups_yaml":  "",
            "subscription_rules_yaml":   "",
        }

    changed = False

    while True:
        print(f"\n{C.BOLD}── Configure pimiproxy-ultralite ───────────{C.NC}")
        print("  1) Manage provider URL")
        print("  2) Manage proxy groups")
        print("  3) Manage rules")
        print("  4) Show current config")
        print("  5) Refresh provider now")
        print("  6) Change API secret")
        print("  7) Change ports")
        print("  8) Save and apply")
        print("  9) Exit without saving")
        print()
        choice = input("  Selection [1-9]: ").strip()

        # ── provider URL ─────────────────────────────────────────────────────
        if choice == "1":
            current = cfg.get("subscription_url", "")
            print(f"\n  Current provider URL: {current or '(none)'}")
            print()
            url = input("  Provider URL (leave blank to clear): ").strip()
            cfg["subscription_url"] = url
            info("Provider URL updated.")
            changed = True

        # ── proxy groups ─────────────────────────────────────────────────────
        elif choice == "2":
            while True:
                groups = cfg.get("proxy_groups", [])
                print(f"\n  {C.BOLD}Proxy groups ({len(groups)} configured):{C.NC}")
                for i, g in enumerate(groups, 1):
                    print(f"    {i}) {g['name']}  [{g['type']}]")
                print()
                print("  a) Add group")
                print("  r) Remove group")
                print("  b) Back")
                print()
                sub = input("  Selection: ").strip().lower()

                if sub == "b":
                    break
                elif sub == "a":
                    name = ask("Group name")
                    if not name:
                        warn("Name cannot be empty.")
                        continue
                    gtype     = choose("Group type", [
                        "select      — pick a server manually",
                        "url-test    — automatically use the fastest server",
                        "fallback    — use first available server",
                        "load-balance — spread traffic across servers",
                    ])
                    type_map  = {1: "select", 2: "url-test", 3: "fallback", 4: "load-balance"}
                    gtype_str = type_map[gtype]
                    raw       = input("  Proxy names (comma-separated): ").strip()
                    proxies   = [p.strip() for p in raw.split(",") if p.strip()]
                    if not proxies:
                        warn("No proxies entered.")
                        continue
                    group: dict = {"name": name, "type": gtype_str, "proxies": proxies}
                    if gtype_str in ("url-test", "fallback", "load-balance"):
                        group["url"]      = "https://www.gstatic.com/generate_204"
                        group["interval"] = 300
                        if gtype_str == "url-test":
                            group["tolerance"] = 50
                    cfg.setdefault("proxy_groups", []).append(group)
                    info(f"Added group '{name}' ({gtype_str}).")
                    changed = True
                elif sub == "r":
                    if not groups:
                        warn("No groups to remove.")
                        continue
                    raw = input("  Remove group number(s) (comma-separated): ").strip()
                    indices = []
                    for s in raw.split(","):
                        s = s.strip()
                        if s.isdigit() and 1 <= int(s) <= len(groups):
                            indices.append(int(s) - 1)
                    for i in sorted(set(indices), reverse=True):
                        removed = groups.pop(i)
                        info(f"Removed group '{removed['name']}'.")
                    cfg["proxy_groups"] = groups
                    changed = True

        # ── rules ────────────────────────────────────────────────────────────
        elif choice == "3":
            while True:
                rules = cfg.get("rules", [])
                print(f"\n  {C.BOLD}Rules ({len(rules)} configured):{C.NC}")
                for i, r in enumerate(rules, 1):
                    print(f"    {i:>3}) {r}")
                print()
                print("  a) Add rule")
                print("  r) Remove rule")
                print("  m) Move rule (reorder)")
                print("  p) Add common presets")
                print("  b) Back")
                print()
                sub = input("  Selection: ").strip().lower()

                if sub == "b":
                    break
                elif sub == "a":
                    print(f"\n  {C.BOLD}Add rule{C.NC}")
                    print("  Examples:")
                    print("    DOMAIN-SUFFIX,google.com,Proxy")
                    print("    IP-CIDR,192.168.0.0/16,DIRECT")
                    print("    DOMAIN-KEYWORD,youtube,Proxy")
                    print("    GEOIP,CN,DIRECT")
                    print("    MATCH,Proxy")
                    print()
                    rule = input("  Rule: ").strip()
                    if not rule:
                        warn("Rule cannot be empty.")
                        continue
                    pos_str = input(
                        f"  Insert at position (1-{len(rules)+1}, blank = end): ").strip()
                    if pos_str.isdigit():
                        pos = max(1, min(int(pos_str), len(rules) + 1)) - 1
                        rules.insert(pos, rule)
                    else:
                        rules.append(rule)
                    cfg["rules"] = rules
                    info(f"Added rule: {rule}")
                    changed = True
                elif sub == "r":
                    if not rules:
                        warn("No rules to remove.")
                        continue
                    raw = input("  Remove rule number(s) (comma-separated): ").strip()
                    indices = []
                    for s in raw.split(","):
                        s = s.strip()
                        if s.isdigit() and 1 <= int(s) <= len(rules):
                            indices.append(int(s) - 1)
                    for i in sorted(set(indices), reverse=True):
                        removed = rules.pop(i)
                        info(f"Removed: {removed}")
                    cfg["rules"] = rules
                    changed = True
                elif sub == "m":
                    if len(rules) < 2:
                        warn("Need at least 2 rules to reorder.")
                        continue
                    try:
                        frm  = int(input("  Move rule number: ").strip()) - 1
                        to   = int(input("  To position: ").strip()) - 1
                        rule = rules.pop(frm)
                        rules.insert(to, rule)
                        cfg["rules"] = rules
                        info(f"Moved '{rule}' to position {to + 1}.")
                        changed = True
                    except (ValueError, IndexError):
                        warn("Invalid input.")
                elif sub == "p":
                    presets = [
                        ("LAN bypass (all private IP ranges go DIRECT)", [
                            "IP-CIDR,192.168.0.0/16,DIRECT",
                            "IP-CIDR,10.0.0.0/8,DIRECT",
                            "IP-CIDR,172.16.0.0/12,DIRECT",
                        ]),
                        ("Local hostname bypass (.lan and .local go DIRECT)", [
                            "DOMAIN-SUFFIX,lan,DIRECT",
                            "DOMAIN-SUFFIX,local,DIRECT",
                        ]),
                        ("China mainland DIRECT (GEOIP)", ["GEOIP,CN,DIRECT"]),
                        ("Catch-all → Proxy",   ["MATCH,Proxy"]),
                        ("Catch-all → DIRECT",  ["MATCH,DIRECT"]),
                    ]
                    print(f"\n  {C.BOLD}Presets:{C.NC}")
                    for i, (label, _) in enumerate(presets, 1):
                        print(f"    {i}) {label}")
                    print()
                    sel = input("  Select preset(s) (comma-separated): ").strip()
                    for s in sel.split(","):
                        s = s.strip()
                        if s.isdigit() and 1 <= int(s) <= len(presets):
                            _, preset_rules = presets[int(s) - 1]
                            for r in preset_rules:
                                if r not in rules:
                                    rules.append(r)
                                    info(f"Added: {r}")
                    cfg["rules"] = rules
                    changed = True

        # ── show config ──────────────────────────────────────────────────────
        elif choice == "4":
            print()
            print(build_config(cfg))

        # ── refresh provider ─────────────────────────────────────────────────
        elif choice == "5":
            url = cfg.get("subscription_url", "")
            if not url:
                warn("No provider URL configured (option 1).")
            else:
                if save_subscription(install_dir, url):
                    cfg = json.loads(ext_cfg_path.read_text())
                    cfg_path.write_text(build_config(cfg))
                    info("config.yaml rebuilt.")
                    run(["systemctl", "restart", "mihomo"], check=False)
                    info("Mihomo restarted.")

        # ── change API secret ────────────────────────────────────────────────
        elif choice == "6":
            current_secret = cfg.get("api_secret", meta.get("api_secret", "pimiproxy"))
            print(f"\n  Current API secret: {current_secret}")
            new_secret = ask_secret("New API secret", current_secret)
            if new_secret != current_secret:
                cfg["api_secret"] = new_secret
                info("API secret updated — save and apply to activate.")
                changed = True
            else:
                info("Secret unchanged.")

        # ── change ports ─────────────────────────────────────────────────────
        elif choice == "7":
            cur_api   = cfg.get("api_port",  meta.get("api_port",  "9090"))
            cur_mixed = cfg.get("mixed_port", meta.get("mixed_port", "7890"))
            print(f"\n  {C.BOLD}Current ports:{C.NC}")
            print(f"    Mihomo API   port : {cur_api}")
            print(f"    Mixed proxy  port : {cur_mixed}")
            print()
            new_api   = ask("Mihomo API port",  cur_api)
            new_mixed = ask("Mixed proxy port", cur_mixed)
            if new_api != cur_api:
                cfg["api_port"] = new_api
                changed = True
            if new_mixed != cur_mixed:
                cfg["mixed_port"] = new_mixed
                changed = True
            if changed:
                info("Ports updated — save and apply to activate.")

        # ── save and apply ───────────────────────────────────────────────────
        elif choice == "8":
            if not changed:
                info("No changes to save.")
                break
            cfg_path.write_text(build_config(cfg))
            ext_cfg_path.write_text(json.dumps(cfg, indent=2))
            info("config.yaml written.")
            for key in ("api_port", "mixed_port", "api_secret"):
                if key in cfg:
                    meta[key] = cfg[key]
            write_meta(meta)
            if BANNER_PATH.exists():
                BANNER_PATH.write_text(build_banner(cfg, meta))
                info("Login banner updated.")
            if confirm("Restart Mihomo now to apply changes?", default=True):
                run(["systemctl", "restart", "mihomo"], check=False)
                info("Mihomo restarted.")
            break

        elif choice == "9":
            info("Exiting without saving.")
            break
        else:
            warn("Invalid selection.")


# =============================================================================
# INSTALL
# =============================================================================
def do_install():
    banner("pimiproxy-ultralite installer", C.CYAN)

    install_dir  = "/opt/pimiproxy-lite"
    _cur         = current_hostname()
    hostname_new = _cur if _cur.lower() not in _DEFAULT_HOSTNAMES else "pimiproxy"
    api_port     = "9090"
    mixed_port   = "7890"
    api_secret   = "pimiproxy"
    arch         = _host_arch()

    # ── express / advanced ────────────────────────────────────────────────────
    step("Install mode")
    print()
    print("  Express install uses all defaults — only asks for hostname and")
    print("  your provider URL. Everything else is pre-configured.")
    print()
    mode    = choose("Install mode", [
        "Express  (recommended)",
        "Advanced (configure all options)",
    ])
    express = (mode == 1)

    # ── prompts ───────────────────────────────────────────────────────────────
    step("Configuration")
    if express:
        hostname_new = ask("Hostname", hostname_new)
    else:
        install_dir  = ask("Install directory", install_dir)
        hostname_new = ask("Hostname",          hostname_new)
        api_port     = ask("Mihomo API port",   api_port)
        mixed_port   = ask("Mixed proxy port",  mixed_port)
        api_secret   = ask_secret("API secret", api_secret)

    print()
    print("  ── Provider URL " + "─" * 26)
    print("  If your proxy provider gave you a Clash/Mihomo API URL, enter it here.")
    print("  It typically looks like:")
    print("    https://provider.example.com/api?token=abc123")
    print()
    print("  Leave blank to skip — add later via:")
    print("    sudo python3 pimiproxy-ultralite.py configure")
    print("  " + "─" * 42)
    print()
    subscription_url = input("  Provider URL [leave blank to skip]: ").strip()

    # ── hostname ──────────────────────────────────────────────────────────────
    step("Hostname")
    _hostname_changed = hostname_new != current_hostname()
    if not _hostname_changed:
        info(f"Hostname unchanged: {hostname_new}")
    else:
        set_hostname(hostname_new)

    # ── packages ──────────────────────────────────────────────────────────────
    step("Packages")
    distro = detect_distro()
    info(f"Detected distro family: {distro}")

    with Spinner("Updating package lists"):
        pkg_update(distro)
    info("Package lists updated.")

    _mdns_pkgs = {
        "debian": ("avahi-daemon", "libnss-mdns"),
        "fedora": ("avahi", "nss-mdns"),
        "arch":   ("avahi", "nss-mdns"),
        "suse":   ("avahi", "nss-mdns"),
        "alpine": ("avahi",),
    }.get(distro, ("avahi-daemon",))
    pkg_install(distro, *_mdns_pkgs)
    with Spinner("Enabling avahi-daemon"):
        run(["systemctl", "enable", "--now", "avahi-daemon"], silent=True)
    info("avahi-daemon ready.")

    # ── systemd-resolved: free port 53 for Mihomo's DNS server ─────────────
    _resolved_conf = Path("/etc/systemd/resolved.conf.d")
    if shutil.which("systemd-resolve") or shutil.which("resolvectl"):
        _resolved_conf.mkdir(parents=True, exist_ok=True)
        (_resolved_conf / "no-stub.conf").write_text(
            "[Resolve]\nDNSStubListener=no\n"
        )
        run(["systemctl", "restart", "systemd-resolved"],
            silent=True, check=False)
        info("systemd-resolved: stub listener disabled (port 53 freed for Mihomo).")

    # ── firewalld masquerade + ports ─────────────────────────────────────────
    if shutil.which("firewall-cmd") and run_ok(
            ["systemctl", "is-active", "--quiet", "firewalld"]):
        with Spinner("Configuring firewalld"):
            run(["firewall-cmd", "--permanent", "--zone=public", "--add-masquerade"],
                silent=True, check=False)
            for _fw_port in [mixed_port, api_port]:
                run(["firewall-cmd", "--permanent", "--zone=public",
                     "--add-port=%s/tcp" % _fw_port], silent=True, check=False)
            run(["firewall-cmd", "--reload"], silent=True, check=False)
        info("firewalld: masquerade + proxy ports opened on public zone.")

    # ── mihomo binary ─────────────────────────────────────────────────────────
    step("Mihomo binary")
    binary = _find_mihomo_binary()
    if binary is None:
        die(
            f"No mihomo binary found for arch '{arch}'.\n"
            f"  Place 'mihomo-{arch}' (or 'mihomo') in the bin/ directory\n"
            f"  next to this script, then re-run.\n"
            f"  Download: https://github.com/MetaCubeX/mihomo/releases\n"
            f"  Decompress the .gz and rename to mihomo-{arch}"
        )
    dest_bin = Path("/usr/local/bin/mihomo")
    shutil.copy2(binary, dest_bin)
    dest_bin.chmod(0o755)
    info(f"Installed: {dest_bin}  (from {binary.name})")

    # ── directories + geodata ─────────────────────────────────────────────────
    step("Directories")
    install_path = Path(install_dir)
    (install_path / "config").mkdir(parents=True, exist_ok=True)
    info(f"Created {install_dir}/config")

    _script_dir = Path(__file__).resolve().parent
    for _gf in ["geoip.metadb", "geosite.dat", "country.mmdb"]:
        _src = _script_dir / "docker-images" / _gf
        _dst = install_path / "config" / _gf
        if _src.exists() and not _dst.exists():
            shutil.copy2(_src, _dst)
            info(f"Copied {_gf} to config directory")

    # ── config ────────────────────────────────────────────────────────────────
    step("Mihomo config")
    cfg = {
        "api_port":                  api_port,
        "api_secret":                api_secret,
        "mixed_port":                mixed_port,
        "install_dir":               install_dir,
        "subscription_url":          subscription_url,
        "proxy_groups":              [],
        "rules":                     [],
        "subscription_proxies_yaml": "",
        "subscription_groups_yaml":  "",
        "subscription_rules_yaml":   "",
        "auto_refresh_enabled":      True,
        "refresh_interval_days":     1,
    }

    if subscription_url:
        step("Subscription")
        blocks = fetch_subscription(subscription_url)
        if blocks:
            cfg["subscription_proxies_yaml"] = blocks.get("proxies",      "")
            cfg["subscription_groups_yaml"]  = blocks.get("proxy-groups", "")
            cfg["subscription_rules_yaml"]   = blocks.get("rules",        "")
        else:
            warn("Subscription fetch failed — Mihomo will start with no proxies.")
            warn("Run after install:  sudo python3 pimiproxy-ultralite.py refresh")

    (install_path / "config" / "config.yaml").write_text(build_config(cfg))
    (install_path / ".pimiproxy-config.json").write_text(json.dumps(cfg, indent=2))
    info("config.yaml written.")

    # ── systemd service ───────────────────────────────────────────────────────
    step("Mihomo systemd service")
    svc_path = Path("/etc/systemd/system/mihomo.service")
    svc_path.write_text(build_mihomo_service(install_dir))
    run(["systemctl", "daemon-reload"],       silent=True)
    run(["systemctl", "enable", "mihomo"],    silent=True)
    run(["systemctl", "start",  "mihomo"],    silent=True, check=False)
    info("mihomo.service installed and started.")

    # ── IP forwarding ─────────────────────────────────────────────────────────
    step("Enabling IP forwarding")
    sysctl_conf = Path("/etc/sysctl.d/99-pimiproxy.conf")
    sysctl_conf.write_text("net.ipv4.ip_forward = 1\n")
    run(["sysctl", "-w", "net.ipv4.ip_forward=1"], silent=True)
    info("IP forwarding enabled (persistent via /etc/sysctl.d/99-pimiproxy.conf).")

    # ── refresh script + cron ─────────────────────────────────────────────────
    step("Refresh script")
    script_dest = install_path / "pimiproxy-ultralite.py"
    shutil.copy2(__file__, script_dest)
    info(f"Script copied to {script_dest}.")

    cron_path = Path("/etc/cron.daily/pimiproxy-refresh")
    cron_path.write_text(
        "#!/usr/bin/env bash\n"
        "# pimiproxy-ultralite subscription auto-refresh\n"
        f'exec python3 "{script_dest}" refresh-cron\n'
    )
    cron_path.chmod(0o755)
    info("Daily refresh cron installed.")

    # ── metadata ──────────────────────────────────────────────────────────────
    meta = {
        "install_dir": install_dir,
        "hostname":    hostname_new,
        "api_port":    api_port,
        "mixed_port":  mixed_port,
        "api_secret":  api_secret,
        "arch":        arch,
    }
    write_meta(meta)

    # ── login banner ──────────────────────────────────────────────────────────
    step("Login banner")
    BANNER_PATH.write_text(build_banner(cfg, meta))
    BANNER_PATH.chmod(0o644)
    info(f"Login banner installed ({BANNER_PATH}).")

    _bashrc = Path("/etc/bash.bashrc")
    if not _bashrc.exists():
        _bashrc = Path("/etc/bashrc")
    _banner_source = f'[[ -f {BANNER_PATH} ]] && source {BANNER_PATH}'
    if _bashrc.exists():
        _txt = _bashrc.read_text()
        if str(BANNER_PATH) not in _txt:
            _bashrc.write_text(_txt.rstrip() + "\n\n" + _banner_source + "\n")
            info(f"Banner source added to {_bashrc} for desktop terminals.")

    # ── summary ───────────────────────────────────────────────────────────────
    if shutil.which("hostname"):
        _r = run(["hostname", "-I"], capture=True, check=False)
        lan_ip = (_r.stdout.strip().split() or [""])[0]
    else:
        lan_ip = ""
    if not lan_ip:
        _r = run(["ip", "-4", "addr", "show", "scope", "global"],
                 capture=True, check=False)
        _m = re.search(r'inet (\S+)/', _r.stdout) if _r else None
        lan_ip = _m.group(1) if _m else "<device-ip>"

    _gw_r = run(["ip", "route", "show", "default"], capture=True, check=False)
    _gw   = ""
    _cidr = "24"
    if _gw_r and _gw_r.returncode == 0:
        _tokens = _gw_r.stdout.split()
        for _i, _t in enumerate(_tokens):
            if _t == "via" and _i + 1 < len(_tokens):
                _gw = _tokens[_i + 1]
            if _t == "dev" and _i + 1 < len(_tokens):
                _iface  = _tokens[_i + 1]
                _addr_r = run(["ip", "-4", "addr", "show", _iface],
                              capture=True, check=False)
                if _addr_r and _addr_r.returncode == 0:
                    _m = re.search(r'inet \S+/(\d+)', _addr_r.stdout)
                    if _m:
                        _cidr = _m.group(1)
                break

    SEP = f"  {'─'*42}"
    B, G, Y, D, N, C2 = C.BOLD, C.GREEN, C.YELLOW, C.DIM, C.NC, C.CYAN

    print()
    print(f"{B}{G}  pimiproxy-ultralite — installation complete{N}")
    print(SEP)
    print(f"  {Y}Tip:{N} Some apps (Unity VR games etc.) bypass the system proxy.")
    print(f"  Route all device traffic through this device instead:")
    print(f"  Set the device WiFi to {B}static{N} with these values:")
    print(f"    IP address  →  pick any unused IP on your subnet")
    print(f"    Subnet      →  /{_cidr}")
    print(f"    Gateway     →  {lan_ip}  {D}(this device){N}")
    print(f"    DNS 1       →  {lan_ip}  {D}(this device){N}")
    print(f"    DNS 2       →  {_gw or '<router-ip>'}  {D}(your router, fallback){N}")
    print(SEP)
    print(f"  {C2}API{N}    →  http://{hostname_new}.lan:{api_port}")
    print(f"         →  http://{lan_ip}:{api_port}")
    print(f"  {B}{C2}Proxy{N}  →  {B}{hostname_new}.lan:{mixed_port}  (HTTP/SOCKS5){N}")
    print(f"  {Y}Login{N}  →  API secret: {B}{api_secret}{N}")
    print(SEP)
    print(f"  {D}pimiproxy-ultralite.py [configure|status|logs|debug]{N}")
    print(SEP)
    print()

    if not has_aes_hardware():
        warn("Hardware AES not available — encryption runs in software. Expect higher latency.")
    if _hostname_changed:
        dhcp_renew()
        info(f"{hostname_new}.local is available immediately via mDNS.")
        warn(f"{hostname_new}.lan requires a DHCP lease renewal or reboot to register with your router.")
        if confirm("Reboot now to ensure the router fully registers the new hostname?",
                   default=False):
            run(["reboot"])
    else:
        warn(f"{hostname_new}.local is available via mDNS.")
        warn(f"{hostname_new}.lan resolves via your router's DNS.")
    print()


# =============================================================================
# EXPORT
# =============================================================================
def do_export():
    meta         = read_meta()
    install_dir  = Path(meta["install_dir"])
    ext_cfg_path = install_dir / ".pimiproxy-config.json"
    cfg          = json.loads(ext_cfg_path.read_text()) if ext_cfg_path.exists() else {}

    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)

    banner("pimiproxy-ultralite export", C.CYAN)

    user_data = {
        "proxy_groups": cfg.get("proxy_groups", []),
        "rules":        cfg.get("rules",        []),
    }
    user_path = out_dir / "pimiproxy-user-config.json"
    user_path.write_text(json.dumps(user_data, indent=2))
    info(f"User config (groups + rules)  →  {user_path}")

    proxies = cfg.get("subscription_proxies_yaml", "")
    groups  = cfg.get("subscription_groups_yaml",  "")
    rules   = cfg.get("subscription_rules_yaml",   "")
    if proxies or groups or rules:
        parts = ["# Provider subscription cache — exported from pimiproxy-ultralite"]
        url   = cfg.get("subscription_url", "")
        if url:
            parts.append(f"# Source: {url}")
        parts.append("")
        if proxies: parts.append(proxies.rstrip())
        if groups:  parts.append(groups.rstrip())
        if rules:   parts.append(rules.rstrip())
        provider_path = out_dir / "pimiproxy-provider.yaml"
        provider_path.write_text("\n".join(parts) + "\n")
        info(f"Provider subscription YAML    →  {provider_path}")
    else:
        warn("No provider YAML cached — skipping (run refresh first if needed).")

    config_yaml = install_dir / "config" / "config.yaml"
    if config_yaml.exists():
        dest = out_dir / "pimiproxy-config.yaml"
        shutil.copy2(config_yaml, dest)
        info(f"Final Mihomo config           →  {dest}")
    else:
        warn("config.yaml not found — skipping.")
    print()


# =============================================================================
# REFRESH
# =============================================================================
def do_refresh():
    meta         = read_meta()
    install_dir  = Path(meta["install_dir"])
    ext_cfg_path = install_dir / ".pimiproxy-config.json"

    if not ext_cfg_path.exists():
        die("No pimiproxy-ultralite config found. Is it installed?")

    cfg = json.loads(ext_cfg_path.read_text())
    url = cfg.get("subscription_url", "")

    if not url:
        info("No provider URL configured — nothing to refresh.")
        return

    step("Refreshing provider")
    if not save_subscription(install_dir, url):
        die("Provider refresh failed.")

    cfg      = json.loads(ext_cfg_path.read_text())
    cfg_path = install_dir / "config" / "config.yaml"
    cfg_path.write_text(build_config(cfg))
    info("config.yaml rebuilt.")

    run(["systemctl", "restart", "mihomo"], check=False)
    info("Mihomo restarted.")

    if BANNER_PATH.exists():
        meta = read_meta()
        BANNER_PATH.write_text(build_banner(cfg, meta))
        info("Login banner updated.")


# =============================================================================
# REFRESH-CRON
# =============================================================================
def do_refresh_cron():
    import time

    meta         = read_meta()
    install_dir  = Path(meta["install_dir"])
    ext_cfg_path = install_dir / ".pimiproxy-config.json"

    if not ext_cfg_path.exists():
        return

    cfg = json.loads(ext_cfg_path.read_text())

    if not cfg.get("auto_refresh_enabled", True):
        return

    interval_days = int(cfg.get("refresh_interval_days", 1))
    stamp_path    = install_dir / ".last_refresh"

    if interval_days > 1 and stamp_path.exists():
        try:
            last         = float(stamp_path.read_text().strip())
            elapsed_days = (time.time() - last) / 86400
            if elapsed_days < interval_days:
                return
        except (ValueError, OSError):
            pass

    do_refresh()
    stamp_path.write_text(str(time.time()))


# =============================================================================
# UNINSTALL
# =============================================================================
def do_uninstall():
    banner("pimiproxy-ultralite uninstaller", C.RED)

    meta        = read_meta()
    install_dir = Path(meta["install_dir"])

    if not confirm("Stop and remove pimiproxy-ultralite, Mihomo, and all config files?"):
        print("Aborted.")
        return

    # ── mihomo service ────────────────────────────────────────────────────────
    step("Stopping Mihomo service")
    run(["systemctl", "stop",    "mihomo"], check=False, silent=True)
    run(["systemctl", "disable", "mihomo"], check=False, silent=True)
    svc_path = Path("/etc/systemd/system/mihomo.service")
    if svc_path.exists():
        svc_path.unlink()
        info("Removed mihomo.service")
    run(["systemctl", "daemon-reload"], silent=True, check=False)
    info("Mihomo service stopped and removed.")

    # ── mihomo binary ─────────────────────────────────────────────────────────
    step("Removing Mihomo binary")
    bin_path = Path("/usr/local/bin/mihomo")
    if bin_path.exists():
        bin_path.unlink()
        info("Removed /usr/local/bin/mihomo")

    # ── files ─────────────────────────────────────────────────────────────────
    step("Removing files")

    if install_dir.exists():
        shutil.rmtree(install_dir)
        info(f"Removed {install_dir}")

    # Clean up legacy clash-system if present from older installs
    cs = Path("/usr/local/bin/clash-system")
    if cs.exists():
        run([str(cs), "off"], check=False, silent=True)
        cs.unlink()
        info("Removed legacy /usr/local/bin/clash-system")

    cron_path = Path("/etc/cron.daily/pimiproxy-refresh")
    if cron_path.exists():
        cron_path.unlink()
        info("Removed daily refresh cron.")

    if BANNER_PATH.exists():
        BANNER_PATH.unlink()
        info(f"Removed login banner ({BANNER_PATH}).")

    for _rc in [Path("/etc/bash.bashrc"), Path("/etc/bashrc")]:
        if _rc.exists():
            _txt = _rc.read_text()
            if str(BANNER_PATH) in _txt:
                _rc.write_text("\n".join(
                    l for l in _txt.splitlines()
                    if str(BANNER_PATH) not in l
                ) + "\n")
                info(f"Removed banner source from {_rc}.")

    sysctl_conf = Path("/etc/sysctl.d/99-pimiproxy.conf")
    if sysctl_conf.exists():
        sysctl_conf.unlink()
        run(["sysctl", "-w", "net.ipv4.ip_forward=0"], check=False, silent=True)
        info("Removed IP forwarding config and disabled forwarding.")

    # Clean up legacy bypass service from older installs
    if BYPASS_SERVICE_PATH.exists():
        run(["systemctl", "stop",    "pimiproxy-bypass"], check=False, silent=True)
        run(["systemctl", "disable", "pimiproxy-bypass"], check=False, silent=True)
        BYPASS_SERVICE_PATH.unlink()
        run(["systemctl", "daemon-reload"], silent=True, check=False)
        info("Removed legacy bypass service.")
    run(["iptables", "-t", "mangle", "-D", "OUTPUT", "-j", "MARK", "--set-mark", "666"],
        check=False, silent=True)

    # ── hostname ──────────────────────────────────────────────────────────────
    step("Hostname")
    current = current_hostname()
    if current == meta.get("hostname"):
        if confirm(f"Reset hostname '{current}' back to 'raspberrypi'?"):
            run(["hostnamectl", "set-hostname", "raspberrypi"])
            hosts = Path("/etc/hosts")
            txt   = hosts.read_text()
            hosts.write_text("\n".join(
                "127.0.1.1\traspberrypi" if l.startswith("127.0.1.1") else l
                for l in txt.splitlines()))
            info("Hostname reset to 'raspberrypi'.")

    # ── optional package removal ───────────────────────────────────────────────
    step("Optional package removal")
    if confirm("Remove avahi-daemon? Only if nothing else on this device needs mDNS."):
        distro     = detect_distro()
        _mdns_pkgs = {
            "debian": ("avahi-daemon", "libnss-mdns"),
            "fedora": ("avahi", "nss-mdns"),
            "arch":   ("avahi", "nss-mdns"),
            "suse":   ("avahi", "nss-mdns"),
            "alpine": ("avahi",),
        }.get(distro, ("avahi-daemon",))
        pkg_remove(distro, *_mdns_pkgs)
        info("avahi-daemon removed.")

    print(f"\n{C.BOLD}{C.GREEN}Uninstall complete.{C.NC}\n")


# =============================================================================
# RULE MANAGEMENT (non-interactive subcommands)
# =============================================================================
def _load_cfg_for_rules():
    meta         = read_meta()
    install_dir  = Path(meta["install_dir"])
    ext_cfg_path = install_dir / ".pimiproxy-config.json"
    if not ext_cfg_path.exists():
        die("No pimiproxy-ultralite config found. Is it installed?")
    return install_dir, ext_cfg_path, json.loads(ext_cfg_path.read_text())


def _save_cfg_and_restart(install_dir, ext_cfg_path, cfg):
    ext_cfg_path.write_text(json.dumps(cfg, indent=2))
    cfg_path = install_dir / "config" / "config.yaml"
    cfg_path.write_text(build_config(cfg))
    run(["systemctl", "restart", "mihomo"], check=False)
    info("Mihomo restarted.")


def do_rule_list():
    _, _, cfg = _load_cfg_for_rules()
    rules     = cfg.get("rules", [])
    if not rules:
        print("  No user rules configured.")
        return
    print(f"\n  {C.BOLD}User rules ({len(rules)}):{C.NC}")
    for i, r in enumerate(rules, 1):
        print(f"  {i:>3}) {r}")
    print()


def do_rule_add():
    if len(sys.argv) < 3 or not sys.argv[2].strip():
        die('Usage: sudo python3 pimiproxy-ultralite.py rule-add "TYPE,value,target"')
    rule                             = sys.argv[2].strip()
    install_dir, ext_cfg_path, cfg   = _load_cfg_for_rules()
    cfg.setdefault("rules", []).append(rule)
    _save_cfg_and_restart(install_dir, ext_cfg_path, cfg)
    info(f"Added rule: {rule}")


def do_rule_remove():
    if len(sys.argv) < 3 or not sys.argv[2].strip().isdigit():
        die("Usage: sudo python3 pimiproxy-ultralite.py rule-remove <index>  (1-based)")
    index                            = int(sys.argv[2].strip()) - 1
    install_dir, ext_cfg_path, cfg   = _load_cfg_for_rules()
    rules                            = cfg.get("rules", [])
    if index < 0 or index >= len(rules):
        die(f"Index {index + 1} out of range (1–{len(rules)}).")
    removed      = rules.pop(index)
    cfg["rules"] = rules
    _save_cfg_and_restart(install_dir, ext_cfg_path, cfg)
    info(f"Removed rule: {removed}")


# =============================================================================
# REPO MANAGEMENT (standard-repos / china-repos)
# =============================================================================
def do_standard_repos():
    banner("Restore system repos", C.CYAN)
    distro = detect_distro()

    if distro == "debian":
        pairs = []
        sl = Path("/etc/apt/sources.list.bak")
        if sl.exists():
            pairs.append((sl, Path("/etc/apt/sources.list")))
        sld = Path("/etc/apt/sources.list.d")
        if sld.exists():
            for bak in sld.glob("*.bak"):
                pairs.append((bak, bak.with_suffix("")))
            for dis in sld.glob("*.disabled"):
                orig = Path(str(dis).replace(".disabled", ""))
                pairs.append((dis, orig))
        aliyun = Path("/etc/apt/sources.list.d/aliyun-system.sources")
        if not pairs and not aliyun.exists():
            info("No backups found — repos were not modified by pimiproxy.")
            return
        step("Restoring apt sources")
        if aliyun.exists():
            aliyun.unlink()
            info("Removed aliyun-system.sources")
        for bak, orig in pairs:
            bak.rename(orig)
            info(f"Restored {orig.name}")
        with Spinner("Updating package lists"):
            run(["apt-get", "update", "-qq"], check=False)
        info("System repos restored to original configuration.")

    elif distro == "fedora":
        step("Restoring dnf repos")
        restored = False
        for bak in Path("/etc/yum.repos.d").glob("*.bak"):
            bak.rename(bak.with_suffix(""))
            info(f"Restored {bak.with_suffix('').name}")
            restored = True
        if not restored:
            info("No backups found — repos were not modified by pimiproxy.")
            return
        info("System repos restored to original configuration.")

    elif distro == "arch":
        bak = Path("/etc/pacman.d/mirrorlist.bak")
        if not bak.exists():
            info("No backup found — mirrorlist was not modified by pimiproxy.")
            return
        step("Restoring pacman mirrorlist")
        bak.rename(Path("/etc/pacman.d/mirrorlist"))
        with Spinner("Syncing package database"):
            run(["pacman", "-Sy", "--noconfirm"], check=False)
        info("Mirrorlist restored to original configuration.")

    else:
        warn(f"standard-repos is not supported on {distro}.")


def do_china_repos():
    banner("Switch to Aliyun mirrors", C.CYAN)
    distro = detect_distro()

    if distro == "debian":
        step("Switching apt sources to Aliyun")
        distro_id = ""
        codename = ""
        if Path("/etc/os-release").exists():
            for line in Path("/etc/os-release").read_text().splitlines():
                if line.startswith("ID="):
                    distro_id = line.split("=", 1)[1].strip().strip('"')
                if line.startswith("VERSION_CODENAME="):
                    codename = line.split("=", 1)[1].strip().strip('"')
        if not codename:
            die("Could not determine VERSION_CODENAME.")
        sl = Path("/etc/apt/sources.list")
        sld = Path("/etc/apt/sources.list.d")
        if sl.exists() and not Path("/etc/apt/sources.list.bak").exists():
            shutil.copy2(sl, sl.with_suffix(".bak"))
        if sld.exists():
            for f in list(sld.glob("*.sources")) + list(sld.glob("*.list")):
                if "aliyun" in f.name:
                    continue
                bak = f.with_suffix(f.suffix + ".bak")
                if not bak.exists():
                    shutil.copy2(f, bak)
                f.rename(f.with_suffix(f.suffix + ".disabled"))
        if sl.exists():
            sl.rename(sl.with_suffix(".disabled"))
        aliyun = sld / "aliyun-system.sources"
        if distro_id == "ubuntu":
            aliyun.write_text(
                f"Types: deb\nURIs: https://mirrors.aliyun.com/ubuntu\n"
                f"Suites: {codename} {codename}-updates {codename}-security\n"
                f"Components: main restricted universe multiverse\n"
                f"Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n")
        else:
            aliyun.write_text(
                f"Types: deb\nURIs: https://mirrors.aliyun.com/debian\n"
                f"Suites: {codename} {codename}-updates\n"
                f"Components: main contrib non-free non-free-firmware\n"
                f"Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\n"
                f"Types: deb\nURIs: https://mirrors.aliyun.com/debian-security\n"
                f"Suites: {codename}-security\n"
                f"Components: main contrib non-free non-free-firmware\n"
                f"Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n")
        with Spinner("Updating package lists"):
            run(["apt-get", "update", "-qq"], check=False)
        info("System repos switched to Aliyun. Run 'standard-repos' to undo.")

    elif distro == "fedora":
        step("Switching dnf repos to Aliyun")
        for f in [Path("/etc/yum.repos.d/fedora.repo"),
                  Path("/etc/yum.repos.d/fedora-updates.repo")]:
            if not f.exists():
                continue
            bak = f.with_suffix(".bak")
            if not bak.exists():
                shutil.copy2(f, bak)
            txt = f.read_text()
            txt = re.sub(r'^metalink=', '#metalink=', txt, flags=re.MULTILINE)
            txt = re.sub(r'^#baseurl=http://download\.example/pub/fedora/linux',
                         'baseurl=https://mirrors.aliyun.com/fedora',
                         txt, flags=re.MULTILINE)
            f.write_text(txt)
        for name in ["fedora-cisco-openh264", "fedora-updates-testing",
                     "fedora-modular", "fedora-updates-modular"]:
            f = Path(f"/etc/yum.repos.d/{name}.repo")
            if f.exists():
                f.write_text(re.sub(r'^enabled=1', 'enabled=0',
                                    f.read_text(), flags=re.MULTILINE))
        info("System repos switched to Aliyun. Run 'standard-repos' to undo.")

    elif distro == "arch":
        step("Switching pacman mirrorlist to Aliyun")
        ml = Path("/etc/pacman.d/mirrorlist")
        bak = ml.with_suffix(".bak")
        if not bak.exists() and ml.exists():
            shutil.copy2(ml, bak)
        ml.write_text("Server = https://mirrors.aliyun.com/archlinux/$repo/os/$arch\n")
        with Spinner("Syncing package database"):
            run(["pacman", "-Sy", "--noconfirm"], check=False)
        info("Mirrorlist switched to Aliyun. Run 'standard-repos' to undo.")

    else:
        warn(f"china-repos is not supported on {distro}.")


# =============================================================================
# ENTRYPOINT
# =============================================================================
COMMANDS = {
    "install":      do_install,
    "uninstall":    do_uninstall,
    "configure":    do_configure,
    "refresh":      do_refresh,
    "refresh-cron": do_refresh_cron,
    "status":       do_status,
    "logs":         do_logs,
    "restart":      do_restart,
    "hostname":     do_set_hostname,
    "debug":        do_debug,
    "export":       do_export,
    "rule-list":    do_rule_list,
    "rule-add":     do_rule_add,
    "rule-remove":  do_rule_remove,
    "standard-repos": do_standard_repos,
    "china-repos":   do_china_repos,
}

_TAKES_ARG = {"hostname", "debug", "export", "rule-add", "rule-remove"}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cmd  = args[0] if args else "install"

    if cmd not in COMMANDS:
        simple = "|".join(k for k in COMMANDS if k not in _TAKES_ARG)
        print(f"Usage: sudo python3 pimiproxy-ultralite.py [{simple}]")
        print("       sudo python3 pimiproxy-ultralite.py logs")
        print("       sudo python3 pimiproxy-ultralite.py restart")
        print("       sudo python3 pimiproxy-ultralite.py hostname <new-hostname>")
        print("       sudo python3 pimiproxy-ultralite.py debug    [url]")
        print("       sudo python3 pimiproxy-ultralite.py export   [output-dir]")
        print('       sudo python3 pimiproxy-ultralite.py rule-add "TYPE,value,target"')
        print("       sudo python3 pimiproxy-ultralite.py rule-remove <index>")
        sys.exit(1)

    try:
        COMMANDS[cmd]()
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
