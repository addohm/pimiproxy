"""
pimiproxy_common — helpers shared by the full (pimiproxy-core.py) and
Docker-free (pimiproxy-ultralite-core.py) installers.

Everything here is deliberately variant-agnostic: UI helpers, subprocess
wrappers, hostname/distro/package utilities, and subscription fetching.
Variant-specific logic (Docker vs systemd, image vs binary handling,
build_config, build_banner, do_install, etc.) stays in the core scripts.

Must be importable from the same directory as each core script, so
installers and the release build script must copy this file alongside
the wrapper + core.
"""

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path


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


SEP_LINE = "─" * 42


def banner(msg, colour=C.CYAN):
    print(f"\n{colour}{C.BOLD}{SEP_LINE}")
    print(f"  {msg}")
    print(f"{SEP_LINE}{C.NC}\n")


def die(msg):
    print(f"{C.RED}  ✖  {msg}{C.NC}", file=sys.stderr)
    sys.exit(1)


def yaml_quote(s: str) -> str:
    """Return s as a safely double-quoted YAML scalar value."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def confirm(msg, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"{C.YELLOW}  ?{C.NC}  {msg} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw == "y"


def ask(prompt, default=""):
    hint = f" {C.DIM}[{default}]{C.NC}" if default else ""
    raw = input(f"  {C.BOLD}{prompt}{C.NC}{hint}: ").strip()
    return raw if raw else default


def ask_secret(prompt, default=""):
    import getpass
    hint = f" {C.DIM}[{default}]{C.NC}" if default else ""
    raw = getpass.getpass(f"  {C.BOLD}{prompt}{C.NC}{hint}: ").strip()
    return raw if raw else default


def choose(prompt, options, default=1):
    """Present a numbered menu, return the chosen index (1-based)."""
    print(f"  {C.BOLD}{prompt}{C.NC}")
    for i, opt in enumerate(options, 1):
        marker = f" {C.DIM}(default){C.NC}" if i == default else ""
        print(f"    {C.CYAN}{i}){C.NC} {opt}{marker}")
    while True:
        raw = input(f"  {C.BOLD}Selection{C.NC} {C.DIM}[1-{len(options)}]{C.NC}: ").strip()
        if raw == "":
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        warn(f"Please enter a number between 1 and {len(options)}.")


# =============================================================================
# SPINNER
# =============================================================================
class Spinner:
    """
    Context manager that shows a braille-dot spinner while a silent long-
    running operation is in progress. When the block exits the spinner line
    is erased so the next info()/warn() prints clean.
    """
    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, msg: str):
        self.msg = msg
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._spin, daemon=True)

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
    """Run a shell command. cmd is a list or string."""
    kwargs = dict(check=check)
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    elif silent:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    if isinstance(cmd, str):
        kwargs["shell"] = True
    if cwd is not None:
        kwargs["cwd"] = cwd
    return subprocess.run(cmd, **kwargs)


def run_ok(cmd):
    """Return True if command exits 0, False otherwise."""
    return run(cmd, check=False, silent=True).returncode == 0


def has_aes_hardware() -> bool:
    """
    Return True if the CPU advertises hardware AES acceleration (ARM Features
    line or x86_64 flags line in /proc/cpuinfo). Without AES, proxy encryption
    runs in software and heavy-traffic latency suffers.
    """
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

# OS default hostnames that indicate the device hasn't been renamed yet
_DEFAULT_HOSTNAMES = {"raspberrypi", "localhost", "debian", "ubuntu", "alarm"}


def current_hostname() -> str:
    if shutil.which("hostname"):
        return run(["hostname"], capture=True).stdout.strip()
    # Arch Linux doesn't ship hostname; fall back to hostnamectl or /etc/hostname
    if shutil.which("hostnamectl"):
        r = run(["hostnamectl", "--static"], capture=True, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    try:
        return Path("/etc/hostname").read_text().strip()
    except FileNotFoundError:
        return "localhost"


def set_hostname(new_name: str):
    """Set the system hostname and update /etc/hosts and cloud-init."""
    old = current_hostname()

    run(["hostnamectl", "set-hostname", new_name])

    hosts = Path("/etc/hosts")
    txt = hosts.read_text()
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
    """
    Soft DHCP renewal — sends DHCPREQUEST with the new hostname in option 12
    so the router updates its hostname→IP mapping without releasing the lease.
    Safe over SSH: no release, so the IP stays the same.
    """
    result = run(["ip", "route", "show", "default"], capture=True, check=False)
    iface = None
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


# =============================================================================
# SUBSCRIPTION HELPERS
# =============================================================================
def extract_yaml_blocks(content: str) -> dict:
    """
    Extract top-level YAML blocks by name from a Clash/Mihomo config.
    Returns a dict mapping block name -> raw text for 'proxies', 'proxy-groups',
    'rules'. Works whether the URL returns a full config or a proxies-only list.
    """
    target_keys = {"proxies", "proxy-groups", "rules"}
    lines = content.splitlines(keepends=True)
    blocks = {}
    current_key = None
    current_start = None

    for i, line in enumerate(lines):
        if (line and not line[0].isspace() and not line.startswith("#")
                and not line.startswith("-") and ":" in line):
            key = line.split(":")[0].strip()
            if current_key in target_keys:
                blocks[current_key] = "".join(lines[current_start:i]).rstrip()
            current_key = key if key in target_keys else None
            current_start = i if current_key else None

    if current_key in target_keys:
        blocks[current_key] = "".join(lines[current_start:]).rstrip()

    return blocks


def fetch_subscription(url: str) -> dict:
    """
    Fetch a Clash/Mihomo subscription URL and return the proxies/proxy-groups/
    rules blocks as raw YAML text. Empty dict on failure.
    """
    import urllib.request
    info("Fetching subscription...")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Clash-Verge/1.0 (clash-verge-rev; mihomo)"})
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
        # Strip GEOIP rules — they need the MaxMind MMDB which Mihomo tries
        # to download from GitHub on first run. On networks that can't reach
        # GitHub this is fatal, and the provider's ruleset already has
        # explicit IP-CIDR entries for China/other regions.
        rules_lines = blocks["rules"].splitlines(keepends=True)
        filtered = [l for l in rules_lines if "GEOIP," not in l.upper()]
        removed = len(rules_lines) - len(filtered)
        if removed:
            info(f"Removed {removed} GEOIP rules (require MMDB — covered by IP-CIDR rules).")
        blocks["rules"] = "".join(filtered).rstrip()
        rule_count = blocks["rules"].count("    - ")
        info(f"Found {rule_count} rules from provider — will use instead of defaults.")

    return blocks


def save_subscription(install_path, url: str) -> bool:
    """
    Fetch the subscription and store the extracted blocks into
    .pimiproxy-config.json so build_config can inject them verbatim.
    Returns True on success.
    """
    blocks = fetch_subscription(url)
    if not blocks:
        return False

    ext_cfg_path = install_path / ".pimiproxy-config.json"
    cfg = json.loads(ext_cfg_path.read_text()) if ext_cfg_path.exists() else {}
    cfg["subscription_proxies_yaml"] = blocks.get("proxies", "")
    cfg["subscription_groups_yaml"] = blocks.get("proxy-groups", "")
    cfg["subscription_rules_yaml"] = blocks.get("rules", "")
    ext_cfg_path.write_text(json.dumps(cfg, indent=2))
    return True


# =============================================================================
# DISTRO DETECTION & PACKAGE MANAGEMENT
# =============================================================================
def detect_distro() -> str:
    """
    Return a distro family string: 'debian', 'fedora', 'arch', 'suse', 'alpine'.
    Falls back to 'debian' if unknown.
    """
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
    if shutil.which("apt-get"):
        return "debian"
    if shutil.which("dnf") or shutil.which("yum"):
        return "fedora"
    if shutil.which("pacman"):
        return "arch"
    if shutil.which("zypper"):
        return "suse"
    if shutil.which("apk"):
        return "alpine"
    return "debian"


def pkg_update(distro: str):
    """Refresh package lists for the given distro family."""
    if distro == "debian":
        run(["apt-get", "update", "-qq"], silent=True)
    elif distro == "fedora":
        pass  # dnf installs are self-refreshing
    elif distro == "arch":
        run(["pacman", "-Sy", "--noconfirm"], silent=True)
    elif distro == "suse":
        run(["zypper", "refresh"], silent=True)
    elif distro == "alpine":
        run(["apk", "update"], silent=True)


def pkg_install(distro: str, *packages):
    """Install one or more packages using the distro's package manager."""
    with Spinner(f"Installing {', '.join(packages)}"):
        if distro == "debian":
            env = os.environ.copy()
            env["DEBIAN_FRONTEND"] = "noninteractive"
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
    """Remove one or more packages using the distro's package manager."""
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
