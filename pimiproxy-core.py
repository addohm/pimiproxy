#!/usr/bin/env python3
"""
pimiproxy — install / uninstall / configure
Supports Debian, Ubuntu, Fedora, Arch, openSUSE, Alpine, and derivatives.
Run as root: sudo python3 pimiproxy-core.py [install|uninstall|configure]
"""

import os
import re
import sys
import shutil
import subprocess
import textwrap
import json
import threading
import platform
from pathlib import Path

# =============================================================================
# REQUIRE ROOT
# =============================================================================
if os.geteuid() != 0:
    sys.exit(
        "Run as root:  sudo ./pimiproxy [install|uninstall|configure]"
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
    build_config,
    SUBSCRIPTION_KEYS, config_path, config_lock, load_config, write_config,
    update_config, CONFIG_CANDIDATE, install_config, mihomo_test_cmd_docker,
    wait_for_controller, engine_health, print_engine_health, preflight_conflicts,
)


def _save_user_config(ext_cfg_path, cfg: dict) -> dict:
    """
    Persist an interactive session's config without clobbering concurrent
    writers. The session's copy may be minutes old, so a cron/UI refresh
    that ran meanwhile must win for the subscription keys; everything else
    (user-editable settings) comes from the session. Returns the merged dict.
    """
    def _merge(fresh):
        fresh.update({k: v for k, v in cfg.items() if k not in SUBSCRIPTION_KEYS})
    return update_config(ext_cfg_path, _merge)


def _engine_test_cmd(cfg: dict, meta: dict, install_dir) -> list:
    image = cfg.get("engine_image") or meta.get("engine_image") or MIHOMO_IMAGE
    return mihomo_test_cmd_docker(image, Path(install_dir) / "config", CONFIG_CANDIDATE)


def _write_engine_config(cfg: dict, meta: dict, install_dir) -> bool:
    """
    Validate cfg with `mihomo -t` and, only if it passes, install it as
    config.yaml. On rejection the reason is printed and the previous
    config.yaml stays live. Returns True on success.
    """
    with Spinner("Validating config with mihomo"):
        ok, why = install_config(Path(install_dir) / "config", build_config(cfg),
                                 _engine_test_cmd(cfg, meta, install_dir))
    if not ok:
        warn("Mihomo rejected the config — the previous config.yaml is still live.")
        warn(f"Reason: {why}")
        return False
    info("config.yaml validated and written.")
    return True


