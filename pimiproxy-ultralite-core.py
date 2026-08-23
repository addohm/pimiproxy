#!/usr/bin/env python3
"""
pimiproxy-ultralite — install / uninstall / configure
Docker-free variant: installs mihomo as a native systemd service.
Supports Debian, Ubuntu, Fedora, Arch, openSUSE, Alpine, and derivatives.
Run as root: sudo ./pimiproxy-ultralite [install|uninstall|configure]
"""

import os
import re
import sys
import shutil
import subprocess
import json
import threading
import platform
from pathlib import Path

# =============================================================================
# REQUIRE ROOT
# =============================================================================
if os.geteuid() != 0:
    sys.exit(
        "Run as root:  sudo ./pimiproxy-ultralite [install|uninstall|configure]"
    )


# =============================================================================
# SHARED HELPERS (pimiproxy_common.py — installed alongside this script)
# =============================================================================
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pimiproxy_common import (
    C, SEP_LINE, info, step, warn, banner, die, yaml_quote,
    confirm, ask, ask_secret, choose, Spinner,
    run, run_ok, has_aes_hardware,
    _DEFAULT_HOSTNAMES, current_hostname, set_hostname, dhcp_renew,
    extract_yaml_blocks, fetch_subscription, save_subscription,
    detect_distro, pkg_update, pkg_install, pkg_remove,
    build_config as _build_config,
    SUBSCRIPTION_KEYS, config_path, config_lock, load_config, write_config,
    update_config, CONFIG_CANDIDATE, install_config, mihomo_test_cmd_native,
    wait_for_controller, engine_health, print_engine_health,
)


def _save_user_config(ext_cfg_path, cfg: dict) -> dict:
    """
    Persist an interactive session's config without clobbering concurrent
    writers. The session's copy may be minutes old, so a cron refresh that
    ran meanwhile must win for the subscription keys; everything else
    (user-editable settings) comes from the session. Returns the merged dict.
    """
    def _merge(fresh):
        fresh.update({k: v for k, v in cfg.items() if k not in SUBSCRIPTION_KEYS})
    return update_config(ext_cfg_path, _merge)


MIHOMO_BIN = Path("/usr/local/bin/mihomo")


def _engine_test_cmd(install_dir, binary=None) -> list:
    return mihomo_test_cmd_native(binary or MIHOMO_BIN, Path(install_dir) / "config",
                                  CONFIG_CANDIDATE)


def _write_engine_config(cfg: dict, install_dir, binary=None) -> bool:
    """
    Validate cfg with `mihomo -t` and, only if it passes, install it as
    config.yaml. On rejection the reason is printed and the previous
    config.yaml stays live. Returns True on success.
    """
    with Spinner("Validating config with mihomo"):
        ok, why = install_config(Path(install_dir) / "config", build_config(cfg),
                                 _engine_test_cmd(install_dir, binary))
    if not ok:
        warn("Mihomo rejected the config — the previous config.yaml is still live.")
        warn(f"Reason: {why}")
        return False
    info("config.yaml validated and written.")
    return True


def _engine_health(meta: dict, cfg: dict = None) -> dict:
    """engine_health() for the ultralite variant: process state from systemd."""
    r = run(["systemctl", "is-active", "mihomo"], capture=True, check=False)
    state = r.stdout.strip() or "unknown"
    src = cfg or meta
    return engine_health(state, state == "active",
                         src.get("api_port", meta.get("api_port", "9090")),
                         src.get("api_secret", meta.get("api_secret", "")),
                         src.get("mixed_port", meta.get("mixed_port", "7890")))


def _restart_engine(cfg: dict, meta: dict) -> bool:
    """Restart mihomo.service and wait for its controller to answer."""
    r = run(["systemctl", "restart", "mihomo"], check=False, silent=True)
    if r.returncode != 0:
        warn("`systemctl restart mihomo` failed. Try:  sudo pimiproxy-ultralite logs")
        return False
    api_port = cfg.get("api_port") or meta.get("api_port", "9090")
    secret = cfg.get("api_secret") or meta.get("api_secret", "")
    with Spinner("Waiting for Mihomo to come up"):
        up, detail = wait_for_controller("127.0.0.1", api_port, secret, timeout=25)
    if up:
        info(f"Mihomo restarted ({detail}).")
        return True
    warn(f"Mihomo restarted but its controller is not answering: {detail}")
    warn("Check:  sudo pimiproxy-ultralite logs")
    return False


def do_set_hostname():
    if len(sys.argv) < 3 or not sys.argv[2].strip():
        die("Usage: sudo ./pimiproxy-ultralite hostname <new-hostname>")
    name = sys.argv[2].strip()
    if len(name) > 24:
        die(f"Hostname too long ({len(name)} chars). Max 24 characters.")
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$', name):
        die("Hostname must contain only letters, digits, and hyphens, "
            "and cannot start or end with a hyphen.")
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
# CONFIG GENERATION
# The generator lives in pimiproxy_common.py (shared with the full variant
# and the API container). Only the header comment differs per variant.
# =============================================================================
def build_config(cfg: dict) -> str:
    return _build_config(cfg, variant="ultralite")


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
    # After Mihomo brings the TUN up, systemd-resolved (if present) auto-adds
    # it as a DNS scope — which breaks getaddrinfo when clash-system is off
    # (queries get fake-IPs instead of going to the real resolver). Strip it.
    L("ExecStartPost=/bin/sh -c 'command -v resolvectl >/dev/null 2>&1 && "
      "systemctl is-active systemd-resolved >/dev/null 2>&1 && "
      "for i in $(seq 1 10); do [ -d /sys/class/net/mihomo ] && break; sleep 1; done && "
      "resolvectl dns mihomo \"\" && "
      "resolvectl default-route mihomo false && "
      "resolvectl flush-caches || true'")
    L("Restart=always")
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
# CLASH-SYSTEM (advanced: toggle host-traffic bypass)
# =============================================================================
# Default behaviour: all host traffic flows through Mihomo via TUN
# (auto-route installs the ip rules that catch it).
#
# `clash-system off` marks host OUTPUT with fwmark 888 and adds an ip rule at
# priority 5000 that routes marked packets to the main table — bypassing TUN.
# Mihomo's own outbound traffic uses mark 666 (set via routing-mark in the
# config), so it's never confused with our bypass mark. Gateway clients
# (e.g. Meta Quest using the Pi as their DHCP gateway) are always proxied
# because their traffic hits FORWARD, not OUTPUT — we don't mangle FORWARD.
#
# Intentionally NOT auto-enabled on boot. Default behaviour is unchanged from
# before this feature returned — advanced users invoke it explicitly.

CLASH_SYSTEM_PATH = Path("/usr/local/bin/clash-system")


def build_clash_system() -> str:
    return r"""#!/usr/bin/env bash
# clash-system — toggle host-traffic bypass
# Gateway clients (forwarded via this device) are always proxied.
set -euo pipefail

[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

MARK=888
RULE_PRIO=5000

G=$'\033[0;32m'; Y=$'\033[1;33m'; R=$'\033[0;31m'; D=$'\033[2m'; B=$'\033[1m'; N=$'\033[0m'

MARK_HEX=$(printf '0x%x' "$MARK")

iface_up()        { [[ -d /sys/class/net/mihomo ]]; }
mark_is_set()     { iptables -t mangle -C OUTPUT -j MARK --set-mark $MARK 2>/dev/null; }
rule_is_set()     { ip rule show | grep -qE "^${RULE_PRIO}:.*fwmark ${MARK_HEX}.*lookup main"; }
snat_is_set()     { iptables -t nat -C POSTROUTING -m mark --mark $MARK -j MASQUERADE 2>/dev/null; }
ensure_rule()     { rule_is_set || ip rule add fwmark "$MARK" lookup main priority "$RULE_PRIO"; }
remove_rule()     { while rule_is_set; do ip rule del fwmark "$MARK" lookup main priority "$RULE_PRIO" 2>/dev/null || break; done; }
set_mark()        { mark_is_set || iptables -t mangle -A OUTPUT -j MARK --set-mark $MARK; }
unset_mark()      { while mark_is_set; do iptables -t mangle -D OUTPUT -j MARK --set-mark $MARK 2>/dev/null || break; done; }
# MASQUERADE is required: after re-route, the packet inherits the TUN's source
# IP (198.18.0.1). That source isn't routable outside the Pi, so we rewrite
# it to the outgoing interface's address on POSTROUTING.
set_snat()        { snat_is_set || iptables -t nat -A POSTROUTING -m mark --mark $MARK -j MASQUERADE; }
unset_snat()      { while snat_is_set; do iptables -t nat -D POSTROUTING -m mark --mark $MARK -j MASQUERADE 2>/dev/null || break; done; }

# On systems running systemd-resolved, the mihomo TUN gets auto-added as a
# DNS scope when it comes up. That makes getaddrinfo return fake-IPs via
# resolved even when our bypass is active. Strip it. Idempotent; safe to
# call on systems without resolvectl.
fix_resolved() {
    command -v resolvectl >/dev/null 2>&1 || return 0
    systemctl is-active systemd-resolved >/dev/null 2>&1 || return 0
    iface_up || return 0
    resolvectl dns mihomo "" 2>/dev/null || true
    resolvectl default-route mihomo false 2>/dev/null || true
    resolvectl flush-caches 2>/dev/null || true
}

case "${1:-}" in
    on)
        iface_up || { echo "${R}✗${N}  mihomo TUN interface not found — is the service running?" >&2; exit 1; }
        unset_mark
        unset_snat
        remove_rule
        fix_resolved
        echo "${G}✓${N}  clash-system: ${B}on${N}  ${D}(host traffic routes through proxy — default)${N}"
    ;;
    off)
        iface_up || { echo "${R}✗${N}  mihomo TUN interface not found — is the service running?" >&2; exit 1; }
        ensure_rule
        set_mark
        set_snat
        fix_resolved
        echo "${Y}○${N}  clash-system: ${B}off${N}  ${D}(host traffic goes direct; gateway clients still proxied)${N}"
    ;;
    status)
        if mark_is_set; then
            echo "${Y}○${N}  clash-system: ${B}off${N}  ${D}(host traffic direct)${N}"
        else
            echo "${G}✓${N}  clash-system: ${B}on${N}  ${D}(host traffic via proxy)${N}"
        fi
        iface_up && echo "   TUN interface: ${G}up${N}" || echo "   TUN interface: ${R}down${N}"
    ;;
    *)
        cat <<EOF
${B}clash-system${N} — toggle host-traffic bypass for pimiproxy

${B}Usage:${N}  sudo clash-system [on|off|status]

  ${B}on${N}       Host traffic routes through Mihomo ${D}(default){N}
  ${B}off${N}      Host traffic bypasses Mihomo (goes direct)
  ${B}status${N}   Show current state

${D}Gateway clients (devices using this host as their DHCP gateway, e.g.
Meta Quest) are always proxied regardless of this setting.${N}

${D}State is not persisted across reboots; reboot resets to on.${N}
EOF
        exit 1
    ;;
esac
"""


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
    # Prevent double-display: on Debian-based distros, login bash reads both
    # /etc/profile (sourcing profile.d) AND /etc/bash.bashrc (which we also
    # source from, for non-login terminals). Without this guard, the banner
    # prints twice on SSH login.
    L("[[ -n \"$_PIMIPROXY_BANNER_SHOWN\" ]] && return")
    L("_PIMIPROXY_BANNER_SHOWN=1")
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
    L("# ── Proxy engine ─────────────────────────────────────────────────────")
    L("# Liveness = the engine's controller answers on its port. No secret is")
    L("# needed: a 401 still proves the engine is up. The TUN device alone is")
    L("# not proof — it can outlive the process or belong to another mihomo.")
    L(f"_CTL=$(curl -s -o /dev/null -m 2 -w '%{{http_code}}' http://127.0.0.1:{api_port}/version 2>/dev/null || echo 000)")
    L('if [[ "$_CTL" == "200" || "$_CTL" == "401" ]]; then')
    L("  printf '  Proxy engine    %s\\n' \"${_G}●  running${_N}\"")
    L("elif systemctl is-active --quiet mihomo 2>/dev/null; then")
    L("  printf '  Proxy engine    %s\\n' \"${_R}●  service active but not answering  sudo pimiproxy-ultralite logs${_N}\"")
    L("elif [[ -d /sys/class/net/mihomo ]]; then")
    L("  printf '  Proxy engine    %s\\n' \"${_R}●  not answering (TUN exists — another mihomo?)${_N}\"")
    L("else")
    L("  printf '  Proxy engine    %s\\n' \"${_R}●  stopped${_N}\"")
    L("fi")
    L()
    L("printf '  TUN mode        %s\\n' \"${_G}●  always on${_N}\"")
    L()
    L("# ── Host traffic (clash-system state) ────────────────────────────────")
    L("if [[ -d /sys/class/net/mihomo ]] && ip rule show 2>/dev/null | "
      "grep -qE '^5000:.*fwmark 0x378.*lookup main'; then")
    L("  printf '  Host traffic    %s\\n' \"${_Y}○  direct (clash-system off)${_N}\"")
    L("else")
    L("  printf '  Host traffic    %s\\n' \"${_G}●  via proxy${_N}\"")
    L("fi")
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
    L('printf "  %s\\n" "${_D}pimiproxy-ultralite [status|logs|configure|debug]${_N}"')
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
    bin_dir    = script_dir / "bin"

    # Direct matches (already decompressed and named correctly)
    candidates = [
        bin_dir / f"mihomo-{arch}",
        bin_dir / "mihomo",
        Path("/opt/pimiproxy-lite/bin") / f"mihomo-{arch}",
        Path("/usr/local/bin/mihomo"),
    ]
    for p in candidates:
        if p.exists():
            return p

    # Map host arch to GitHub release naming conventions
    _gz_arch_map = {
        "x86_64":  ["amd64", "x86_64"],
        "i686":    ["386", "i686", "i386"],
        "i386":    ["386", "i386", "i686"],
        "aarch64": ["arm64", "aarch64"],
        "armv7l":  ["armv7", "armv7l"],
    }
    patterns = _gz_arch_map.get(arch, [arch])

    # Look for compressed .gz files in bin/ and auto-extract
    if bin_dir.exists():
        for gz in sorted(bin_dir.glob("*.gz")):
            name_lower = gz.name.lower()
            if not any(p in name_lower for p in patterns):
                continue
            # Found a matching .gz — decompress it
            import gzip as _gzip
            target = bin_dir / f"mihomo-{arch}"
            info(f"Decompressing {gz.name} → mihomo-{arch}")
            with _gzip.open(gz, "rb") as f_in:
                target.write_bytes(f_in.read())
            target.chmod(0o755)
            return target

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

    # ── engine ────────────────────────────────────────────────────────────────
    step("Engine")
    health = _engine_health(meta)
    print_engine_health(health, "service", "sudo pimiproxy-ultralite logs")

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
        warn("  sudo ./pimiproxy-ultralite logs")

    # ── hardware ──────────────────────────────────────────────────────────────
    step("Hardware")
    if has_aes_hardware():
        info("AES hardware acceleration: available.")
    else:
        warn("AES hardware acceleration: not available — software encryption, higher latency.")

    # ── endpoints ─────────────────────────────────────────────────────────────
    print(f"\n  {C.DIM}{SEP_LINE}{C.NC}")
    print(f"  {C.CYAN}API{C.NC}    →  http://{hostname}.lan:{api_port}")
    print(f"  {C.BOLD}{C.CYAN}Proxy{C.NC}  →  {C.BOLD}{hostname}.lan:{mixed_port}  (HTTP/SOCKS5){C.NC}")
    print(f"  {C.DIM}{SEP_LINE}{C.NC}")
    print()

    if not health["healthy"]:
        sys.exit(1)