def _own_container_running() -> bool:
    r = run(["docker", "inspect", "-f", "{{.State.Running}}", "mihomo"],
            capture=True, check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _engine_health(meta: dict, cfg: dict = None) -> dict:
    """engine_health() for the full variant: process state from docker."""
    r = run(["docker", "inspect", "-f", "{{.State.Status}}", "mihomo"],
            capture=True, check=False)
    state = r.stdout.strip() if r.returncode == 0 else "missing"
    src = cfg or meta
    return engine_health(state, state == "running",
                         src.get("api_port", meta.get("api_port", "9090")),
                         src.get("api_secret", meta.get("api_secret", "")),
                         src.get("mixed_port", meta.get("mixed_port", "7890")))


def _restart_engine(compose, install_dir, cfg: dict, meta: dict) -> bool:
    """Restart the mihomo container and wait for its controller to answer."""
    r = compose_run(compose, "restart", "mihomo", cwd=install_dir, silent=True)
    if r.returncode != 0:
        warn("`compose restart mihomo` failed — is the container present? "
             "Try:  sudo pimiproxy logs")
        return False
    api_port = cfg.get("api_port") or meta.get("api_port", "9090")
    secret = cfg.get("api_secret") or meta.get("api_secret", "")
    with Spinner("Waiting for Mihomo to come up"):
        up, detail = wait_for_controller("127.0.0.1", api_port, secret, timeout=25)
    if up:
        info(f"Mihomo restarted ({detail}).")
        return True
    warn(f"Mihomo restarted but its controller is not answering: {detail}")
    warn("Check:  sudo pimiproxy logs")
    return False


def do_set_hostname():
    """Subcommand: pimiproxy-core.py hostname <new-hostname>"""
    if len(sys.argv) < 3 or not sys.argv[2].strip():
        die("Usage: sudo ./pimiproxy hostname <new-hostname>")
    name = sys.argv[2].strip()
    if len(name) > 24:
        die(f"Hostname too long ({len(name)} chars). Max 24 characters.")
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$', name):
        die("Hostname must contain only letters, digits, and hyphens, "
            "and cannot start or end with a hyphen.")
    step(f"Setting hostname to '{name}'")
    set_hostname(name)

    # Restart avahi-daemon so .local mDNS immediately uses the new name
    with Spinner("Restarting avahi-daemon"):
        result = run(["systemctl", "restart", "avahi-daemon"],
                     check=False,
                     silent=True)
    if result.returncode == 0:
        info(
            "avahi-daemon restarted — .local will resolve to the new hostname."
        )
    else:
        warn("avahi-daemon not running — .local won't update until it starts.")

    dhcp_renew()
    if confirm(
            "Reboot now to ensure the router fully registers the new hostname?",
            default=False):
        run(["reboot"])


def setup_docker_repo(distro: str):
    """
    Add Docker's official package repo if Docker is not already installed.
    China mirror support is handled by pimiproxy (the bootstrap wrapper),
    which prompts the user and installs Docker CE before this script runs.
    """
    if shutil.which("docker"):
        return  # already present, nothing to configure

    if distro == "debian":
        # Detect debian codename
        result = run(["lsb_release", "-cs"], capture=True, check=False)
        codename = result.stdout.strip(
        ) if result and result.returncode == 0 else "bookworm"

        base = "https://download.docker.com/linux/debian"

        # Install prerequisites for apt over HTTPS
        pkg_install(distro, "ca-certificates", "curl", "gnupg")

        keyring = Path("/etc/apt/keyrings/docker.gpg")
        keyring.parent.mkdir(parents=True, exist_ok=True)
        with Spinner("Adding Docker GPG key"):
            run(f'curl -fsSL {base}/gpg | gpg --dearmor -o {keyring}',
                silent=True)
        keyring.chmod(0o644)

        sources = Path("/etc/apt/sources.list.d/docker.list")
        deb_arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
        sources.write_text(
            f"deb [arch={deb_arch} signed-by={keyring}] {base} {codename} stable\n")
        run(["apt-get", "update", "-qq"], silent=True)
        info("Docker CE repo configured.")

    elif distro == "fedora":
        repo_url = "https://download.docker.com/linux/fedora/docker-ce.repo"
        pkg_install(distro, "dnf-plugins-core")
        with Spinner("Adding Docker CE repo"):
            run(["dnf", "config-manager", "--add-repo", repo_url], silent=True)
        info("Docker CE repo configured.")


def apt_install(*packages):
    """Legacy helper — callers that haven't been updated yet use this."""
    pkg_install("debian", *packages)


# =============================================================================
# DOCKER COMPOSE ABSTRACTION
# =============================================================================
def find_compose():
    """Return (bin, sub) for the available compose implementation."""
    if run_ok(["docker", "compose", "version"]):
        return ("docker", "compose")
    if shutil.which("docker-compose"):
        return ("docker-compose", "")
    return None


def compose_cmd(compose, *args):
    """Build the full compose command list."""
    bin_, sub = compose
    base = [bin_] + ([sub] if sub else [])
    return base + list(args)


def compose_run(compose, *args, cwd=None, silent=False):
    cmd = compose_cmd(compose, *args)
    return run(cmd, cwd=cwd, silent=silent)


# =============================================================================
# IMAGE LOADING
# =============================================================================
def _image_already_loaded(tag: str) -> bool:
    """Return True if the Docker image is already present locally."""
    r = run(["docker", "image", "inspect", tag],
            capture=True,
            check=False,
            silent=True)
    return r.returncode == 0


def _host_arch() -> str:
    """Return the Docker platform string for the current host CPU."""
    return {
        "aarch64": "linux/arm64",
        "x86_64":  "linux/amd64",
        "armv7l":  "linux/arm/v7",
        "armv6l":  "linux/arm/v6",
    }.get(platform.machine(), f"linux/{platform.machine()}")


def _find_archive(archive: str) -> "Path | None":
    """
    Search for a .tar.gz archive in order:
      1. docker-images/ next to this script (original source location)
      2. /opt/pimiproxy/docker-images/         (install dir copy)
      3. ~/pimiproxy/docker-images/             (synced source on SBC)
      4. Current working directory
    Returns the first Path that exists, or None.
    """
    candidates = [
        Path(__file__).resolve().parent / "docker-images" / archive,
        Path("/opt/pimiproxy/docker-images") / archive,
        Path.home() / "pimiproxy" / "docker-images" / archive,
        Path(archive),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_image(archive: str, fallback: str, arch: str) -> str:
    """
    Load a Docker image. Priority:
      1. Already in Docker — reuse as-is (safe for reinstall in China)
      2. Local .tar.gz archive found in any of several standard paths
      3. Registry pull — last resort; may fail in China / air-gapped envs

    Returns the image tag.
    """
    # 1 — already loaded (verify arch matches host before reusing)
    if _image_already_loaded(fallback):
        r = run(["docker", "image", "inspect", fallback,
                 "--format", "{{.Os}}/{{.Architecture}}"],
                capture=True, check=False, silent=True)
        img_platform = r.stdout.strip()
        if not img_platform or img_platform == arch:
            info(f"Already loaded: {fallback}")
            return fallback
        info(f"Cached {fallback} is {img_platform}, need {arch} — replacing.")
        run(["docker", "rmi", fallback], silent=True, check=False)

    # 2 — local archive
    p = _find_archive(archive)
    if p:
        with Spinner(f"Loading {p.name} from disk"):
            result = run(f"gunzip -c {p} | docker load", capture=True)
        loaded_tags = []
        for line in (result.stdout + result.stderr).splitlines():
            if "Loaded image:" in line:
                loaded_tags.append(line.split("Loaded image:")[-1].strip())

        if loaded_tags:
            # Multi-arch archives contain e.g. mihomo:arm64 + mihomo:amd64.
            # Prefer the tag whose suffix matches the host arch; fall back to
            # the only tag present (single-arch / legacy archive).
            arch_suffix = {"linux/arm64": "arm64", "linux/amd64": "amd64"}.get(
                arch, arch.split("/")[-1])
            chosen = next(
                (t for t in loaded_tags if t.endswith(f":{arch_suffix}")),
                loaded_tags[0],
            )
            # Verify the chosen image actually matches the host arch
            r = run(["docker", "image", "inspect", chosen,
                     "--format", "{{.Os}}/{{.Architecture}}"],
                    capture=True, check=False, silent=True)
            img_platform = r.stdout.strip()
            if img_platform and img_platform != arch:
                warn(f"Archive {p.name} has no {arch} image (found {img_platform}) — pulling from registry.")
                for t in loaded_tags:
                    run(["docker", "rmi", t], silent=True, check=False)
            else:
                # Retag as the canonical fallback name if needed
                if chosen != fallback:
                    run(["docker", "tag", chosen, fallback], silent=True)
                info(f"Loaded: {fallback}")
                return fallback
        else:
            warn(f"Could not parse image tag from {p.name} — will try pulling instead.")

    # 3 — registry pull (may fail in China / air-gapped environments)
    warn(f"No local archive found for {archive}.")
    warn(
        f"Attempting registry pull — this will fail if the registry is unreachable."
    )
    info(f"Pulling {fallback} ({arch})...")
    result = run(["docker", "pull", "--platform", arch, fallback], check=False)
    if result.returncode != 0:
        die(f"Failed to pull {fallback}.\n"
            f"  Place {archive} in docker-images/ next to pimiproxy-core.py and re-run.\n"
            f"  Registry: {fallback}")
    return fallback


# =============================================================================
# METADATA  (stored as JSON — no quoting issues, no sourcing issues)
# =============================================================================
META_PATH = Path("/opt/pimiproxy/.pimiproxy-meta.json")
BANNER_PATH = Path("/etc/profile.d/pimiproxy-banner.sh")

# Docker image references
MIHOMO_IMAGE = "metacubex/mihomo:latest"
METACUBEXD_IMAGE = "ghcr.io/metacubex/metacubexd:latest"
ZASHBOARD_IMAGE = "ghcr.io/zephyruso/zashboard:latest"


def write_meta(data: dict):
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(data, indent=2))


def read_meta() -> dict:
    if META_PATH.exists():
        info("Loaded install metadata.")
        return json.loads(META_PATH.read_text())
    warn("No metadata found — using defaults.")
    return {
        "install_dir": "/opt/pimiproxy",
        "hostname": "pimiproxy",
        "dashboard_port": "8080",
        "api_port": "9090",
        "mixed_port": "7890",
        "compose_bin": "docker",
        "compose_sub": "compose",
    }


# =============================================================================
# CONFIG GENERATION
# build_config() lives in pimiproxy_common.py — shared with the ultralite
# variant and the pimiproxy-api container so all three stay identical.
# =============================================================================


def build_compose(cfg: dict) -> str:
    engine = cfg["engine_image"]
    dashboard = cfg["dashboard_image"]
    dash_name = cfg["dash_name"]
    dash_port = cfg["dashboard_port"]
    ui_port = cfg.get("ui_port", "8585")
    install_dir = cfg.get("install_dir", "/opt/pimiproxy")

    lines = []

    def L(s=""):
        lines.append(s)

    L("services:")
    L()
    L("  mihomo:")
    L(f"    image: {engine}")
    L("    container_name: mihomo")
    L("    restart: unless-stopped")
    L("    network_mode: host")
    L("    privileged: true")
    L("    volumes:")
    L("      - ./config:/root/.config/mihomo")
    L()
    L("  dashboard:")
    L(f"    image: {dashboard}")
    L(f"    container_name: {dash_name}")
    L("    restart: unless-stopped")
    L("    ports:")
    L(f'      - "{dash_port}:80"')
    L()
    L("  pimiproxy-api:")
    L("    image: pimiproxy-api:latest")
    L("    container_name: pimiproxy-api")
    L("    restart: unless-stopped")
    L("    volumes:")
    L(f"      - {install_dir}:/pimiproxy:z")
    L("      - /var/run/docker.sock:/var/run/docker.sock")
    L()
    L("  pimiproxy-ui:")
    L("    image: pimiproxy-ui:latest")
    L("    container_name: pimiproxy-ui")
    L("    restart: unless-stopped")
    L("    ports:")
    L(f'      - "{ui_port}:80"')
    L("    depends_on:")
    L("      - pimiproxy-api")
    L()

    return "\n".join(lines)


BYPASS_SERVICE_PATH = Path("/etc/systemd/system/pimiproxy-bypass.service")


# =============================================================================
# CLASH-SYSTEM (advanced: toggle host-traffic bypass)
# =============================================================================
# See pimiproxy-ultralite-core.py for full rationale. In short:
# - Default: host traffic flows through Mihomo via TUN (auto-route installs
#   the ip rules that catch it).
# - `clash-system off` installs fwmark 888 on OUTPUT + ip rule at priority
#   5000 → main table, plus MASQUERADE on POSTROUTING (so the source IP
#   doesn't stay as the TUN's 198.18.0.1 when leaving the physical interface).
# - Gateway clients (Meta Quest etc. using this host as their DHCP gateway)
#   hit FORWARD, not OUTPUT — so they're never marked 888 and continue
#   routing through Mihomo regardless of the toggle.

CLASH_SYSTEM_PATH = Path("/usr/local/bin/clash-system")


def build_clash_system() -> str:
    return r"""#!/usr/bin/env bash
# clash-system — toggle host-traffic bypass
# Gateway clients (forwarded via this device) are always proxied.
set -euo pipefail

[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

MARK=888
RULE_PRIO=5000
MARK_HEX=$(printf '0x%x' "$MARK")

G=$'\033[0;32m'; Y=$'\033[1;33m'; R=$'\033[0;31m'; D=$'\033[2m'; B=$'\033[1m'; N=$'\033[0m'

iface_up()        { [[ -d /sys/class/net/mihomo ]]; }
mark_is_set()     { iptables -t mangle -C OUTPUT -j MARK --set-mark $MARK 2>/dev/null; }
rule_is_set()     { ip rule show | grep -qE "^${RULE_PRIO}:.*fwmark ${MARK_HEX}.*lookup main"; }
snat_is_set()     { iptables -t nat -C POSTROUTING -m mark --mark $MARK -j MASQUERADE 2>/dev/null; }
ensure_rule()     { rule_is_set || ip rule add fwmark "$MARK" lookup main priority "$RULE_PRIO"; }
remove_rule()     { while rule_is_set; do ip rule del fwmark "$MARK" lookup main priority "$RULE_PRIO" 2>/dev/null || break; done; }
set_mark()        { mark_is_set || iptables -t mangle -A OUTPUT -j MARK --set-mark $MARK; }
unset_mark()      { while mark_is_set; do iptables -t mangle -D OUTPUT -j MARK --set-mark $MARK 2>/dev/null || break; done; }
set_snat()        { snat_is_set || iptables -t nat -A POSTROUTING -m mark --mark $MARK -j MASQUERADE; }
unset_snat()      { while snat_is_set; do iptables -t nat -D POSTROUTING -m mark --mark $MARK -j MASQUERADE 2>/dev/null || break; done; }

# On systemd-resolved systems, the mihomo TUN gets auto-added as a DNS scope.
# That makes getaddrinfo return fake-IPs when our bypass is active. Strip it.
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
        iface_up || { echo "${R}✗${N}  mihomo TUN interface not found — is the container running?" >&2; exit 1; }
        unset_mark
        unset_snat
        remove_rule
        fix_resolved
        echo "${G}✓${N}  clash-system: ${B}on${N}  ${D}(host traffic routes through proxy — default)${N}"
    ;;
    off)
        iface_up || { echo "${R}✗${N}  mihomo TUN interface not found — is the container running?" >&2; exit 1; }
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


def build_banner(cfg: dict, meta: dict) -> str:
    """
    Generate /etc/profile.d/pimiproxy-banner.sh — displayed on interactive
    login. Hostname, ports, and AES capability are baked in at generation time;
    container state and routing rules are checked live on each login.
    """
    hostname = meta.get("hostname", "pimiproxy")
    dash_port = cfg.get("dashboard_port", meta.get("dashboard_port", "8080"))
    api_port = cfg.get("api_port", meta.get("api_port", "9090"))
    mixed_port = cfg.get("mixed_port", meta.get("mixed_port", "7890"))
    ui_port = cfg.get("ui_port", meta.get("ui_port", "8585"))
    cfg_file = f"{meta.get('install_dir', '/opt/pimiproxy')}/config/config.yaml"
    aes_ok = has_aes_hardware()

    lines = []

    def L(s=""):
        lines.append(s)

    L("#!/usr/bin/env bash")
    L("# pimiproxy login banner — managed by pimiproxy-core.py")
    L("# Shows proxy status on every interactive shell login.")
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
    L('printf "\\n${_C}${_B}  pimiproxy${_N}\\n%s\\n" "$_S"')
    L()
    L("# ── Proxy engine ─────────────────────────────────────────────────────")
    L("# Liveness = the engine's controller answers on its port. No secret is")
    L("# needed: a 401 still proves the engine is up. The TUN device alone is")
    L("# not proof — it can outlive the process or belong to another mihomo.")
    L(f"_CTL=$(curl -s -o /dev/null -m 2 -w '%{{http_code}}' http://127.0.0.1:{api_port}/version 2>/dev/null || echo 000)")
    L('if [[ "$_CTL" == "200" || "$_CTL" == "401" ]]; then')
    L("  printf '  Proxy engine    %s\\n' \"${_G}●  running${_N}\"")
    L("elif docker inspect -f '{{.State.Status}}' mihomo 2>/dev/null | grep -q restarting; then")
    L("  printf '  Proxy engine    %s\\n' \"${_R}●  crash-looping (bad config?)  sudo pimiproxy logs${_N}\"")
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
    L("# ── Proxy count ──────────────────────────────────────────────────────"
      )
    L(f"_COUNT=$(grep -c '- name:' '{cfg_file}' 2>/dev/null || echo 0)")
    L('if [[ "$_COUNT" -gt 0 ]]; then')
    L("  printf '  Proxies loaded  %s\\n' \"${_D}${_COUNT}${_N}\"")
    L("fi")
    L()
    L("# ── AES hardware acceleration (baked in at install time) ─────────────")
    if aes_ok:
        L("  printf '  AES hardware    %s\\n' \"${_G}●  available${_N}\"")
    else:
        L("  printf '  AES hardware    %s\\n' \"${_Y}○  software fallback — higher latency${_N}\"")
    L()
    # ── Tip (above endpoints) ─────────────────────────────────────────────────
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
    # ── Endpoints ─────────────────────────────────────────────────────────────
    L('printf "%s\\n" "$_S"')
    L(f'printf "  ${{_C}}Dashboard${{_N}}  →  http://{hostname}.lan:{dash_port}\\n"')
    L(f'printf "  ${{_C}}UI${{_N}}         →  http://{hostname}.lan:{ui_port}\\n"')
    L(f'printf "  ${{_C}}API${{_N}}        →  http://{hostname}.lan:{api_port}\\n"')
    L(f'printf "  ${{_B}}${{_C}}Proxy${{_N}}      →  ${{_B}}{hostname}.lan:{mixed_port}  (HTTP/SOCKS5)${{_N}}\\n"')
    L('printf "%s\\n" "$_S"')
    L('printf "  %s\\n" "${_D}pimiproxy [status|test|logs|configure|debug]${_N}"')
    L('printf "%s\\n\\n" "$_S"')

    return "\n".join(lines) + "\n"


# =============================================================================
# STATUS
# =============================================================================
def do_status():
    import time
    import urllib.request
    import urllib.error

    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    compose = (meta["compose_bin"], meta["compose_sub"])
    mixed_port = meta.get("mixed_port", "7890")
    api_port = meta.get("api_port", "9090")
    dash_port = meta.get("dashboard_port", "8080")
    hostname = meta.get("hostname", "pimiproxy")

    banner("pimiproxy status", C.CYAN)

    # ── containers ────────────────────────────────────────────────────────────
    step("Containers")
    run(compose_cmd(compose, "ps"), cwd=install_dir, check=False)

    # ── engine ────────────────────────────────────────────────────────────────
    # "Is the TUN device there" is not health: the device can exist while the
    # container is dead, or belong to a different mihomo entirely. Ask the
    # container runtime and the engine's own controller.
    step("Engine")
    health = _engine_health(meta)
    print_engine_health(health, "container", "sudo pimiproxy logs")

    # ── provider ──────────────────────────────────────────────────────────────
    step("Provider")
    ext_cfg_path = install_dir / ".pimiproxy-config.json"
    if ext_cfg_path.exists():
        cfg = json.loads(ext_cfg_path.read_text())
        url = cfg.get("subscription_url", "")
        if url:
            info(f"URL: {url}")
            count = cfg.get("subscription_proxies_yaml", "").count("- name:")
            info(f"Proxies loaded: {count}")
        else:
            info("No provider URL configured.")
    else:
        warn("Config not found — is pimiproxy installed?")

    # ── proxy connectivity test ───────────────────────────────────────────────
    step("Proxy connectivity test")
    proxy_addr = f"http://127.0.0.1:{mixed_port}"
    tests = [
        ("HTTP ", "http://www.gstatic.com/generate_204"),
        ("HTTPS", "https://www.gstatic.com/generate_204"),
    ]
    handler = urllib.request.ProxyHandler({
        "http": proxy_addr,
        "https": proxy_addr
    })
    opener = urllib.request.build_opener(handler)
    all_ok = True
    for label, test_url in tests:
        try:
            t0 = time.monotonic()
            with Spinner(f"Testing {label.strip()}"):
                with opener.open(test_url, timeout=10) as resp:
                    status = resp.status
                    ms = (time.monotonic() - t0) * 1000
            info(f"{label}  {test_url}  ->  {status}  ({ms:.0f}ms)")
        except Exception as exc:
            warn(f"{label}  {test_url}  ->  FAILED  ({exc})")
            all_ok = False
    if all_ok:
        info("Proxy is working correctly.")
    else:
        warn("Proxy test failed — check Mihomo logs:")
        warn("  sudo ./pimiproxy logs")

    # ── hardware ──────────────────────────────────────────────────────────────
    step("Hardware")
    if has_aes_hardware():
        info(
            "AES hardware acceleration: available — optimal encryption performance."
        )
    else:
        warn("AES hardware acceleration: not available.")
        warn("Encryption runs in software — expect higher latency on heavy or "
             "high-speed connections.")

    # ── endpoints ─────────────────────────────────────────────────────────────
    ui_port = meta.get("ui_port", "8585")
    print(f"\n  {C.DIM}{SEP_LINE}{C.NC}")
    print(f"  {C.CYAN}Dashboard{C.NC}  →  http://{hostname}.lan:{dash_port}")
    print(f"  {C.CYAN}UI{C.NC}         →  http://{hostname}.lan:{ui_port}")
    print(f"  {C.CYAN}API{C.NC}        →  http://{hostname}.lan:{api_port}")
    print(f"  {C.BOLD}{C.CYAN}Proxy{C.NC}      →  {C.BOLD}{hostname}.lan:{mixed_port}  (HTTP/SOCKS5){C.NC}")
    print(f"  {C.DIM}{SEP_LINE}{C.NC}\n")

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

    # Check for --url argument
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

    # Quick connectivity check
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
            parsed = urlparse(single_url if "://" in single_url else f"http://{single_url}")
            domain = (parsed.hostname or single_url).lower()
        except Exception:
            domain = single_url.lower()

        api_port = meta.get("api_port", "9090")
        api_secret = meta.get("api_secret", "")

        print(f"  {C.BOLD}Single URL test{C.NC}")
        print(SEP)
        print(f"  Domain: {C.BOLD}{domain}{C.NC}")
        print()

        # Probe through the proxy in a background thread
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

        # Poll Mihomo /connections API to find the rule match
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

        # Show connection result
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

        # Show rule match info
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
            # Fall back to static rule parsing
            install_dir = Path(meta["install_dir"])
            ext_cfg_path = install_dir / ".pimiproxy-config.json"
            matched_rule = None
            source = None
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
    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    compose = (meta["compose_bin"], meta["compose_sub"])

    service = sys.argv[2] if len(sys.argv) > 2 else "mihomo"
    if service not in {"mihomo", "dashboard", "pimiproxy-api", "pimiproxy-ui"}:
        die(f"Unknown service '{service}'. Use: mihomo, dashboard, pimiproxy-api, pimiproxy-ui"
            )

    step(f"Logs: {service}  (Ctrl-C to stop)")
    try:
        run(compose_cmd(compose, "logs", "-f", "--tail=50", service),
            cwd=install_dir,
            check=False)
    except KeyboardInterrupt:
        print()


# =============================================================================
# RESTART
# =============================================================================
def do_restart():
    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    compose = (meta["compose_bin"], meta["compose_sub"])

    service = sys.argv[2] if len(sys.argv) > 2 else None
    if service and service not in {
            "mihomo", "dashboard", "pimiproxy-api", "pimiproxy-ui"
    }:
        die(f"Unknown service '{service}'. Use: mihomo, dashboard, pimiproxy-api, pimiproxy-ui, or omit for all"
            )

    if service:
        step(f"Restarting {service}")
        compose_run(compose, "restart", service, cwd=install_dir)
    else:
        step("Restarting all containers")
        compose_run(compose, "restart", cwd=install_dir)
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
        die("clash-system not installed — run `sudo pimiproxy install` to add it")
    try:
        subprocess.run([str(script), action], check=False)
    except KeyboardInterrupt:
        print()


# =============================================================================
# UPDATE
# =============================================================================
def do_update():
    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    compose = (meta["compose_bin"], meta["compose_sub"])
    arch = meta.get("arch") or _host_arch()
    dash_type = meta.get("dashboard_type", "metacubexd")

    banner("pimiproxy update", C.CYAN)
    print()
    print("  All traffic is tunneled through Mihomo. Registry pulls will route")
    print("  through the proxy. If pulls fail, place .tar.gz archives in")
    print("  docker-images/ and retry.")
    print()

    def _pull(image: str, archive_name: str, label: str):
        """Load from archive if present and arch matches, otherwise pull from registry."""
        _archive = _find_archive(archive_name)
        needs_pull = True
        if _archive:
            with Spinner(f"Loading {label} from archive"):
                result = run(f"gunzip -c {_archive} | docker load", capture=True)
            tag = None
            for line in (result.stdout + result.stderr).splitlines():
                if "Loaded image:" in line:
                    tag = line.split("Loaded image:")[-1].strip()
            if tag:
                r = run(["docker", "image", "inspect", tag,
                         "--format", "{{.Os}}/{{.Architecture}}"],
                        capture=True, check=False, silent=True)
                img_platform = r.stdout.strip()
                if img_platform and img_platform != arch:
                    warn(f"Archive {archive_name} is {img_platform} but this host is {arch} — pulling correct platform.")
                    run(["docker", "rmi", tag], silent=True, check=False)
                else:
                    info(f"Loaded: {tag}")
                    needs_pull = False
        if needs_pull:
            info(f"Pulling {label} from registry ({arch})...")
            result = run(["docker", "pull", "--platform", arch, image], check=False)
            if result.returncode != 0:
                warn(f"Pull failed. Place {archive_name} in docker-images/ and retry.")

    # ── Third-party images ────────────────────────────────────────────────────
    step("Mihomo")
    _pull(MIHOMO_IMAGE, "mihomo.tar.gz", "mihomo")

    step("Dashboard")
    if dash_type == "zashboard":
        _pull(ZASHBOARD_IMAGE, "zashboard.tar.gz", "zashboard")
    else:
        _pull(METACUBEXD_IMAGE, "metacubexd.tar.gz", "metacubexd")

    # ── pimiproxy images ──────────────────────────────────────────────────────
    _script_dir = Path(__file__).resolve().parent

    for _svc, _tag, _archive_name in [
        ("pimiproxy-api", "pimiproxy-api:latest", "pimiproxy-api.tar.gz"),
        ("pimiproxy-ui",  "pimiproxy-ui:latest",  "pimiproxy-ui.tar.gz"),
    ]:
        step(_svc)
        _archive = _find_archive(_archive_name)
        _src = _script_dir / _svc
        if _archive:
            with Spinner(f"Loading {_svc} from archive"):
                run(["docker", "load", "-i", str(_archive)], silent=True)
            info(f"{_svc} loaded from archive.")
        elif _src.exists():
            info(f"Building {_svc} from source…")
            run(["docker", "build", "-t", _tag, str(_src)], silent=False)
            info(f"{_svc} built.")
        else:
            warn(f"No archive or source found for {_svc} — skipping.")

    # ── Recreate all containers ───────────────────────────────────────────────
    step("Recreating containers")
    compose_run(compose, "up", "-d", "--force-recreate", cwd=install_dir)
    info("Update complete.")


# =============================================================================
# DEBUG URL
# Fetch a subscription URL with the Clash User-Agent and inspect the response.
# Replaces the standalone getapiconfig.py utility.
# =============================================================================
def do_debug():
    import urllib.request
    import urllib.error

    banner("pimiproxy debug", C.CYAN)
    print("  Fetches a subscription URL using the Clash-Verge User-Agent and")
    print("  shows exactly what your provider returns.")
    print()

    # Accept URL as optional CLI argument for non-interactive use
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
            url,
            headers={
                "User-Agent": "Clash-Verge/1.0 (clash-verge-rev; mihomo)"
            })
        with Spinner("Waiting for response"):
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type", "(none)")
                content = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        die(f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        die(f"Request failed: {e.reason}")
    except Exception as e:
        die(f"Request failed: {e}")

    info(f"Status       : {status}")
    info(f"Content-Type : {content_type}")
    info(
        f"Response size: {len(content):,} bytes / {len(content.splitlines())} lines"
    )

    # Parse the response and report what was found
    print()
    blocks = extract_yaml_blocks(content)
    if blocks:
        if "proxies" in blocks:
            count = blocks["proxies"].count("- name:")
            info(f"Proxies      : {count}")
        else:
            warn(
                "No 'proxies:' block found — provider may require a different URL or token."
            )
        if "proxy-groups" in blocks:
            groups = blocks["proxy-groups"].count("- name:")
            info(f"Proxy groups : {groups}")
        if "rules" in blocks:
            rules = blocks["rules"].count("    - ")
            info(f"Rules        : {rules}")
        if blocks:
            info("Response looks like a valid Clash/Mihomo config.")
    else:
        warn("Response does not appear to contain valid Clash/Mihomo YAML.")
        warn(
            "Check the URL and confirm your provider supports Clash/Mihomo clients."
        )

    # Save full response for manual inspection
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
# Interactive editor for proxy groups and rules — reads existing config,
# lets the user add/remove entries, writes the result back.
# =============================================================================
def do_configure():
    banner("pimiproxy configure", C.CYAN)

    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    cfg_path = install_dir / "config" / "config.yaml"

    if not cfg_path.exists():
        die(f"Config not found at {cfg_path}. Has pimiproxy been installed?")

    # Load the stored extended config (we keep a JSON copy with full structure)
    ext_cfg_path = install_dir / ".pimiproxy-config.json"
    if ext_cfg_path.exists():
        cfg = json.loads(ext_cfg_path.read_text())
    else:
        # Bootstrap from meta
        cfg = {
            "api_port": meta.get("api_port", "9090"),
            "api_secret": meta.get("api_secret", "pimiproxy"),
            "mixed_port": meta.get("mixed_port", "7890"),
            "install_dir": meta["install_dir"],
            "subscription_url": "",
            "proxy_providers": {},
            "proxy_groups": [],
            "rules": [],
        }

    compose = (meta["compose_bin"], meta["compose_sub"])
    changed = False
    compose_changed = False

    while True:
        # Detect whether web UI images are already present
        _api_img_ok = run(
            ["docker", "image", "inspect", "pimiproxy-api:latest"],
            check=False,
            silent=True).returncode == 0
        _ui_img_ok = run(["docker", "image", "inspect", "pimiproxy-ui:latest"],
                         check=False,
                         silent=True).returncode == 0
        _ui_status = "enabled" if (_api_img_ok and _ui_img_ok) else "not built"

        print(f"\n{C.CYAN}{C.BOLD}  Configure pimiproxy{C.NC}")
        print(f"  {C.DIM}{SEP_LINE}{C.NC}")
        print(f"   {C.CYAN} 1){C.NC} Manage provider URL")
        print(f"   {C.CYAN} 2){C.NC} Manage proxy groups")
        print(f"   {C.CYAN} 3){C.NC} Manage rules")
        print(f"   {C.CYAN} 4){C.NC} Show current config")
        print(f"   {C.CYAN} 5){C.NC} Refresh provider now")
        print(f"   {C.CYAN} 6){C.NC} Change API secret")
        print(f"   {C.CYAN} 7){C.NC} Change ports")
        print(f"   {C.CYAN} 8){C.NC} Switch dashboard")
        print(f"   {C.CYAN} 9){C.NC} Save and apply")
        print(f"   {C.CYAN}10){C.NC} Exit without saving")
        print(f"   {C.CYAN}11){C.NC} Enable / rebuild web management UI  {C.DIM}[{_ui_status}]{C.NC}")
        print(f"  {C.DIM}{SEP_LINE}{C.NC}")
        choice = input(f"  {C.BOLD}Selection{C.NC} {C.DIM}[1-11]{C.NC}: ").strip()

        # ── provider URL ─────────────────────────────────────────────────────
        if choice == "1":
            current = cfg.get("subscription_url", "")
            print(f"\n  Current provider URL: {current or '(none)'}")
            print()
            print("  Your provider must give you a Clash/Mihomo API URL.")
            print("  It typically looks like:")
            print("    https://provider.example.com/api?token=abc123")
            print("  Leave blank to clear.")
            print()
            url = input("  Provider URL: ").strip()
            cfg["subscription_url"] = url
            if url:
                info(f"Provider URL set.")
            else:
                info("Provider URL cleared.")
            changed = True

        # ── proxy groups ─────────────────────────────────────────────────────
        elif choice == "2":
            while True:
                groups = cfg.get("proxy_groups", [])
                print(
                    f"\n  {C.BOLD}Proxy groups ({len(groups)} configured):{C.NC}"
                )
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
                    print(f"\n  {C.BOLD}Add proxy group{C.NC}")
                    name = ask("Group name")
                    if not name:
                        warn("Name cannot be empty.")
                        continue

                    gtype = choose("Group type", [
                        "select      — pick a server manually in the dashboard",
                        "url-test    — automatically use the fastest server",
                        "fallback    — use first available server",
                        "load-balance — spread traffic across servers",
                    ])
                    type_map = {
                        1: "select",
                        2: "url-test",
                        3: "fallback",
                        4: "load-balance"
                    }
                    gtype_str = type_map[gtype]

                    # Source — provider or inline proxies
                    source = choose(
                        "What should this group pull servers from?", [
                            "A proxy-provider (subscription)",
                            "Specific named proxies",
                        ])

                    group: dict = {"name": name, "type": gtype_str}

                    if source == 1:
                        providers = list(cfg.get("proxy_providers", {}).keys())
                        if cfg.get("subscription_url"):
                            providers = ["subscription"] + [
                                p for p in providers if p != "subscription"
                            ]
                        if not providers:
                            warn(
                                "No providers configured. Add a provider URL first (option 1)."
                            )
                            continue
                        print("  Available providers:")
                        for i, p in enumerate(providers, 1):
                            print(f"    {i}) {p}")
                        sel = input(
                            "  Select providers (comma-separated numbers): "
                        ).strip()
                        chosen = []
                        for s in sel.split(","):
                            s = s.strip()
                            if s.isdigit() and 1 <= int(s) <= len(providers):
                                chosen.append(providers[int(s) - 1])
                        if not chosen:
                            warn("No providers selected.")
                            continue
                        group["use"] = chosen
                    else:
                        raw = input(
                            "  Proxy names (comma-separated): ").strip()
                        proxies = [
                            p.strip() for p in raw.split(",") if p.strip()
                        ]
                        if not proxies:
                            warn("No proxies entered.")
                            continue
                        group["proxies"] = proxies

                    if gtype_str in ("url-test", "fallback", "load-balance"):
                        group["url"] = "https://www.gstatic.com/generate_204"
                        group["interval"] = 300
                        if gtype_str == "url-test":
                            group["tolerance"] = 50

                    cfg.setdefault("proxy_groups", []).append(group)
                    info(f"Added group '{name}' ({gtype_str}).")
                    changed = True

                elif sub == "r":
                    groups = cfg.get("proxy_groups", [])
                    if not groups:
                        warn("No groups to remove.")
                        continue
                    raw = input("  Remove group number(s) (comma-separated): "
                                ).strip()
                    indices = []
                    for s in raw.split(","):
                        s = s.strip()
                        if s.isdigit() and 1 <= int(s) <= len(groups):
                            indices.append(int(s) - 1)
                    if not indices:
                        warn("No valid selection.")
                        continue
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
                print(
                    "  Rules are evaluated top to bottom. The last rule should"
                )
                print("  always be a MATCH catch-all.")
                print()
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
                        f"  Insert at position (1-{len(rules)+1}, blank = end): "
                    ).strip()
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
                    raw = input(
                        "  Remove rule number(s) (comma-separated): ").strip()
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
                        frm = int(input("  Move rule number: ").strip()) - 1
                        to = int(input("  To position: ").strip()) - 1
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
                        ("Local hostname bypass (.lan and .lan go DIRECT)", [
                            "DOMAIN-SUFFIX,lan,DIRECT",
                            "DOMAIN-SUFFIX,local,DIRECT",
                        ]),
                        ("China mainland DIRECT (GEOIP)", [
                            "GEOIP,CN,DIRECT",
                        ]),
                        ("Catch-all → Proxy (add as final rule)", [
                            "MATCH,Proxy",
                        ]),
                        ("Catch-all → DIRECT (add as final rule)", [
                            "MATCH,DIRECT",
                        ]),
                    ]
                    print(f"\n  {C.BOLD}Presets:{C.NC}")
                    for i, (label, _) in enumerate(presets, 1):
                        print(f"    {i}) {label}")
                    print()
                    sel = input(
                        "  Select preset(s) (comma-separated): ").strip()
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
                if save_subscription(install_dir, url,
                                     test_cmd=_engine_test_cmd(cfg, meta, install_dir)):
                    # Reload cfg with the fresh (validated) subscription blocks
                    cfg = load_config(ext_cfg_path)
                    _restart_engine(compose, install_dir, cfg, meta)

        # ── change API secret ────────────────────────────────────────────────
        elif choice == "6":
            current_secret = cfg.get("api_secret",
                                     meta.get("api_secret", "pimiproxy"))
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
            cur_dash = cfg.get("dashboard_port",
                               meta.get("dashboard_port", "8080"))
            cur_ui = cfg.get("ui_port", meta.get("ui_port", "8585"))
            cur_api = cfg.get("api_port", meta.get("api_port", "9090"))
            cur_mixed = cfg.get("mixed_port", meta.get("mixed_port", "7890"))
            print(f"\n  {C.BOLD}Current ports:{C.NC}")
            print(f"    Dashboard      port : {cur_dash}")
            print(f"    Management UI  port : {cur_ui}")
            print(f"    Mihomo API     port : {cur_api}")
            print(f"    Mixed proxy    port : {cur_mixed}")
            print()
            new_dash = ask("Dashboard port", cur_dash)
            new_ui = ask("Management UI port", cur_ui)
            new_api = ask("Mihomo API port", cur_api)
            new_mixed = ask("Mixed proxy port", cur_mixed)
            if new_dash != cur_dash:
                cfg["dashboard_port"] = new_dash
                compose_changed = True
            if new_ui != cur_ui:
                cfg["ui_port"] = new_ui
                compose_changed = True
            if new_api != cur_api:
                cfg["api_port"] = new_api
                changed = True
            if new_mixed != cur_mixed:
                cfg["mixed_port"] = new_mixed
                changed = True
            if changed or compose_changed:
                info("Ports updated — save and apply to activate.")

        # ── switch dashboard ──────────────────────────────────────────────────
        elif choice == "8":
            cur_dash = cfg.get("dash_name", "metacubexd")
            other = "zashboard" if cur_dash == "metacubexd" else "metacubexd"
            other_img = (ZASHBOARD_IMAGE
                         if other == "zashboard" else METACUBEXD_IMAGE)
            other_arc = (f"{other}.tar.gz")
            print(f"\n  Current dashboard: {cur_dash}")
            if confirm(f"Switch to {other}?"):
                step(f"Loading {other} image")
                arch = meta.get("arch") or _host_arch()
                new_img = load_image(other_arc, other_img, arch)
                cfg["dashboard_image"] = new_img
                cfg["dash_name"] = other
                info(
                    f"Dashboard switched to {other} — save and apply to activate."
                )
                compose_changed = True

        # ── save and apply ───────────────────────────────────────────────────
        elif choice == "9":
            if not changed and not compose_changed:
                info("No changes to save.")
                break

            # Warn if UI images are missing — compose will include them but
            # docker compose up will fail for those services
            if not _api_img_ok or not _ui_img_ok:
                warn("Web UI images are not built yet.")
                warn("Run option 11 first to build them, then save and apply.")
                if not confirm("Continue saving anyway?", default=False):
                    continue

            # Validate the merged result (a refresh may have run during this
            # session) before persisting anything. On rejection nothing on
            # disk changes and the menu stays open so the edit can be fixed.
            def _merged(fresh):
                fresh.update({k: v for k, v in cfg.items() if k not in SUBSCRIPTION_KEYS})
            with config_lock(ext_cfg_path):
                candidate = load_config(ext_cfg_path)
                _merged(candidate)
                if not _write_engine_config(candidate, meta, install_dir):
                    continue
                write_config(ext_cfg_path, candidate)
            cfg = candidate

            # Rebuild docker-compose.yml when ports or dashboard changed
            if compose_changed:
                (install_dir / "docker-compose.yml").write_text(
                    build_compose(cfg))
                info("docker-compose.yml rebuilt.")

            # Persist port / secret changes back to meta so future subcommands
            # (status, logs, restart) read the right values
            for key in ("dashboard_port", "ui_port", "api_port", "mixed_port",
                        "api_secret"):
                if key in cfg:
                    meta[key] = cfg[key]
            write_meta(meta)

            # Rebuild login banner so it shows updated ports
            if BANNER_PATH.exists():
                BANNER_PATH.write_text(build_banner(cfg, meta))
                info("Login banner updated.")

            # Restart: recreate all containers if compose changed, otherwise
            # just bounce Mihomo (config.yaml is re-read on start)
            if compose_changed:
                if confirm("Recreate containers now to apply changes?",
                           default=True):
                    compose_run(compose,
                                "up",
                                "-d",
                                "--force-recreate",
                                cwd=install_dir)
                    info("Containers recreated.")
            else:
                if confirm("Restart Mihomo now to apply changes?",
                           default=True):
                    _restart_engine(compose, install_dir, cfg, meta)
            break

        elif choice == "10":
            info("Exiting without saving.")
            break

        # ── enable / rebuild web UI ───────────────────────────────────────────
        elif choice == "11":
            _script_dir = Path(__file__).resolve().parent
            _api_archive = _find_archive("pimiproxy-api.tar.gz")
            _ui_archive = _find_archive("pimiproxy-ui.tar.gz")
            _api_src = _script_dir / "pimiproxy-api"
            _ui_src = _script_dir / "pimiproxy-ui"

            step("pimiproxy-api image")
            if _image_already_loaded("pimiproxy-api:latest"):
                info("pimiproxy-api: already loaded — reusing existing image.")
            elif _api_archive:
                with Spinner("Loading pimiproxy-api from archive"):
                    run(["docker", "load", "-i",
                         str(_api_archive)],
                        silent=True)
                info("pimiproxy-api image loaded from archive.")
            elif _api_src.exists():
                info("Building pimiproxy-api from source…")
                run([
                    "docker", "build", "-t", "pimiproxy-api:latest",
                    str(_api_src)
                ],
                    silent=False)
                info("pimiproxy-api image built.")
            else:
                warn(
                    "pimiproxy-api archive not found in any standard location."
                )
                warn(
                    "Place pimiproxy-api.tar.gz in docker-images/ and try again."
                )
                continue

            step("pimiproxy-ui image")
            if _image_already_loaded("pimiproxy-ui:latest"):
                info("pimiproxy-ui: already loaded — reusing existing image.")
            elif _ui_archive:
                with Spinner("Loading pimiproxy-ui from archive"):
                    run(["docker", "load", "-i",
                         str(_ui_archive)],
                        silent=True)
                info("pimiproxy-ui image loaded from archive.")
            elif _ui_src.exists():
                info("Building pimiproxy-ui from source…")
                run([
                    "docker", "build", "-t", "pimiproxy-ui:latest",
                    str(_ui_src)
                ],
                    silent=False)
                info("pimiproxy-ui image built.")
            else:
                warn(
                    "pimiproxy-ui archive not found in any standard location.")
                warn(
                    "Place pimiproxy-ui.tar.gz in docker-images/ and try again."
                )
                continue

            # Ensure ui_port is set in cfg and meta
            cur_ui = cfg.get("ui_port", meta.get("ui_port", "8585"))
            new_ui = ask("Management UI port", cur_ui)
            if new_ui != cur_ui:
                cfg["ui_port"] = new_ui
                meta["ui_port"] = new_ui
            else:
                cfg.setdefault("ui_port", cur_ui)
                meta.setdefault("ui_port", cur_ui)

            # Generate ui-secret.txt if missing
            import secrets as _secrets
            ui_secret_path = install_dir / "ui-secret.txt"
            if not ui_secret_path.exists():
                ui_secret_path.write_text(_secrets.token_hex(32))
                ui_secret_path.chmod(0o600)
                info(f"Generated UI secret key: {ui_secret_path}")

            # Rebuild compose and restart
            (install_dir / "docker-compose.yml").write_text(build_compose(cfg))
            cfg = _save_user_config(ext_cfg_path, cfg)
            write_meta(meta)
            if BANNER_PATH.exists():
                BANNER_PATH.write_text(build_banner(cfg, meta))
            info("docker-compose.yml rebuilt with web UI services.")

            if confirm("Start web UI containers now?", default=True):
                compose_run(compose,
                            "up",
                            "-d",
                            "--force-recreate",
                            cwd=install_dir)
                info("Containers started.")
                ui_port = cfg["ui_port"]
                hostname = meta.get("hostname", "pimiproxy")
                info(f"Web UI available at: http://{hostname}.lan:{ui_port}")

        else:
            warn("Invalid selection.")


# =============================================================================
# INSTALL
# =============================================================================
def do_install():
    banner("pimiproxy installer", C.CYAN)

    # ── defaults ──────────────────────────────────────────────────────────────
    install_dir = "/opt/pimiproxy"
    _cur = current_hostname()
    hostname_new = _cur if _cur.lower(
    ) not in _DEFAULT_HOSTNAMES else "pimiproxy"
    dashboard_port = "8080"
    api_port = "9090"
    mixed_port = "7890"
    ui_port = "8585"
    api_secret = "pimiproxy"
    arch = _host_arch()
    dash_choice = 1

    # ── express / advanced ────────────────────────────────────────────────────
    step("Install mode")
    print()
    print("  Express install uses all defaults — only asks for hostname and")
    print("  your provider URL. Everything else is pre-configured.")
    print()
    print("  Advanced install walks through every option.")
    print()
    mode = choose("Install mode", [
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
        install_dir = ask("Install directory", install_dir)
        hostname_new = _ask_hostname(hostname_new)
        dashboard_port = ask("Dashboard port", dashboard_port)
        ui_port = ask("Management UI port", ui_port)
        api_port = ask("Mihomo API port", api_port)
        mixed_port = ask("Mixed proxy port", mixed_port)
        api_secret = ask_secret("API secret", api_secret)
        print()
        print("  ── Host-traffic default " + "─" * 17)
        print("  Advanced: should this device's OWN traffic route through the proxy")
        print("  on boot, or bypass it (direct internet)?  Gateway clients (e.g. Meta")
        print("  Quest using this device as their gateway) are always proxied either way.")
        print("  You can toggle at runtime with:  sudo clash-system on|off")
        print("  " + "─" * 42)
        boot_bypass = confirm("Bypass proxy for this device's traffic on boot?",
                              default=False)


    # ── preflight: anything already serving as "mihomo" on this host? ────────
    # A second mihomo (packaged, hand-installed, or the other pimiproxy
    # variant) silently wins the TUN device and the mixed port, and this
    # install then looks healthy while changing nothing. Refuse unless the
    # operator explicitly accepts it (or passes --force).
    step("Preflight")
    conflicts = preflight_conflicts(mixed_port, api_port, own_process_running=_own_container_running())
    if conflicts:
        warn("This host already has a Mihomo footprint that will conflict with pimiproxy:")
        for c in conflicts:
            warn(f"  • {c}")
        warn("Stop/remove it first, or choose different ports, unless you know what you're doing.")
        if "--force" not in sys.argv and not confirm("Continue anyway?", default=False):
            die("Install aborted. Re-run with --force to skip this check.")
    else:
        info("No existing mihomo unit, binary, TUN device, or port conflict found.")
    print()
    print("  ── Provider URL " + "─" * 26)
    print("  If your proxy provider gave you a Clash/Mihomo API URL, enter")
    print("  it here. It typically looks like:")
    print("    https://provider.example.com/api?token=abc123")
    print()
    print("  This must be a Clash API URL — not a regular VPN config file,")
    print("  not an app download link. If unsure, leave blank. You can add")
    print("  it later via the web UI or by running:")
    print("    sudo ./pimiproxy configure")
    print("  " + "─" * 42)
    print()
    while True:
        subscription_url = input("  Provider URL [leave blank to skip]: ").strip()
        if not subscription_url:
            break
        # Basic URL validation
        if not subscription_url.startswith(("http://", "https://")):
            warn("URL must start with http:// or https://")
            continue
        # Try fetching to verify it returns a valid Clash config
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

    # Auto-detect available dashboard archives (some arches like armv7 only
    # ship zashboard because metacubexd isn't published for those platforms).
    _has_metacubexd = _find_archive("metacubexd.tar.gz") is not None
    _has_zashboard = _find_archive("zashboard.tar.gz") is not None

    if not _has_metacubexd and not _has_zashboard:
        die("No dashboard archive found. Place metacubexd.tar.gz or "
            "zashboard.tar.gz in docker-images/ and re-run.")

    if not _has_metacubexd:
        info("Metacubexd not available for this arch — using Zashboard.")
        dash_choice = 2
    elif not _has_zashboard:
        info("Zashboard not available for this arch — using Metacubexd.")
        dash_choice = 1
    elif not express:
        print()
        dash_choice = choose("Dashboard", [
            "Metacubexd  (standard, default)",
            "Zashboard   (power user, more stats)",
        ])

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

    # mDNS — package names differ by distro
    _mdns_pkgs = {
        "debian": ("avahi-daemon", "libnss-mdns"),
        "fedora": ("avahi", "nss-mdns"),
        "arch": ("avahi", "nss-mdns"),
        "suse": ("avahi", "nss-mdns"),
        "alpine": ("avahi", ),
    }.get(distro, ("avahi-daemon", ))
    pkg_install(distro, *_mdns_pkgs)
    with Spinner("Enabling avahi-daemon"):
        run(["systemctl", "enable", "--now", "avahi-daemon"], silent=True)
    info("avahi-daemon ready.")

    # Docker — add repo first if needed, then install
    setup_docker_repo(distro)
    if not shutil.which("docker"):
        _docker_pkg = "docker-ce" if distro in ("fedora", "arch",
                                                "suse") else "docker.io"
        pkg_install(distro, _docker_pkg)
    else:
        v = run(["docker", "--version"], capture=True).stdout.strip()
        info(f"Docker already present: {v}")
    with Spinner("Enabling Docker"):
        run(["systemctl", "enable", "--now", "docker"], silent=True)

    # ── systemd-resolved: free port 53 for Mihomo's DNS server ─────────────
    # Mihomo TUN mode needs to bind 0.0.0.0:53 for dns-hijack. On distros with
    # systemd-resolved (Fedora, Ubuntu, etc.), the stub listener occupies port 53.
    # Disable it so Mihomo can take over DNS for TUN routing.
    _resolved_conf = Path("/etc/systemd/resolved.conf.d")
    if shutil.which("systemd-resolve") or shutil.which("resolvectl"):
        _resolved_conf.mkdir(parents=True, exist_ok=True)
        (_resolved_conf / "no-stub.conf").write_text(
            "[Resolve]\nDNSStubListener=no\n"
        )
        run(["systemctl", "restart", "systemd-resolved"],
            silent=True, check=False)
        info("systemd-resolved: stub listener disabled (port 53 freed for Mihomo).")

    # ── firewalld: ensure masquerade so container traffic can reach the internet
    # Docker CE manages its own firewalld zone and binds docker0 itself —
    # do NOT add docker0 to any zone manually or Docker will fail to start
    # with ZONE_CONFLICT. We only need to ensure masquerade is on so that
    # NAT works for traffic leaving the host.
    if shutil.which("firewall-cmd") and run_ok(
            ["systemctl", "is-active", "--quiet", "firewalld"]):
        with Spinner("Configuring firewalld"):
            run(["firewall-cmd", "--permanent", "--zone=public",
                 "--add-masquerade"], silent=True, check=False)
            for _fw_port in [mixed_port, dashboard_port, ui_port, api_port]:
                run(["firewall-cmd", "--permanent", "--zone=public",
                     "--add-port=%s/tcp" % _fw_port], silent=True, check=False)
            run(["firewall-cmd", "--reload"], silent=True, check=False)
        info("firewalld: masquerade + proxy ports opened on public zone.")

    compose = find_compose()
    if compose is None:
        info("Installing docker-compose-plugin...")
        try:
            pkg_install(distro, "docker-compose-plugin")
        except Exception:
            pkg_install(distro, "docker-compose")
        compose = find_compose()
        if compose is None:
            die("Could not find or install any Docker Compose variant.")

    info(f"Using compose: {compose[0]} {compose[1]}".strip())

    # ── images ────────────────────────────────────────────────────────────────
    step("Container images")

    engine = load_image("mihomo.tar.gz", MIHOMO_IMAGE, arch)
    info(f"Engine image:    {engine}")

    if dash_choice == 2:
        dashboard = load_image("zashboard.tar.gz", ZASHBOARD_IMAGE, arch)
        dash_name = "zashboard"
    else:
        dashboard = load_image("metacubexd.tar.gz", METACUBEXD_IMAGE, arch)
        dash_name = "metacubexd"
    info(f"Dashboard image: {dashboard} ({dash_name})")

    # ── pimiproxy-api and pimiproxy-ui images ─────────────────────────────────
    _script_dir = Path(__file__).resolve().parent
    _api_archive = _find_archive("pimiproxy-api.tar.gz")
    _ui_archive = _find_archive("pimiproxy-ui.tar.gz")
    _api_src = _script_dir / "pimiproxy-api"
    _ui_src = _script_dir / "pimiproxy-ui"

    arch_suffix = {"linux/arm64": "arm64", "linux/amd64": "amd64"}.get(
        arch, arch.split("/")[-1])

    for _svc, _tag, _svc_archive, _svc_src in [
        ("pimiproxy-api", "pimiproxy-api:latest", _api_archive, _api_src),
        ("pimiproxy-ui",  "pimiproxy-ui:latest",  _ui_archive,  _ui_src),
    ]:
        step(f"{_svc} image")
        _reuse = False
        if _image_already_loaded(_tag):
            r = run(["docker", "image", "inspect", _tag,
                     "--format", "{{.Os}}/{{.Architecture}}"],
                    capture=True, check=False, silent=True)
            img_platform = r.stdout.strip()
            if not img_platform or img_platform == arch:
                info(f"{_svc}: already loaded — reusing existing image.")
                _reuse = True
            else:
                info(f"Cached {_tag} is {img_platform}, need {arch} — replacing.")
                run(["docker", "rmi", _tag], silent=True, check=False)
        if _reuse:
            pass
        elif _svc_archive:
            with Spinner(f"Loading {_svc} from archive"):
                result = run(["docker", "load", "-i", str(_svc_archive)], capture=True)
            loaded = [l.split("Loaded image:")[-1].strip()
                      for l in (result.stdout + result.stderr).splitlines()
                      if "Loaded image:" in l]
            # Pick arch-specific tag from multi-arch archive, fall back to first
            chosen = next((t for t in loaded if t.endswith(f":{arch_suffix}")),
                          loaded[0] if loaded else None)
            if chosen:
                r = run(["docker", "image", "inspect", chosen,
                         "--format", "{{.Os}}/{{.Architecture}}"],
                        capture=True, check=False, silent=True)
                img_platform = r.stdout.strip()
                if img_platform and img_platform != arch:
                    warn(f"Archive has no {arch} image for {_svc} — building from source.")
                    for t in loaded:
                        run(["docker", "rmi", t], silent=True, check=False)
                    if _svc_src.exists():
                        run(["docker", "build", "-t", _tag, str(_svc_src)], silent=False)
                        info(f"{_svc} image built.")
                    else:
                        warn(f"No source found for {_svc} — Web UI will not work.")
                else:
                    if chosen != _tag:
                        run(["docker", "tag", chosen, _tag], silent=True)
                    info(f"{_svc} image loaded from archive.")
        elif _svc_src.exists():
            info(f"Building {_svc} from source (this may take a minute)…")
            run(["docker", "build", "-t", _tag, str(_svc_src)], silent=False)
            info(f"{_svc} image built.")
        else:
            warn(f"{_svc} archive not found in any standard location — skipping. Web UI will not work.")

    # ── directories ───────────────────────────────────────────────────────────
    step("Directories")

    install_path = Path(install_dir)
    (install_path / "config" / "providers").mkdir(parents=True, exist_ok=True)
    info(f"Created {install_dir}")

    # Set SELinux context on install dir so containers can access the volume
    # (belt-and-suspenders alongside the :z flag in docker-compose.yml)
    if shutil.which("chcon"):
        run(["chcon", "-R", "-t", "container_file_t", str(install_path)],
            silent=True, check=False)

    # Copy bundled geodata files so Mihomo doesn't try to download them
    _script_dir = Path(__file__).resolve().parent
    _geo_files = ["geoip.metadb", "geosite.dat", "country.mmdb"]
    for _gf in _geo_files:
        _src = _script_dir / "docker-images" / _gf
        _dst = install_path / "config" / _gf
        if _src.exists() and not _dst.exists():
            shutil.copy2(_src, _dst)
            info(f"Copied {_gf} to config directory")

    # ── UI secret key ─────────────────────────────────────────────────────────
    step("UI secret key")

    import secrets as _secrets
    ui_secret_path = install_path / "ui-secret.txt"
    if not ui_secret_path.exists():
        ui_secret_path.write_text(_secrets.token_hex(32))
        ui_secret_path.chmod(0o600)
        info(f"Generated UI secret key: {ui_secret_path}")
    else:
        info(f"UI secret key already exists: {ui_secret_path}")

    # ── config ────────────────────────────────────────────────────────────────
    step("Mihomo config")

    cfg = {
        "api_port": api_port,
        "api_secret": api_secret,
        "mixed_port": mixed_port,
        "install_dir": install_dir,
        "subscription_url": subscription_url,
        "proxy_providers": {},
        "proxy_groups": [],
        "rules": [],
        "subscription_proxies_yaml": "",
        "subscription_groups_yaml": "",
        "subscription_rules_yaml": "",
        "engine_image": engine,
        "dashboard_image": dashboard,
        "dash_name": dash_name,
        "dashboard_port": dashboard_port,
        "ui_port": ui_port,
        "auto_refresh_enabled": True,
        "refresh_interval_days": 1,
    }

    # ── subscription pre-fetch ────────────────────────────────────────────────
    # Fetch before writing config so proxies/groups/rules are included on first start
    if subscription_url:
        step("Subscription")
        blocks = fetch_subscription(subscription_url)
        if blocks:
            cfg["subscription_proxies_yaml"] = blocks.get("proxies", "")
            cfg["subscription_groups_yaml"] = blocks.get("proxy-groups", "")
            cfg["subscription_rules_yaml"] = blocks.get("rules", "")
        else:
            warn(
                "Subscription fetch failed — Mihomo will start with no proxies."
            )
            warn("Run after install:  sudo ./pimiproxy refresh")

    # Validate with the freshly loaded engine image. If the provider's
    # payload is rejected, install without it rather than leave a config
    # Mihomo cannot start — the URL is kept so `pimiproxy refresh` can retry.
    if not _write_engine_config(cfg, {}, install_path):
        if cfg.get("subscription_proxies_yaml"):
            warn("Installing without the provider's proxies. After install, check the URL with:")
            warn("  sudo pimiproxy debug   then   sudo pimiproxy refresh")
            for k in SUBSCRIPTION_KEYS:
                cfg[k] = ""
            if not _write_engine_config(cfg, {}, install_path):
                die("Even the minimal config failed validation — see the reason above.")
        else:
            die("Config failed validation — see the reason above.")
    write_config(config_path(install_path), cfg)

    # ── copy script + wrapper + install daily refresh cron ───────────────────
    step("Wrapper + refresh script")

    script_dest = install_path / "pimiproxy-core.py"
    shutil.copy2(__file__, script_dest)
    info(f"Core script copied to {script_dest}.")

    # Shared helper module — core.py imports this at startup, so it must
    # sit next to the installed core script.
    _common_src = Path(__file__).resolve().parent / "pimiproxy_common.py"
    if _common_src.exists():
        common_dest = install_path / "pimiproxy_common.py"
        shutil.copy2(_common_src, common_dest)
        info(f"Shared helpers copied to {common_dest}.")

    # Copy the wrapper script next to core.py if we can find it
    _wrapper_src = Path(__file__).resolve().parent / "pimiproxy"
    wrapper_dest = install_path / "pimiproxy"
    if _wrapper_src.exists():
        shutil.copy2(_wrapper_src, wrapper_dest)
        wrapper_dest.chmod(0o755)
        info(f"Wrapper copied to {wrapper_dest}.")

        # Symlink so users can run `sudo pimiproxy <cmd>` from anywhere
        _symlink = Path("/usr/local/bin/pimiproxy")
        if _symlink.is_symlink() or _symlink.exists():
            _symlink.unlink()
        _symlink.symlink_to(wrapper_dest)
        info(f"Symlink: {_symlink} → {wrapper_dest}")

    cron_path = Path("/etc/cron.daily/pimiproxy-refresh")
    # Use the wrapper so PIMIPROXY_VIA_WRAPPER is set in cron invocations
    cron_lines = [
        "#!/usr/bin/env bash",
        "# pimiproxy subscription auto-refresh — generated by installer",
        f'exec "{wrapper_dest}" refresh-cron',
    ] if _wrapper_src.exists() else [
        "#!/usr/bin/env bash",
        "# pimiproxy subscription auto-refresh — generated by installer",
        f'exec python3 "{script_dest}" refresh-cron',
    ]
    cron_path.write_text("\n".join(cron_lines) + "\n")
    cron_path.chmod(0o755)
    info("Daily refresh cron installed.")

    # ── docker-compose.yml ────────────────────────────────────────────────────
    step("Docker Compose")

    compose_text = build_compose(cfg)
    (install_path / "docker-compose.yml").write_text(compose_text)
    info("docker-compose.yml written.")

    # ── metadata ──────────────────────────────────────────────────────────────
    meta = {
        "install_dir": install_dir,
        "hostname": hostname_new,
        "dashboard_port": dashboard_port,
        "ui_port": ui_port,
        "api_port": api_port,
        "mixed_port": mixed_port,
        "api_secret": api_secret,
        "arch": arch,
        "compose_bin": compose[0],
        "compose_sub": compose[1],
    }
    write_meta(meta)

    # ── IP forwarding ─────────────────────────────────────────────────────────
    step("Enabling IP forwarding")

    sysctl_conf = Path("/etc/sysctl.d/99-pimiproxy.conf")
    sysctl_conf.write_text("net.ipv4.ip_forward = 1\n")
    run(["sysctl", "-w", "net.ipv4.ip_forward=1"], silent=True)
    info(
        "IP forwarding enabled (persistent via /etc/sysctl.d/99-pimiproxy.conf)."
    )

    # Allow gateway clients (Meta Quest etc.) to reach Mihomo's TUN.
    # Docker sets FORWARD policy DROP; without this rule, forwarded packets
    # from LAN devices using this host as their gateway would be dropped.
    if shutil.which("iptables"):
        with Spinner("Configuring FORWARD for gateway clients"):
            run(["iptables", "-D", "FORWARD", "-o", "mihomo", "-j", "ACCEPT"],
                check=False, silent=True)
            run(["iptables", "-D", "FORWARD", "-i", "mihomo", "-j", "ACCEPT"],
                check=False, silent=True)
            run(["iptables", "-I", "FORWARD", "-o", "mihomo", "-j", "ACCEPT"],
                check=False, silent=True)
            run(["iptables", "-I", "FORWARD", "-i", "mihomo", "-j", "ACCEPT"],
                check=False, silent=True)
        info("FORWARD rules installed (gateway clients → TUN).")

    # ── clash-system (advanced: toggle host-traffic bypass) ──────────────────
    step("clash-system utility")
    CLASH_SYSTEM_PATH.write_text(build_clash_system())
    CLASH_SYSTEM_PATH.chmod(0o755)
    info(f"Installed {CLASH_SYSTEM_PATH}  (advanced: sudo clash-system [on|off|status])")

    if boot_bypass:
        # Wait (up to 30s) for the mihomo Docker container to bring up the TUN,
        # then apply the bypass. docker.service is the right ordering dep on the
        # full variant (mihomo runs inside Docker).
        BYPASS_SERVICE_PATH.write_text(
            "[Unit]\n"
            "Description=pimiproxy — apply clash-system bypass after mihomo is up\n"
            "After=docker.service\n"
            "Requires=docker.service\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            "ExecStartPre=/bin/sh -c 'for i in $(seq 1 30); do [ -d /sys/class/net/mihomo ] && exit 0; sleep 1; done; exit 1'\n"
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

    # Ensure non-login interactive shells (desktop terminals) also show the banner.
    # Fedora's /etc/bashrc already sources profile.d; Arch and Ubuntu do not.
    _bashrc = Path("/etc/bash.bashrc")  # Arch, Ubuntu, Debian
    if not _bashrc.exists():
        _bashrc = Path("/etc/bashrc")   # Fedora, RHEL
    _banner_source = f'[[ -f {BANNER_PATH} ]] && source {BANNER_PATH}'
    if _bashrc.exists():
        _txt = _bashrc.read_text()
        if str(BANNER_PATH) not in _txt:
            _bashrc.write_text(_txt.rstrip() + "\n\n" + _banner_source + "\n")
            info(f"Banner source added to {_bashrc} for desktop terminals.")

    # ── launch ────────────────────────────────────────────────────────────────
    step("Starting services")

    compose_run(compose, "up", "-d", "--force-recreate", cwd=install_path)
    info("Containers started.")

    # On systemd-resolved systems, wait for mihomo TUN to come up and strip it
    # from resolved's DNS scopes. Without this, getaddrinfo returns fake-IPs
    # from mihomo instead of going to the real resolver — which breaks
    # clash-system off (host bypass).
    if shutil.which("resolvectl") and run_ok(
            ["systemctl", "is-active", "--quiet", "systemd-resolved"]):
        with Spinner("Configuring systemd-resolved for mihomo TUN"):
            # Wait up to 15s for the mihomo interface to appear
            for _ in range(15):
                if Path("/sys/class/net/mihomo").exists():
                    break
                import time as _t; _t.sleep(1)
            run(["resolvectl", "dns", "mihomo", ""], silent=True, check=False)
            run(["resolvectl", "default-route", "mihomo", "false"],
                silent=True, check=False)
            run(["resolvectl", "flush-caches"], silent=True, check=False)
        info("systemd-resolved: mihomo TUN removed from DNS scopes.")

    # ── summary ───────────────────────────────────────────────────────────────
    if shutil.which("hostname"):
        _r = run(["hostname", "-I"], capture=True, check=False)
        lan_ip = (_r.stdout.strip().split() or [""])[0]
    else:
        lan_ip = ""
    if not lan_ip:
        # Fallback: get first global-scope IPv4 address
        _r = run(["ip", "-4", "addr", "show", "scope", "global"],
                 capture=True, check=False)
        _m = re.search(r'inet (\S+)/', _r.stdout) if _r else None
        lan_ip = _m.group(1) if _m else "<device-ip>"

    _gw_r = run(["ip", "route", "show", "default"], capture=True, check=False)
    _gw = ""
    _cidr = "24"
    if _gw_r and _gw_r.returncode == 0:
        _tokens = _gw_r.stdout.split()
        for _i, _t in enumerate(_tokens):
            if _t == "via" and _i + 1 < len(_tokens):
                _gw = _tokens[_i + 1]
            if _t == "dev" and _i + 1 < len(_tokens):
                _iface = _tokens[_i + 1]
                _addr_r = run(["ip", "-4", "addr", "show", _iface],
                              capture=True,
                              check=False)
                if _addr_r and _addr_r.returncode == 0:
                    _m = re.search(r'inet \S+/(\d+)', _addr_r.stdout)
                    if _m:
                        _cidr = _m.group(1)
                break

    _SEP = f"  {C.DIM}{SEP_LINE}{C.NC}"

    print()
    print(f"{C.BOLD}{C.GREEN}  pimiproxy — installation complete{C.NC}")
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
    print(f"  {C.CYAN}Dashboard{C.NC}  →  http://{hostname_new}.lan:{dashboard_port}")
    print(f"             →  http://{lan_ip}:{dashboard_port}")
    print(f"  {C.CYAN}UI{C.NC}         →  http://{hostname_new}.lan:{ui_port}")
    print(f"  {C.CYAN}API{C.NC}        →  http://{hostname_new}.lan:{api_port}")
    print(f"  {C.BOLD}{C.CYAN}Proxy{C.NC}      →  {C.BOLD}{hostname_new}.lan:{mixed_port}  (HTTP/SOCKS5){C.NC}")
    print(f"  {C.YELLOW}Login{C.NC}      →  API secret: {C.BOLD}{api_secret}{C.NC}")
    print(_SEP)
    print(f"  {C.DIM}pimiproxy [status|test|logs|configure|debug]{C.NC}")
    print(_SEP)
    print()

    if subscription_url:
        print(f"  {C.YELLOW}Next step:{C.NC} Open the dashboard and select a proxy server for the")
        print(f"  {C.BOLD}🧭 Meta Quest{C.NC} group. This group handles Meta/Oculus/Facebook traffic")
        print(f"  and has no proxy selected by default.")
        print()

    if not has_aes_hardware():
        warn(
            "Hardware AES not available — encryption runs in software. Expect higher latency on heavy traffic."
        )
    if _hostname_changed:
        dhcp_renew()
        info(f"{hostname_new}.local is available immediately via mDNS.")
        warn(
            f"{hostname_new}.lan requires a DHCP lease renewal or reboot to register with your router."
        )
        if confirm(
                "Reboot now to ensure the router fully registers the new hostname?",
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
    """Export user config, cached provider YAML, and final config.yaml."""
    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    ext_cfg_path = install_dir / ".pimiproxy-config.json"
    cfg = json.loads(ext_cfg_path.read_text()) if ext_cfg_path.exists() else {}

    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)

    banner("pimiproxy export", C.CYAN)

    # ── User config ───────────────────────────────────────────────────────────
    user_data = {
        "proxy_groups": cfg.get("proxy_groups", []),
        "rules": cfg.get("rules", []),
    }
    user_path = out_dir / "pimiproxy-user-config.json"
    user_path.write_text(json.dumps(user_data, indent=2))
    info(f"User config (groups + rules)  →  {user_path}")

    # ── Provider YAML ─────────────────────────────────────────────────────────
    proxies = cfg.get("subscription_proxies_yaml", "")
    groups = cfg.get("subscription_groups_yaml", "")
    rules = cfg.get("subscription_rules_yaml", "")
    if proxies or groups or rules:
        parts = ["# Provider subscription cache — exported from pimiproxy"]
        url = cfg.get("subscription_url", "")
        if url:
            parts.append(f"# Source: {url}")
        parts.append("")
        if proxies: parts.append(proxies.rstrip())
        if groups: parts.append(groups.rstrip())
        if rules: parts.append(rules.rstrip())
        provider_path = out_dir / "pimiproxy-provider.yaml"
        provider_path.write_text("\n".join(parts) + "\n")
        info(f"Provider subscription YAML    →  {provider_path}")
    else:
        warn(
            "No provider YAML cached — skipping (run refresh first if needed)."
        )

    # ── Final config ──────────────────────────────────────────────────────────
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
    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    ext_cfg_path = install_dir / ".pimiproxy-config.json"

    if not ext_cfg_path.exists():
        die("No pimiproxy config found. Is pimiproxy installed?")

    cfg = json.loads(ext_cfg_path.read_text())
    url = cfg.get("subscription_url", "")

    if not url:
        info("No provider URL configured — nothing to refresh.")
        return

    step("Refreshing provider")
    # save_subscription updates .pimiproxy-config.json with new blocks
    # save_subscription validates the merged config with mihomo -t and
    # installs config.yaml before it persists the JSON, so a bad provider
    # payload leaves the previous working config live.
    if not save_subscription(install_dir, url,
                             test_cmd=_engine_test_cmd(cfg, meta, install_dir)):
        die("Provider refresh failed.")

    cfg = load_config(ext_cfg_path)
    compose = (meta["compose_bin"], meta["compose_sub"])
    if not _restart_engine(compose, install_dir, cfg, meta):
        sys.exit(1)

    # Rebuild the login banner so proxy count stays current
    BANNER_PATH.write_text(build_banner(cfg, meta))
    info("Login banner updated.")


# =============================================================================
# REFRESH-CRON  (called by cron job — respects auto_refresh settings)
# =============================================================================
def do_refresh_cron():
    """Called by the daily cron job. Checks auto_refresh settings before running."""
    import time

    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    ext_cfg_path = install_dir / ".pimiproxy-config.json"

    if not ext_cfg_path.exists():
        return  # not installed — silently exit

    cfg = json.loads(ext_cfg_path.read_text())

    if not cfg.get("auto_refresh_enabled", True):
        return  # auto-refresh disabled by user

    interval_days = int(cfg.get("refresh_interval_days", 1))
    stamp_path = install_dir / ".last_refresh"

    if interval_days > 1 and stamp_path.exists():
        try:
            last = float(stamp_path.read_text().strip())
            elapsed_days = (time.time() - last) / 86400
            if elapsed_days < interval_days:
                return  # not time yet
        except (ValueError, OSError):
            pass  # corrupt stamp — proceed with refresh

    do_refresh()

    # Update timestamp after a successful refresh
    stamp_path.write_text(str(time.time()))


# =============================================================================
# UNINSTALL
# =============================================================================
def do_uninstall():
    banner("pimiproxy uninstaller", C.RED)

    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    compose = (meta["compose_bin"], meta["compose_sub"])

    if not confirm(
            "Stop and remove all pimiproxy containers, images, and files?"):
        print("Aborted.")
        return

    # ── stop containers ───────────────────────────────────────────────────────
    step("Stopping containers")

    compose_file = install_dir / "docker-compose.yml"
    if compose_file.exists():
        active = find_compose()
        if active:
            run(compose_cmd(active, "down", "--remove-orphans"),
                cwd=install_dir,
                check=False)
            info("Containers stopped.")
        else:
            warn("No compose binary found — stopping containers by name.")
            for name in [
                    "mihomo", "metacubexd", "zashboard", "pimiproxy-api",
                    "pimiproxy-ui"
            ]:
                run(["docker", "stop", name], check=False, silent=True)
                run(["docker", "rm", name], check=False, silent=True)
    else:
        warn("No docker-compose.yml found — stopping containers by name.")
        for name in [
                "mihomo", "metacubexd", "zashboard", "pimiproxy-api",
                "pimiproxy-ui"
        ]:
            run(["docker", "stop", name], check=False, silent=True)
            run(["docker", "rm", name], check=False, silent=True)

    # ── remove images ─────────────────────────────────────────────────────────
    step("Removing Docker images")

    for img in [
            "ghcr.io/metacubex/mihomo:latest",
            "ghcr.io/metacubex/metacubexd:latest",
            "ghcr.io/zephyruso/zashboard:latest",
            "pimiproxy-api:latest",
            "pimiproxy-ui:latest",
    ]:
        result = run(["docker", "image", "inspect", img],
                     check=False,
                     silent=True)
        if result.returncode == 0:
            r = run(["docker", "rmi", img], check=False, capture=True)
            if r.returncode == 0:
                info(f"Removed: {img}")
            else:
                warn(f"Could not remove {img} — in use elsewhere?")

    # ── files ─────────────────────────────────────────────────────────────────
    step("Removing files")

    ui_secret = install_dir / "ui-secret.txt"
    if ui_secret.exists():
        ui_secret.unlink()
        info("Removed ui-secret.txt")

    if install_dir.exists():
        shutil.rmtree(install_dir)
        info(f"Removed {install_dir}")

    # Remove the /usr/local/bin/pimiproxy symlink
    _symlink = Path("/usr/local/bin/pimiproxy")
    if _symlink.is_symlink() or _symlink.exists():
        _symlink.unlink()
        info(f"Removed {_symlink}")

    # Remove clash-system script and any active bypass rules
    cs = Path("/usr/local/bin/clash-system")
    if cs.exists():
        # Flip to 'on' first so any active mark/rule gets cleared
        run([str(cs), "on"], check=False, silent=True)
        cs.unlink()
        info("Removed /usr/local/bin/clash-system")
    # Belt-and-suspenders: clear any leftover rules
    if shutil.which("ip"):
        run(["ip", "rule", "del", "fwmark", "888", "lookup", "main",
             "priority", "5000"], check=False, silent=True)
    if shutil.which("iptables"):
        run(["iptables", "-t", "mangle", "-D", "OUTPUT",
             "-j", "MARK", "--set-mark", "888"], check=False, silent=True)
        run(["iptables", "-t", "nat", "-D", "POSTROUTING",
             "-m", "mark", "--mark", "888", "-j", "MASQUERADE"],
            check=False, silent=True)
        # FORWARD rules for gateway clients
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

    # Remove banner source line from system bashrc
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
        run(["sysctl", "-w", "net.ipv4.ip_forward=0"],
            check=False,
            silent=True)
        info("Removed IP forwarding config and disabled forwarding.")

    # Clean up legacy bypass service and iptables rules from older installs
    if BYPASS_SERVICE_PATH.exists():
        run(["systemctl", "stop", "pimiproxy-bypass"],
            check=False,
            silent=True)
        run(["systemctl", "disable", "pimiproxy-bypass"],
            check=False,
            silent=True)
        BYPASS_SERVICE_PATH.unlink()
        run(["systemctl", "daemon-reload"], silent=True, check=False)
        info("Removed legacy bypass service.")
    run([
        "iptables", "-t", "mangle", "-D", "OUTPUT", "-j", "MARK",
        "--set-mark", "666"
    ],
        check=False,
        silent=True)

    # ── hostname ──────────────────────────────────────────────────────────────
    step("Hostname")

    current = current_hostname()
    if current == meta.get("hostname"):
        if confirm(f"Reset hostname '{current}' back to 'raspberrypi'?"):
            run(["hostnamectl", "set-hostname", "raspberrypi"])
            hosts = Path("/etc/hosts")
            txt = hosts.read_text()
            hosts.write_text("\n".join(
                f"127.0.1.1\traspberrypi" if l.startswith("127.0.1.1") else l
                for l in txt.splitlines()))
            info("Hostname reset to 'raspberrypi'.")

    # ── optional package removal ───────────────────────────────────────────────
    step("Optional package removal")

    if confirm(
            "Remove avahi-daemon? Only if nothing else on this device needs mDNS."
    ):
        distro = detect_distro()
        _mdns_pkgs = {
            "debian": ("avahi-daemon", "libnss-mdns"),
            "fedora": ("avahi", "nss-mdns"),
            "arch": ("avahi", "nss-mdns"),
            "suse": ("avahi", "nss-mdns"),
            "alpine": ("avahi", ),
        }.get(distro, ("avahi-daemon", ))
        pkg_remove(distro, *_mdns_pkgs)
        info("avahi-daemon removed.")

    print(f"\n{C.BOLD}{C.GREEN}Uninstall complete.{C.NC}\n")


# =============================================================================
# REPO MANAGEMENT (standard-repos / china-repos)
# =============================================================================
def _apt_backup_paths():
    """Return list of (backup, original) tuples for apt sources."""
    pairs = []
    sl = Path("/etc/apt/sources.list.bak")
    if sl.exists():
        pairs.append((sl, Path("/etc/apt/sources.list")))
    sld = Path("/etc/apt/sources.list.d")
    if sld.exists():
        for bak in sld.glob("*.bak"):
            pairs.append((bak, bak.with_suffix("")))
        # Also check .disabled files from the installer
        for dis in sld.glob("*.disabled"):
            orig = Path(str(dis).replace(".disabled", ""))
            pairs.append((dis, orig))
    return pairs


def do_standard_repos():
    """Restore original system package repos from backups made by the installer."""
    banner("Restore system repos", C.CYAN)
    distro = detect_distro()

    if distro == "debian":
        pairs = _apt_backup_paths()
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
            orig = bak.with_suffix("")
            bak.rename(orig)
            info(f"Restored {orig.name}")
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


def _write_china_repos_apt():
    """Switch apt sources to Aliyun."""
    distro_id = ""
    codename = ""
    if Path("/etc/os-release").exists():
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("ID="):
                distro_id = line.split("=", 1)[1].strip().strip('"')
            if line.startswith("VERSION_CODENAME="):
                codename = line.split("=", 1)[1].strip().strip('"')

    if not codename:
        die("Could not determine VERSION_CODENAME from /etc/os-release.")

    # Back up existing sources
    sl = Path("/etc/apt/sources.list")
    if sl.exists() and not Path("/etc/apt/sources.list.bak").exists():
        shutil.copy2(sl, sl.with_suffix(".bak"))
    sld = Path("/etc/apt/sources.list.d")
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
            f"Types: deb\n"
            f"URIs: https://mirrors.aliyun.com/ubuntu\n"
            f"Suites: {codename} {codename}-updates {codename}-security\n"
            f"Components: main restricted universe multiverse\n"
            f"Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        )
    elif distro_id == "raspbian":
        aliyun.write_text(
            f"Types: deb\n"
            f"URIs: https://mirrors.aliyun.com/raspbian/raspbian\n"
            f"Suites: {codename}\n"
            f"Components: main contrib non-free non-free-firmware rpi\n"
            f"Signed-By: /usr/share/keyrings/raspbian-archive-keyring.gpg\n"
        )
    else:
        aliyun.write_text(
            f"Types: deb\n"
            f"URIs: https://mirrors.aliyun.com/debian\n"
            f"Suites: {codename} {codename}-updates\n"
            f"Components: main contrib non-free non-free-firmware\n"
            f"Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n"
            f"\n"
            f"Types: deb\n"
            f"URIs: https://mirrors.aliyun.com/debian-security\n"
            f"Suites: {codename}-security\n"
            f"Components: main contrib non-free non-free-firmware\n"
            f"Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n"
        )
    info("Wrote aliyun-system.sources")


def do_china_repos():
    """Switch system package repos to Aliyun Chinese mirrors."""
    banner("Switch to Aliyun mirrors", C.CYAN)
    distro = detect_distro()

    if distro == "debian":
        step("Switching apt sources to Aliyun")
        _write_china_repos_apt()
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
            txt = re.sub(
                r'^#baseurl=http://download\.example/pub/fedora/linux',
                'baseurl=https://mirrors.aliyun.com/fedora',
                txt, flags=re.MULTILINE)
            f.write_text(txt)
            info(f"Updated {f.name}")
        # Disable repos with no Aliyun mirror
        for name in ["fedora-cisco-openh264", "fedora-updates-testing",
                     "fedora-modular", "fedora-updates-modular"]:
            f = Path(f"/etc/yum.repos.d/{name}.repo")
            if f.exists():
                txt = f.read_text()
                f.write_text(re.sub(r'^enabled=1', 'enabled=0',
                                    txt, flags=re.MULTILINE))
        info("System repos switched to Aliyun. Run 'standard-repos' to undo.")

    elif distro == "arch":
        # Detect arch32 (no Aliyun mirror — use archlinux32 mirrors reachable from China)
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
    "install": do_install,
    "uninstall": do_uninstall,
    "configure": do_configure,
    "refresh": do_refresh,
    "refresh-cron": do_refresh_cron,
    "status": do_status,
    "logs": do_logs,
    "restart": do_restart,
    "update": do_update,
    "hostname": do_set_hostname,
    "test": do_test,
    "debug": do_debug,
    "export": do_export,
    "standard-repos": do_standard_repos,
    "china-repos": do_china_repos,
    "system": do_system,
}

# Commands that accept an optional second argument
_TAKES_ARG = {"hostname", "logs", "restart", "debug", "export", "test", "system"}


def main():
    if os.geteuid() != 0:
        print("Error: must be run as root.")
        print("       sudo ./pimiproxy [command]")
        sys.exit(1)

    # Warn if invoked directly instead of through the wrapper. The wrapper
    # sets up environment, handles path resolution for symlinked installs,
    # and is the supported entry point.
    if os.environ.get("PIMIPROXY_VIA_WRAPPER") != "1":
        print(f"{C.YELLOW}  ⚠{C.NC}  pimiproxy-core.py should be run via the wrapper:")
        print(f"     {C.BOLD}sudo pimiproxy {' '.join(sys.argv[1:]) or '<command>'}{C.NC}")
        print(f"     (or {C.DIM}sudo /opt/pimiproxy/pimiproxy <command>{C.NC})")
        print()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    cmd = args[0] if args else "install"
    if cmd not in COMMANDS:
        simple = "|".join(k for k in COMMANDS if k not in _TAKES_ARG)
        print(f"Usage: sudo ./pimiproxy [{simple}]")
        print("       sudo ./pimiproxy logs     [mihomo|dashboard]")
        print("       sudo ./pimiproxy restart  [mihomo|dashboard]")
        print("       sudo ./pimiproxy hostname <new-hostname>")
        print("       sudo ./pimiproxy test     [--url URL]")
        print("       sudo ./pimiproxy debug    [url]")
        sys.exit(1)
    try:
        if cmd == "install":
            do_install()
        else:
            COMMANDS[cmd]()
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