# =============================================================================
# TEST (proxy group connectivity)
# =============================================================================
_TEST_GROUPS = [
    ("DIRECT",       "China — routed direct",    [
        "http://www.baidu.com",
        "https://www.bilibili.com",
    ]),
    ("General",      "general international",     [
        "https://www.google.com",
        "https://www.twitter.com",
    ]),
    ("AI",           "OpenAI, Anthropic, Copilot", [
        "https://www.openai.com",
        "https://claude.ai",
    ]),
    ("Google/Youtube", None,                       [
        "https://www.youtube.com",
        "https://mail.google.com",
    ]),
    ("Netflix",      None,                         [
        "https://www.netflix.com",
    ]),
    ("Intl Media",   "international media",        [
        "https://www.bbc.co.uk",
        "https://www.reddit.com",
    ]),
    ("Apple",        None,                         [
        "https://www.apple.com",
    ]),
    ("Storage",      "online storage",             [
        "https://www.dropbox.com",
        "https://drive.google.com",
    ]),
]


def do_test():
    import time
    import urllib.request

    meta = read_meta()
    mixed_port = meta.get("mixed_port", "7890")
    proxy_addr = f"http://127.0.0.1:{mixed_port}"

    single_url = None
    for i, arg in enumerate(sys.argv[2:], start=2):
        if arg == "--url" and i + 1 < len(sys.argv):
            single_url = sys.argv[i + 1]
            break
        if not arg.startswith("--") and arg.startswith("http"):
            single_url = arg
            break

    banner("pimiproxy proxy test", C.CYAN)
    print(f"  Proxy: {C.BOLD}127.0.0.1:{mixed_port}{C.NC}")
    print()

    handler = urllib.request.ProxyHandler({"http": proxy_addr, "https": proxy_addr})
    opener = urllib.request.build_opener(handler)
    try:
        with opener.open("http://www.gstatic.com/generate_204", timeout=5) as r:
            if r.status != 204:
                raise Exception(f"Expected 204, got {r.status}")
        info("Proxy responding.")
    except Exception as e:
        die(f"Proxy not responding: {e}")

    print()

    passed = 0
    failed = 0

    def _test(group: str, url: str):
        nonlocal passed, failed
        t0 = time.monotonic()
        try:
            with opener.open(url, timeout=10) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            code = 0
        ms = (time.monotonic() - t0) * 1000

        if code == 0:
            failed += 1
            print(f"  {C.RED}✗{C.NC}  {group:<20}  {url:<30}  {C.RED}TIMEOUT{C.NC}")
        elif code == 403:
            passed += 1
            print(f"  {C.YELLOW}~{C.NC}  {group:<20}  {url:<30}  "
                  f"{C.YELLOW}{code}{C.NC} {C.DIM}{ms:.0f}ms (reached server){C.NC}")
        elif 200 <= code < 400:
            passed += 1
            print(f"  {C.GREEN}✓{C.NC}  {group:<20}  {url:<30}  "
                  f"{C.GREEN}{code}{C.NC} {C.DIM}{ms:.0f}ms{C.NC}")
        else:
            failed += 1
            print(f"  {C.RED}✗{C.NC}  {group:<20}  {url:<30}  "
                  f"{C.RED}{code}{C.NC} {C.DIM}{ms:.0f}ms{C.NC}")

    SEP = f"  {C.DIM}{'─' * 58}{C.NC}"

    if single_url:
        import urllib.error
        import threading
        from urllib.parse import urlparse

        try:
            parsed_url = urlparse(single_url if "://" in single_url else f"http://{single_url}")
            domain = (parsed_url.hostname or single_url).lower()
        except Exception:
            domain = single_url.lower()

        api_port = meta.get("api_port", "9090")
        api_secret = meta.get("api_secret", "")

        print(f"  {C.BOLD}Single URL test{C.NC}")
        print(SEP)
        print(f"  Domain: {C.BOLD}{domain}{C.NC}")
        print()

        probe_result = {}

        def _probe():
            t0 = time.monotonic()
            try:
                req = urllib.request.Request(single_url)
                with opener.open(req, timeout=10) as r:
                    probe_result["code"] = r.status
            except urllib.error.HTTPError as e:
                probe_result["code"] = e.code
            except Exception as e:
                probe_result["code"] = 0
                probe_result["error"] = str(e)[:120]
            probe_result["ms"] = (time.monotonic() - t0) * 1000

        t = threading.Thread(target=_probe, daemon=True)
        t.start()

        mihomo_conn = {}
        deadline = time.time() + 4.0
        while time.time() < deadline:
            time.sleep(0.1)
            try:
                mreq = urllib.request.Request(
                    f"http://127.0.0.1:{api_port}/connections",
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

        code = probe_result.get("code", 0)
        ms = probe_result.get("ms", 0)
        if code == 0:
            failed += 1
            print(f"  {C.RED}✗{C.NC}  Connection: {C.RED}TIMEOUT{C.NC}"
                  f"  {C.DIM}{probe_result.get('error', '')}{C.NC}")
        elif code == 403:
            passed += 1
            print(f"  {C.YELLOW}~{C.NC}  Connection: {C.YELLOW}{code}{C.NC}"
                  f"  {C.DIM}{ms:.0f}ms (reached server, blocked without session){C.NC}")
        elif 200 <= code < 400:
            passed += 1
            print(f"  {C.GREEN}✓{C.NC}  Connection: {C.GREEN}{code}{C.NC}"
                  f"  {C.DIM}{ms:.0f}ms{C.NC}")
        else:
            failed += 1
            print(f"  {C.RED}✗{C.NC}  Connection: {C.RED}{code}{C.NC}"
                  f"  {C.DIM}{ms:.0f}ms{C.NC}")

        print()
        if mihomo_conn.get("rule"):
            rule_type = mihomo_conn["rule"]
            payload = mihomo_conn["payload"]
            chains = mihomo_conn["chains"]
            target = chains[0] if chains else ""
            rule_str = f"{rule_type},{payload},{target}" if payload else f"{rule_type},{target}"
            print(f"  {C.CYAN}Rule match{C.NC}  {C.BOLD}{rule_str}{C.NC}")
            print(f"  {C.CYAN}Type{C.NC}        {rule_type}")
            if payload:
                print(f"  {C.CYAN}Value{C.NC}       {payload}")
            print(f"  {C.CYAN}Target{C.NC}      {target}")
            if len(chains) > 1:
                print(f"  {C.CYAN}Chain{C.NC}       {' → '.join(chains)}")
            print(f"  {C.CYAN}Source{C.NC}      {C.DIM}mihomo (live connection){C.NC}")
        else:
            install_dir = Path(meta["install_dir"])
            ext_cfg_path = install_dir / ".pimiproxy-config.json"
            matched_rule = source = None
            if ext_cfg_path.exists():
                cfg = json.loads(ext_cfg_path.read_text())
                def _parse_rule(r):
                    parts = r.split(",", 2)
                    if len(parts) == 3:
                        return {"rule_type": parts[0], "value": parts[1], "target": parts[2]}
                    if len(parts) == 2:
                        return {"rule_type": parts[0], "value": "", "target": parts[1]}
                    return {"rule_type": r, "value": "", "target": ""}

                def _rule_matches(rule, dom):
                    rt = rule["rule_type"].upper()
                    val = rule.get("value", "").lower()
                    if rt == "DOMAIN":
                        return dom == val
                    if rt == "DOMAIN-SUFFIX":
                        return dom == val or dom.endswith("." + val)
                    if rt == "DOMAIN-KEYWORD":
                        return bool(val) and val in dom
                    if rt == "MATCH":
                        return True
                    return False

                for r in cfg.get("rules", []):
                    parsed_r = _parse_rule(r)
                    if _rule_matches(parsed_r, domain):
                        matched_rule, source = parsed_r, "user"
                        break
                if not matched_rule:
                    for line in cfg.get("subscription_rules_yaml", "").splitlines():
                        line = line.strip().lstrip("- ").strip("'\"")
                        if line and line != "rules:":
                            parsed_r = _parse_rule(line)
                            if _rule_matches(parsed_r, domain):
                                matched_rule, source = parsed_r, "provider"
                                break

            if matched_rule:
                rule_str = (f"{matched_rule['rule_type']},{matched_rule['value']},{matched_rule['target']}"
                            if matched_rule["value"]
                            else f"{matched_rule['rule_type']},{matched_rule['target']}")
                print(f"  {C.CYAN}Rule match{C.NC}  {C.BOLD}{rule_str}{C.NC}")
                print(f"  {C.CYAN}Type{C.NC}        {matched_rule['rule_type']}")
                if matched_rule["value"]:
                    print(f"  {C.CYAN}Value{C.NC}       {matched_rule['value']}")
                print(f"  {C.CYAN}Target{C.NC}      {matched_rule['target']}")
                print(f"  {C.CYAN}Source{C.NC}      {C.DIM}{source} (static rule match){C.NC}")
            else:
                warn("Could not determine matching rule.")
                warn("GEOIP/GEOSITE rules require Mihomo's API to report the match.")
        print()
    else:
        for group_name, desc, urls in _TEST_GROUPS:
            label = f"{C.BOLD}{group_name}{C.NC}"
            if desc:
                label += f" {C.DIM}({desc}){C.NC}"
            print(f"  {label}")
            print(SEP)
            for url in urls:
                _test(group_name, url)
            print()

    total = passed + failed
    print(SEP)
    if failed == 0:
        print(f"  {C.GREEN}{C.BOLD}All {total} tests passed.{C.NC}")
    else:
        print(f"  {C.BOLD}{passed}/{total} passed{C.NC}, {C.RED}{failed} failed{C.NC}")
        print(f"  {C.DIM}Failures are typically caused by provider node issues, not pimiproxy.{C.NC}")
    print(SEP)
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
# SYSTEM — host-traffic bypass toggle (dispatches to /usr/local/bin/clash-system)
# =============================================================================
def do_system():
    action = sys.argv[2].lower() if len(sys.argv) > 2 else "status"
    if action not in {"on", "off", "status"}:
        die(f"Unknown action '{action}'. Use: on, off, status")
    script = Path("/usr/local/bin/clash-system")
    if not script.exists():
        die("clash-system not installed — run `sudo pimiproxy-ultralite install` to add it")
    try:
        subprocess.run([str(script), action], check=False)
    except KeyboardInterrupt:
        print()


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
        print(f"\n{C.CYAN}{C.BOLD}  Configure pimiproxy-ultralite{C.NC}")
        print(f"  {C.DIM}{SEP_LINE}{C.NC}")
        print(f"   {C.CYAN}1){C.NC} Manage provider URL")
        print(f"   {C.CYAN}2){C.NC} Manage proxy groups")
        print(f"   {C.CYAN}3){C.NC} Manage rules")
        print(f"   {C.CYAN}4){C.NC} Show current config")
        print(f"   {C.CYAN}5){C.NC} Refresh provider now")
        print(f"   {C.CYAN}6){C.NC} Change API secret")
        print(f"   {C.CYAN}7){C.NC} Change ports")
        print(f"   {C.CYAN}8){C.NC} Save and apply")
        print(f"   {C.CYAN}9){C.NC} Exit without saving")
        print(f"  {C.DIM}{SEP_LINE}{C.NC}")
        choice = input(f"  {C.BOLD}Selection{C.NC} {C.DIM}[1-9]{C.NC}: ").strip()

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
                if save_subscription(install_dir, url, variant="ultralite",
                                     test_cmd=_engine_test_cmd(install_dir)):
                    cfg = load_config(ext_cfg_path)
                    _restart_engine(cfg, meta)

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
            # Validate the merged result (a cron refresh may have run during
            # this session) before persisting anything. On rejection nothing
            # on disk changes and the menu stays open so the edit can be fixed.
            def _merged(fresh):
                fresh.update({k: v for k, v in cfg.items() if k not in SUBSCRIPTION_KEYS})
            with config_lock(ext_cfg_path):
                candidate = load_config(ext_cfg_path)
                _merged(candidate)
                if not _write_engine_config(candidate, install_dir):
                    continue
                write_config(ext_cfg_path, candidate)
            cfg = candidate
            for key in ("api_port", "mixed_port", "api_secret"):
                if key in cfg:
                    meta[key] = cfg[key]
            write_meta(meta)
            if BANNER_PATH.exists():
                BANNER_PATH.write_text(build_banner(cfg, meta))
                info("Login banner updated.")
            if confirm("Restart Mihomo now to apply changes?", default=True):
                _restart_engine(cfg, meta)
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

    def _ask_hostname(default):
        while True:
            val = ask("Hostname", default)
            if len(val) > 24:
                warn(f"Hostname too long ({len(val)} chars). Max 24 characters.")
                continue
            if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$', val):
                warn("Hostname must contain only letters, digits, and hyphens,")
                warn("and cannot start or end with a hyphen.")
                continue
            return val

    boot_bypass = False  # default: host traffic through proxy on boot

    if express:
        hostname_new = _ask_hostname(hostname_new)
    else:
        install_dir  = ask("Install directory", install_dir)
        hostname_new = _ask_hostname(hostname_new)
        api_port     = ask("Mihomo API port",   api_port)
        mixed_port   = ask("Mixed proxy port",  mixed_port)
        api_secret   = ask_secret("API secret", api_secret)
        print()
        print("  ── Host-traffic default " + "─" * 17)
        print("  Advanced: should this device's OWN traffic route through the proxy")
        print("  on boot, or bypass it (direct internet)?  Gateway clients (e.g. Meta")
        print("  Quest using this device as their gateway) are always proxied either way.")
        print("  You can toggle at runtime with:  sudo clash-system on|off")
        print("  " + "─" * 42)
        boot_bypass = confirm("Bypass proxy for this device's traffic on boot?",
                              default=False)

    print()
    print("  ── Provider URL " + "─" * 26)
    print("  If your proxy provider gave you a Clash/Mihomo API URL, enter it here.")
    print("  It typically looks like:")
    print("    https://provider.example.com/api?token=abc123")
    print()
    print("  Leave blank to skip — add later via:")
    print("    sudo ./pimiproxy-ultralite configure")
    print("  " + "─" * 42)
    print()
    while True:
        subscription_url = input("  Provider URL [leave blank to skip]: ").strip()
        if not subscription_url:
            break
        if not subscription_url.startswith(("http://", "https://")):
            warn("URL must start with http:// or https://")
            continue
        print()
        _test = fetch_subscription(subscription_url)
        if _test:
            info("Provider URL validated — proxies found.")
            break
        else:
            warn("Could not fetch a valid Clash config from that URL.")
            if not confirm("Use it anyway?", default=False):
                subscription_url = ""
                continue
            break

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
    # On SELinux systems (Fedora/RHEL), copied files inherit the source context
    # (often user_tmp_t) which prevents systemd from executing them. Restore
    # the standard bin_t context so the mihomo service can start.
    if shutil.which("restorecon"):
        run(["restorecon", str(dest_bin)], silent=True, check=False)
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
            warn("Run after install:  sudo ./pimiproxy-ultralite refresh")

    # Validate with the just-installed binary. If the provider's payload is
    # rejected, install without it rather than leave a config Mihomo cannot
    # start — the URL is kept so `pimiproxy-ultralite refresh` can retry.
    if not _write_engine_config(cfg, install_path):
        if cfg.get("subscription_proxies_yaml"):
            warn("Installing without the provider's proxies. After install, check the URL with:")
            warn("  sudo pimiproxy-ultralite debug   then   sudo pimiproxy-ultralite refresh")
            for k in SUBSCRIPTION_KEYS:
                cfg[k] = ""
            if not _write_engine_config(cfg, install_path):
                die("Even the minimal config failed validation — see the reason above.")
        else:
            die("Config failed validation — see the reason above.")
    write_config(config_path(install_path), cfg)

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

    # Allow gateway clients (Meta Quest etc.) to reach Mihomo's TUN.
    # Distros with restrictive default FORWARD policy (or Docker installed,
    # which sets policy DROP) would otherwise drop forwarded packets.
    if shutil.which("iptables"):
        with Spinner("Configuring FORWARD for gateway clients"):
            # Clear any previous rule before re-adding (idempotent across reinstalls)
            run(["iptables", "-D", "FORWARD", "-o", "mihomo", "-j", "ACCEPT"],
                check=False, silent=True)
            run(["iptables", "-D", "FORWARD", "-i", "mihomo", "-j", "ACCEPT"],
                check=False, silent=True)
            run(["iptables", "-I", "FORWARD", "-o", "mihomo", "-j", "ACCEPT"],
                check=False, silent=True)
            run(["iptables", "-I", "FORWARD", "-i", "mihomo", "-j", "ACCEPT"],
                check=False, silent=True)
        info("FORWARD rules installed (gateway clients → TUN).")

    # ── refresh script + wrapper + cron ───────────────────────────────────────
    step("Wrapper + refresh script")
    # Keep the "-core" suffix so the wrapper script can find it
    script_dest = install_path / "pimiproxy-ultralite-core.py"
    shutil.copy2(__file__, script_dest)
    info(f"Core script copied to {script_dest}.")

    # Shared helper module — core.py imports this at startup, so it must
    # sit next to the installed core script.
    _common_src = Path(__file__).resolve().parent / "pimiproxy_common.py"
    if _common_src.exists():
        common_dest = install_path / "pimiproxy_common.py"
        shutil.copy2(_common_src, common_dest)
        info(f"Shared helpers copied to {common_dest}.")

    # Copy the wrapper script if available, and symlink for global access
    _wrapper_src = Path(__file__).resolve().parent / "pimiproxy-ultralite"
    wrapper_dest = install_path / "pimiproxy-ultralite"
    if _wrapper_src.exists():
        shutil.copy2(_wrapper_src, wrapper_dest)
        wrapper_dest.chmod(0o755)
        info(f"Wrapper copied to {wrapper_dest}.")

        _symlink = Path("/usr/local/bin/pimiproxy-ultralite")
        if _symlink.is_symlink() or _symlink.exists():
            _symlink.unlink()
        _symlink.symlink_to(wrapper_dest)
        info(f"Symlink: {_symlink} → {wrapper_dest}")

    cron_path = Path("/etc/cron.daily/pimiproxy-refresh")
    if _wrapper_src.exists():
        cron_path.write_text(
            "#!/usr/bin/env bash\n"
            "# pimiproxy-ultralite subscription auto-refresh\n"
            f'exec "{wrapper_dest}" refresh-cron\n'
        )
    else:
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

    # ── clash-system (advanced: toggle host-traffic bypass) ──────────────────
    step("clash-system utility")
    CLASH_SYSTEM_PATH.write_text(build_clash_system())
    CLASH_SYSTEM_PATH.chmod(0o755)
    info(f"Installed {CLASH_SYSTEM_PATH}  (advanced: sudo clash-system [on|off|status])")

    if boot_bypass:
        # Install a systemd unit that runs `clash-system off` once the TUN is up.
        # This persists the "direct" choice across reboots.
        BYPASS_SERVICE_PATH.write_text(
            "[Unit]\n"
            "Description=pimiproxy — apply clash-system bypass after mihomo is up\n"
            "After=mihomo.service\n"
            "Requires=mihomo.service\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            "ExecStartPre=/bin/sh -c 'for i in 1 2 3 4 5; do [ -d /sys/class/net/mihomo ] && exit 0; sleep 1; done; exit 1'\n"
            f"ExecStart={CLASH_SYSTEM_PATH} off\n"
            f"ExecStop={CLASH_SYSTEM_PATH} on\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        run(["systemctl", "daemon-reload"], silent=True, check=False)
        run(["systemctl", "enable", "pimiproxy-bypass.service"], silent=True, check=False)
        run(["systemctl", "start",  "pimiproxy-bypass.service"], silent=True, check=False)
        info("pimiproxy-bypass.service enabled: host traffic bypasses proxy on boot.")
    else:
        # Make sure any previously-installed unit is gone
        if BYPASS_SERVICE_PATH.exists():
            run(["systemctl", "disable", "--now", "pimiproxy-bypass.service"],
                silent=True, check=False)
            BYPASS_SERVICE_PATH.unlink()
            run(["systemctl", "daemon-reload"], silent=True, check=False)

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

    _SEP = f"  {C.DIM}{SEP_LINE}{C.NC}"

    print()
    print(f"{C.BOLD}{C.GREEN}  pimiproxy-ultralite — installation complete{C.NC}")
    print(_SEP)
    print(f"  {C.YELLOW}Tip:{C.NC} Some apps (Unity VR games etc.) bypass the system proxy.")
    print(f"  Route all device traffic through this device instead:")
    print(f"  Set the device WiFi to {C.BOLD}static{C.NC} with these values:")
    print(f"    IP address  →  pick any unused IP on your subnet")
    print(f"    Subnet      →  /{_cidr}")
    print(f"    Gateway     →  {lan_ip}  {C.DIM}(this device){C.NC}")
    print(f"    DNS 1       →  {lan_ip}  {C.DIM}(this device){C.NC}")
    print(f"    DNS 2       →  {_gw or '<router-ip>'}  {C.DIM}(your router, fallback){C.NC}")
    print(_SEP)
    print(f"  {C.CYAN}API{C.NC}    →  http://{hostname_new}.lan:{api_port}")
    print(f"         →  http://{lan_ip}:{api_port}")
    print(f"  {C.BOLD}{C.CYAN}Proxy{C.NC}  →  {C.BOLD}{hostname_new}.lan:{mixed_port}  (HTTP/SOCKS5){C.NC}")
    print(f"  {C.YELLOW}Login{C.NC}  →  API secret: {C.BOLD}{api_secret}{C.NC}")
    print(_SEP)
    print(f"  {C.DIM}pimiproxy-ultralite [status|test|logs|configure|debug]{C.NC}")
    print(_SEP)
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
    # save_subscription validates the merged config with mihomo -t and
    # installs config.yaml before it persists the JSON, so a bad provider
    # payload leaves the previous working config live.
    if not save_subscription(install_dir, url, variant="ultralite",
                             test_cmd=_engine_test_cmd(install_dir)):
        die("Provider refresh failed.")

    cfg = load_config(ext_cfg_path)
    if not _restart_engine(cfg, meta):
        sys.exit(1)

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

    # Remove the /usr/local/bin/pimiproxy-ultralite symlink
    _symlink = Path("/usr/local/bin/pimiproxy-ultralite")
    if _symlink.is_symlink() or _symlink.exists():
        _symlink.unlink()
        info(f"Removed {_symlink}")

    # Remove clash-system script and any active bypass rules
    cs = Path("/usr/local/bin/clash-system")
    if cs.exists():
        # Flip it back to 'on' first so any active bypass mark/rule gets cleared
        run([str(cs), "on"], check=False, silent=True)
        cs.unlink()
        info("Removed /usr/local/bin/clash-system")
    # Belt-and-suspenders: clear any leftover bypass ip rule or iptables mark
    if shutil.which("ip"):
        run(["ip", "rule", "del", "fwmark", "888", "lookup", "main",
             "priority", "5000"], check=False, silent=True)
    if shutil.which("iptables"):
        run(["iptables", "-t", "mangle", "-D", "OUTPUT",
             "-j", "MARK", "--set-mark", "888"], check=False, silent=True)
        run(["iptables", "-t", "nat", "-D", "POSTROUTING",
             "-m", "mark", "--mark", "888", "-j", "MASQUERADE"],
            check=False, silent=True)
        # FORWARD rules installed for gateway clients
        run(["iptables", "-D", "FORWARD", "-o", "mihomo", "-j", "ACCEPT"],
            check=False, silent=True)
        run(["iptables", "-D", "FORWARD", "-i", "mihomo", "-j", "ACCEPT"],
            check=False, silent=True)

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
    if shutil.which("iptables"):
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
    """Validate → persist → restart. Exits non-zero if Mihomo rejects the
    change, leaving the previous config live."""
    def _merged(fresh):
        fresh.update({k: v for k, v in cfg.items() if k not in SUBSCRIPTION_KEYS})
    with config_lock(ext_cfg_path):
        candidate = load_config(ext_cfg_path)
        _merged(candidate)
        if not _write_engine_config(candidate, install_dir):
            sys.exit(1)
        write_config(ext_cfg_path, candidate)
    if not _restart_engine(candidate, read_meta()):
        sys.exit(1)


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
        die('Usage: sudo ./pimiproxy-ultralite rule-add "TYPE,value,target"')
    rule                             = sys.argv[2].strip()
    install_dir, ext_cfg_path, cfg   = _load_cfg_for_rules()
    cfg.setdefault("rules", []).append(rule)
    _save_cfg_and_restart(install_dir, ext_cfg_path, cfg)
    info(f"Added rule: {rule}")


def do_rule_remove():
    if len(sys.argv) < 3 or not sys.argv[2].strip().isdigit():
        die("Usage: sudo ./pimiproxy-ultralite rule-remove <index>  (1-based)")
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
        elif distro_id == "raspbian":
            aliyun.write_text(
                f"Types: deb\nURIs: https://mirrors.aliyun.com/raspbian/raspbian\n"
                f"Suites: {codename}\n"
                f"Components: main contrib non-free non-free-firmware rpi\n"
                f"Signed-By: /usr/share/keyrings/raspbian-archive-keyring.gpg\n")
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
        _id = ""
        if Path("/etc/os-release").exists():
            for line in Path("/etc/os-release").read_text().splitlines():
                if line.startswith("ID="):
                    _id = line.split("=", 1)[1].strip().strip('"')
                    break
        is_arch32 = _id in ("arch32", "archlinux32")

        step("Switching pacman mirrorlist")
        ml = Path("/etc/pacman.d/mirrorlist")
        bak = ml.with_suffix(".bak")
        if not bak.exists() and ml.exists():
            shutil.copy2(ml, bak)
        if is_arch32:
            ml.write_text(
                "Server = https://mirror.yandex.ru/archlinux32/$arch/$repo\n"
                "Server = https://de.mirror.archlinux32.org/$arch/$repo\n"
                "Server = https://mirror.archlinux32.org/$arch/$repo\n"
            )
            info("Using archlinux32 mirrors (Yandex + fallbacks).")
        else:
            ml.write_text("Server = https://mirrors.aliyun.com/archlinux/$repo/os/$arch\n")
            info("Using Aliyun mirror.")
        with Spinner("Syncing package database"):
            run(["pacman", "-Sy", "--noconfirm"], check=False)
        info("Mirrorlist switched. Run 'standard-repos' to undo.")

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
    "test":          do_test,
    "standard-repos": do_standard_repos,
    "china-repos":   do_china_repos,
    "system":        do_system,
}

_TAKES_ARG = {"hostname", "debug", "export", "rule-add", "rule-remove", "test", "system"}


def main():
    # Warn if invoked directly instead of through the wrapper.
    if os.environ.get("PIMIPROXY_VIA_WRAPPER") != "1":
        print(f"{C.YELLOW}  ⚠{C.NC}  pimiproxy-ultralite-core.py should be run via the wrapper:")
        print(f"     {C.BOLD}sudo pimiproxy-ultralite {' '.join(sys.argv[1:]) or '<command>'}{C.NC}")
        print(f"     (or {C.DIM}sudo /opt/pimiproxy-lite/pimiproxy-ultralite <command>{C.NC})")
        print()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cmd  = args[0] if args else "install"

    if cmd not in COMMANDS:
        simple = "|".join(k for k in COMMANDS if k not in _TAKES_ARG)
        print(f"Usage: sudo ./pimiproxy-ultralite [{simple}]")
        print("       sudo ./pimiproxy-ultralite logs")
        print("       sudo ./pimiproxy-ultralite restart")
        print("       sudo ./pimiproxy-ultralite hostname <new-hostname>")
        print("       sudo ./pimiproxy-ultralite debug    [url]")
        print("       sudo ./pimiproxy-ultralite export   [output-dir]")
        print('       sudo ./pimiproxy-ultralite rule-add "TYPE,value,target"')
        print("       sudo ./pimiproxy-ultralite rule-remove <index>")
        sys.exit(1)

    try:
        COMMANDS[cmd]()
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
