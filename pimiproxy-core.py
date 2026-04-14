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
import itertools
from pathlib import Path

# =============================================================================
# REQUIRE ROOT
# =============================================================================
if os.geteuid() != 0:
    sys.exit(
        "Run as root:  sudo python3 pimiproxy.py [install|uninstall|configure]"
    )


# =============================================================================
# COLOUR HELPERS
# =============================================================================
class C:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    CYAN = "\033[0;36m"
    YELLOW = "\033[1;33m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    NC = "\033[0m"


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
    """Return s as a safely double-quoted YAML scalar value."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def confirm(msg, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"{C.YELLOW}  ?{C.NC}  {msg} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw == "y"


def ask(prompt, default=""):
    hint = f" [{default}]" if default else ""
    raw = input(f"  {prompt}{hint}: ").strip()
    return raw if raw else default


def ask_secret(prompt, default=""):
    import getpass
    hint = f" [{default}]" if default else ""
    raw = getpass.getpass(f"  {prompt}{hint}: ").strip()
    return raw if raw else default


def choose(prompt, options, default=1):
    """Present a numbered menu, return the chosen index (1-based)."""
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
# SPINNER  (animated progress for silent long-running operations)
# =============================================================================
class Spinner:
    """
    Context manager that shows a braille-dot spinner on the current line
    while a silent long-running operation is in progress.  When the block
    exits the spinner line is erased so the next info()/warn() prints clean.

    Usage:
        with Spinner("Updating package lists"):
            run(["apt-get", "update", "-qq"], silent=True)
        info("Package lists updated.")
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
        # Erase the spinner line so the next print starts on a clean line
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
    Return True if the CPU advertises hardware AES acceleration.

    Works on both ARM (/proc/cpuinfo Features: ... aes ...) and x86_64
    (/proc/cpuinfo flags: ... aes ...).  Without hardware AES, all proxy
    encryption runs in software and latency on heavy traffic will be higher.
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
    return run(["hostname"], capture=True).stdout.strip()


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
    Soft DHCP renewal — sends a DHCPREQUEST with the new hostname in option 12
    so the router updates its hostname→IP mapping without releasing the lease.
    Detects the default-route interface and the available DHCP client.
    Safe over SSH: no release, so the IP stays the same.
    """
    # Find the interface carrying the default route
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
        warn(
            "Could not detect default network interface — skipping DHCP renew."
        )
        return

    with Spinner(f"Renewing DHCP lease on {iface}"):
        if shutil.which("dhcpcd"):
            r = run(["dhcpcd", "--rebind", iface], check=False, silent=True)
        elif shutil.which("dhclient"):
            r = run(["dhclient", iface], check=False, silent=True)
        elif shutil.which("nmcli"):
            r = run(["nmcli", "device", "reapply", iface],
                    check=False,
                    silent=True)
        else:
            warn("No supported DHCP client found (dhcpcd/dhclient/nmcli).")
            return

    if r.returncode == 0:
        info(f"DHCP renewed — router should pick up the new hostname shortly.")
    else:
        warn(
            "DHCP renew returned non-zero — router may not update immediately."
        )


def do_set_hostname():
    """Subcommand: pimiproxy-core.py hostname <new-hostname>"""
    if len(sys.argv) < 3 or not sys.argv[2].strip():
        die("Usage: sudo python3 pimiproxy-core.py hostname <new-hostname>")
    name = sys.argv[2].strip()
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


# =============================================================================
# SUBSCRIPTION HELPERS
# =============================================================================
def extract_yaml_blocks(content: str) -> dict:
    """
    Extract top-level YAML blocks by name from a Clash/Mihomo config.
    Returns a dict mapping block name -> raw text string for the blocks:
    'proxies', 'proxy-groups', 'rules'.
    Works whether the URL returns a full config or a proxies-only list.
    """
    target_keys = {"proxies", "proxy-groups", "rules"}
    lines = content.splitlines(keepends=True)
    blocks = {}
    current_key = None
    current_start = None

    for i, line in enumerate(lines):
        # Detect a top-level key: non-indented, not a list item, not a comment
        if (line and not line[0].isspace() and not line.startswith('#')
                and not line.startswith('-') and ':' in line):
            key = line.split(':')[0].strip()
            # Save the block we were accumulating
            if current_key in target_keys:
                blocks[current_key] = "".join(lines[current_start:i]).rstrip()
            current_key = key if key in target_keys else None
            current_start = i if current_key else None

    # Save the final block
    if current_key in target_keys:
        blocks[current_key] = "".join(lines[current_start:]).rstrip()

    return blocks


def fetch_subscription(url: str) -> dict:
    """
    Fetch a Clash/Mihomo subscription URL and extract the proxies,
    proxy-groups, and rules blocks. Returns a dict with those keys
    (values are raw YAML text), or an empty dict on failure.
    """
    import urllib.request
    info("Fetching subscription...")
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Clash-Verge/1.0 (clash-verge-rev; mihomo)"
            })
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
        info(
            "Found proxy-groups from provider — will use instead of defaults.")

    if "rules" in blocks:
        # Strip GEOIP rules — they require the MaxMind MMDB database which
        # Mihomo downloads from GitHub on first run. On networks that can't
        # reach GitHub (or during initial setup) this causes a fatal crash.
        # The provider's ruleset already contains explicit IP-CIDR entries
        # for China and other regions, so GEOIP rules are redundant here.
        rules_lines = blocks["rules"].splitlines(keepends=True)
        filtered = [l for l in rules_lines if "GEOIP," not in l.upper()]
        removed = len(rules_lines) - len(filtered)
        if removed:
            info(
                f"Removed {removed} GEOIP rules (require MMDB — covered by IP-CIDR rules)."
            )
        blocks["rules"] = "".join(filtered).rstrip()
        rule_count = blocks["rules"].count("    - ")
        info(
            f"Found {rule_count} rules from provider — will use instead of defaults."
        )

    return blocks


def save_subscription(install_path, url: str) -> bool:
    """
    Fetch the subscription and store the extracted proxies/groups/rules blocks
    into .pimiproxy-config.json so build_config can inject them verbatim.
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
        if any(k in text
               for k in ("fedora", "rhel", "centos", "rocky", "alma")):
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
            run([
                "apt-get", "install", "-y", "-qq", "--no-install-recommends",
                *packages
            ],
                silent=True)
        elif distro == "fedora":
            run(["dnf", "install", "-y", "--quiet", *packages], silent=True)
        elif distro == "arch":
            run(["pacman", "-S", "--noconfirm", "--needed", *packages],
                silent=True)
        elif distro == "suse":
            run(["zypper", "install", "-y", *packages], silent=True)
        elif distro == "alpine":
            run(["apk", "add", "--no-cache", *packages], silent=True)


def pkg_remove(distro: str, *packages):
    """Remove one or more packages using the distro's package manager."""
    with Spinner(f"Removing {', '.join(packages)}"):
        if distro == "debian":
            run(["apt-get", "remove", "-y", *packages],
                silent=True,
                check=False)
        elif distro == "fedora":
            run(["dnf", "remove", "-y", *packages], silent=True, check=False)
        elif distro == "arch":
            run(["pacman", "-R", "--noconfirm", *packages],
                silent=True,
                check=False)
        elif distro == "suse":
            run(["zypper", "remove", "-y", *packages],
                silent=True,
                check=False)
        elif distro == "alpine":
            run(["apk", "del", *packages], silent=True, check=False)


def setup_docker_repo(distro: str):
    """
    Add Docker's official package repo if Docker is not already installed.
    China mirror support is handled by pimiproxy.sh (the bootstrap wrapper),
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
# Builds config as a plain Python dict, writes it as clean YAML manually.
# We write YAML by hand (no PyYAML dependency) to keep the script self-contained
# and avoid install friction — but the structure is validated as a dict first.
# =============================================================================
def build_config(cfg: dict) -> str:
    """
    Build the Mihomo config.yaml content as a string from a structured dict.
    Returns the complete file content.
    """
    api_port = cfg["api_port"]
    api_secret = cfg["api_secret"]
    mixed_port = cfg["mixed_port"]
    subscription_url = cfg.get("subscription_url", "")
    install_dir = cfg["install_dir"]

    lines = []

    def L(s=""):
        lines.append(s)

    L("# Mihomo config — managed by pimiproxy")
    L("# Edit via the pimiproxy web UI or run:  sudo python3 pimiproxy-core.py configure"
      )
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
            # LAN / mDNS — never fake-IP local hostnames
            "*.lan",
            "*.local",
            # Android / Meta / Apple connectivity checks — must resolve to real
            # IPs or the OS reports no internet and throttles/blocks traffic
            "connectivitycheck.gstatic.com",
            "connectivitycheck.android.com",
            "captive.apple.com",
            "*.msftconnecttest.com",
            "*.msftncsi.com",
            # NTP — clocks must resolve to real servers
            "time.android.com",
            "time.facebook.com",
            "time1.google.com",
            "pool.ntp.org",
            "+.pool.ntp.org",
            # STUN / WebRTC — real IPs needed for ICE candidate negotiation
            "stun.l.google.com",
            # Meta / Oculus — Quest OS uses these at the system level
            "+.meta.com",
            "+.oculus.com",
            "+.oculuscdn.com",
            "+.facebook.com",
            "+.fbcdn.net",
            "+.facebook.net",
            "+.fb.me",
            "+.facebook-hardware.com",
            "+.messenger.com",
            "+.instagram.com",
            "+.cdninstagram.com",
            # ── Entries below are commented out ───────────────────────────────
            # Les Mills and Brightcove are listed here for reference but have no
            # effect when using the standard proxy setup (pinasmc:7890), because
            # the Les Mills app (Unity engine) bypasses Android's system proxy
            # entirely and never sends traffic through Mihomo at all.
            #
            # Uncomment these if you configure a device to route through the Pi
            # as its network gateway (transparent proxy mode), at which point
            # Mihomo will see Les Mills traffic and these entries ensure real DNS
            # resolution for DRM license servers and geo-routed CDN endpoints.
            #
            # "+.lesmills.com",
            # "+.lesmillsplus.com",
            # "+.lesmillsondemand.com",
            # "+.brightcove.com",
            # "+.brightcove.net",
            # "+.bcovlive.io",
            # "+.boltdns.net",
    ]:
        L(f'    - "{entry}"')
    L()

    # ── proxies / proxy-groups / rules ────────────────────────────────────────
    # Two-layer merge strategy:
    #   Layer 1 (provider): subscription_proxies/groups/rules_yaml — verbatim
    #                       from the provider, refreshed daily.
    #   Layer 2 (user):     proxy_groups / rules from .pimiproxy-config.json —
    #                       always applied on top regardless of subscription state.
    #
    # Groups: provider groups first, then user groups appended.
    # Rules:  user rules first (highest priority), then provider rules.

    sub_proxies = cfg.get("subscription_proxies_yaml", "")
    sub_groups = cfg.get("subscription_groups_yaml", "")
    sub_rules = cfg.get("subscription_rules_yaml", "")
    user_groups = cfg.get("proxy_groups", [])
    user_rules = cfg.get("rules", [])

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

    # Individual proxy server names for the Quest group proxy list
    _sub_proxy_names = re.findall(
        r'^\s*-\s*name:\s*["\']?([^"\'\n]+?)["\']?\s*$', sub_proxies,
        re.MULTILINE) if sub_proxies else []

    # Subscription group names for the Quest group proxy list
    _sub_group_names = re.findall(
        r'^\s*-\s*name:\s*["\']?([^"\'\n]+?)["\']?\s*$', sub_groups,
        re.MULTILINE) if sub_groups else []

    # ── proxy-groups ──────────────────────────────────────────────────────────
    # Helper: write a single group dict as YAML lines
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
        if "url" in g:
            L(f'    url: {g["url"]}')
        if "interval" in g:
            L(f'    interval: {g["interval"]}')
        if "tolerance" in g:
            L(f'    tolerance: {g["tolerance"]}')

    if sub_groups:
        # Provider groups verbatim, Quest group appended, then user groups
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
        # No provider groups — use user groups + Quest fallback
        if subscription_url and not user_groups:
            user_groups = [{
                "name": "Proxy",
                "type": "select",
                "proxies": ["DIRECT"]
            }]
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
    # Quest domain rules — always injected before provider/user catch-alls
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
        # Detect provider indentation so injected rules match
        rule_indent = "  "
        for line in sub_rules.splitlines():
            s = line.strip()
            if s and s != "rules:" and s.startswith("-"):
                rule_indent = " " * (len(line) - len(line.lstrip()))
                break
        L()
        L("rules:")
        # Order: Quest rules → user rules → provider rules
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
        # No provider rules — use user rules or minimal defaults
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


def build_clash_system() -> str:
    # How this works with auto-route: true
    # ─────────────────────────────────────
    # Mihomo's auto-route installs two key ip rules at startup:
    #   fwmark 666 → lookup main   (exempt marked packets from TUN)
    #   all traffic → lookup TUN table  (everything else enters TUN)
    #
    # clash-system off (DEFAULT — Pi traffic goes direct):
    #   iptables mangle OUTPUT marks every packet the Pi itself originates
    #   with fwmark 666.  Mihomo's ip rule then exempts them from TUN →
    #   Pi gets direct internet.
    #   Forwarded packets (Quest 3 etc.) go through the FORWARD chain, NOT
    #   OUTPUT → never marked → hit Mihomo's TUN table → proxied. ✓
    #
    # clash-system on (Pi routes through proxy too):
    #   Remove the iptables OUTPUT mark rule.  Pi packets no longer carry
    #   fwmark 666 → fall through to Mihomo's TUN table → proxied.
    #   Forwarded packets: unchanged → proxied. ✓
    #
    # Persisted across reboots by pimiproxy-bypass.service which runs
    # clash-system off once after network is up.

    lines = []

    def L(s=""):
        lines.append(s)

    L("#!/usr/bin/env bash")
    L("# clash-system — control whether the SBC's own traffic routes through Mihomo"
      )
    L("# Forwarded traffic (e.g. Quest 3 using Pi as gateway) always uses the proxy."
      )
    L()
    L("RED=$'\\033[0;31m'; GREEN=$'\\033[0;32m'; YELLOW=$'\\033[1;33m'; NC=$'\\033[0m'"
      )
    L()
    L("[[ $EUID -eq 0 ]] || exec sudo \"$0\" \"$@\"")
    L()
    L("MARK=666")
    L()
    L("iface_up()   { [[ -d /sys/class/net/mihomo ]]; }")
    L("bypass_set() { iptables -t mangle -C OUTPUT -j MARK --set-mark $MARK 2>/dev/null; }"
      )
    L()
    L("case \"${1:-}\" in")
    L("  on)")
    L("    iface_up || {")
    L("      echo -e \"${RED}✗${NC} mihomo TUN not found — is the container running?\""
      )
    L("      exit 1")
    L("    }")
    L("    iptables -t mangle -D OUTPUT -j MARK --set-mark $MARK 2>/dev/null || true"
      )
    L("    touch /run/pimiproxy-proxy-on 2>/dev/null || true")
    L("    echo -e \"System proxy: ${GREEN}ON${NC}  (Pi traffic → Mihomo)\"")
    L("    ;;")
    L("  off)")
    L("    bypass_set || iptables -t mangle -A OUTPUT -j MARK --set-mark $MARK"
      )
    L("    rm -f /run/pimiproxy-proxy-on")
    L("    echo -e \"System proxy: ${YELLOW}OFF${NC} (Pi traffic → direct, forwarded clients → Mihomo)\""
      )
    L("    ;;")
    L("  status)")
    L("    if bypass_set; then")
    L("      echo -e \"System proxy: ${YELLOW}OFF${NC} (Pi direct)\"")
    L("    else")
    L("      echo -e \"System proxy: ${GREEN}ON${NC}  (Pi proxied)\"")
    L("    fi")
    L("    iface_up \\")
    L("      && echo -e \"  TUN interface: ${GREEN}up${NC}\" \\")
    L("      || echo -e \"  TUN interface: ${RED}down${NC} (container not running?)\""
      )
    L("    ;;")
    L("  restart)")
    L("    \"$0\" off; sleep 1; \"$0\" on")
    L("    ;;")
    L("  *)")
    L("    echo 'Usage: clash-system [on|off|status|restart]'")
    L("    echo '  off  — device uses direct internet (default); gateway clients still proxied'"
      )
    L("    echo '  on   — device traffic also routes through Mihomo'")
    L("    exit 1")
    L("    ;;")
    L("esac")

    return "\n".join(lines)


BYPASS_SERVICE_PATH = Path("/etc/systemd/system/pimiproxy-bypass.service")


def build_bypass_service() -> str:
    lines = []

    def L(s=""):
        lines.append(s)

    L("[Unit]")
    L("Description=pimiproxy — restore device traffic bypass after network is up")
    L("Documentation=https://github.com/your-repo/pimiproxy")
    L("After=network.target")
    L()
    L("[Service]")
    L("Type=oneshot")
    L("ExecStart=/usr/local/bin/clash-system off")
    L("RemainAfterExit=yes")
    L()
    L("[Install]")
    L("WantedBy=multi-user.target")

    return "\n".join(lines)


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
    L("# ── Proxy engine (mihomo TUN interface) ─────────────────────────────")
    L("if [[ -d /sys/class/net/mihomo ]]; then")
    L("  printf '  Proxy engine    %s\\n' \"${_G}●  running${_N}\"")
    L("else")
    L("  printf '  Proxy engine    %s\\n' \"${_R}●  stopped${_N}\"")
    L("fi")
    L()
    L("# ── System proxy (state file written by clash-system on/off) ─────────"
      )
    L("if [[ -f /run/pimiproxy-proxy-on ]]; then")
    L("  printf '  System proxy    %s\\n' \"${_G}●  ON${_N}\"")
    L("else")
    L("  printf '  System proxy    %s\\n' \"${_Y}○  OFF${_N}\"")
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
    L('_PI_IP=$(hostname -I 2>/dev/null | awk \'{print $1}\')')
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
    L('printf "  %s\\n" "${_D}clash-system [on|off|status|restart]${_N}"')
    L('printf "  %s\\n" "${_D}pimiproxy-core.py [status|logs|configure|debug]${_N}"')
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

    # ── clash-system ──────────────────────────────────────────────────────────
    step("System proxy (clash-system)")
    cs = Path("/usr/local/bin/clash-system")
    if cs.exists():
        run([str(cs), "status"], check=False)
    else:
        warn("clash-system not installed.")

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
        warn("  sudo python3 pimiproxy-core.py logs")

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
    print()
    print(f"  Dashboard  ->  http://{hostname}.lan:{dash_port}")
    print(f"  UI         ->  http://{hostname}.lan:{ui_port}")
    print(f"  API        ->  http://{hostname}.lan:{api_port}")
    print(f"  Proxy      ->  0.0.0.0:{mixed_port}  (HTTP/SOCKS5)")
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
# UPDATE
# =============================================================================
def do_update():
    meta = read_meta()
    install_dir = Path(meta["install_dir"])
    compose = (meta["compose_bin"], meta["compose_sub"])
    arch = meta.get("arch") or _host_arch()
    dash_type = meta.get("dashboard_type", "metacubexd")

    proxy_active = Path("/run/pimiproxy-proxy-on").exists()

    banner("pimiproxy update", C.CYAN)
    print()
    if proxy_active:
        print("  System proxy is ON — registry pulls will route through Mihomo.")
    else:
        print("  Place updated .tar.gz archives in docker-images/ before running")
        print("  this command. Archives are loaded from disk if present; registry")
        print("  pull is attempted only as a last resort (may fail without a proxy).")
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
                if proxy_active:
                    warn(f"Pull failed even with system proxy. Check Mihomo logs.")
                else:
                    warn(f"Pull failed. Enable system proxy or place {archive_name} in docker-images/ and retry.")

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

        print(f"\n{C.BOLD}── Configure pimiproxy ─────────────────────{C.NC}")
        print("  1) Manage provider URL")
        print("  2) Manage proxy groups")
        print("  3) Manage rules")
        print("  4) Show current config")
        print("  5) Refresh provider now")
        print("  6) Change API secret")
        print("  7) Change ports")
        print("  8) Switch dashboard")
        print("  9) Save and apply")
        print(" 10) Exit without saving")
        print(f" 11) Enable / rebuild web management UI  [{_ui_status}]")
        print()
        choice = input("  Selection [1-11]: ").strip()

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
                if save_subscription(install_dir, url):
                    # Reload cfg with fresh subscription blocks and rebuild config
                    cfg = json.loads(ext_cfg_path.read_text())
                    cfg_path.write_text(build_config(cfg))
                    info("config.yaml rebuilt.")
                    compose_run(compose,
                                "restart",
                                "mihomo",
                                cwd=install_dir,
                                silent=False)
                    info("Mihomo restarted.")

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

            # Always write config.yaml (covers secret, api_port, mixed_port)
            cfg_path.write_text(build_config(cfg))
            ext_cfg_path.write_text(json.dumps(cfg, indent=2))
            info("config.yaml written.")

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
                    compose_run(compose,
                                "restart",
                                "mihomo",
                                cwd=install_dir,
                                silent=False)
                    info("Mihomo restarted.")
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
            ext_cfg_path.write_text(json.dumps(cfg, indent=2))
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

    if express:
        hostname_new = ask("Hostname", hostname_new)
    else:
        install_dir = ask("Install directory", install_dir)
        hostname_new = ask("Hostname", hostname_new)
        dashboard_port = ask("Dashboard port", dashboard_port)
        ui_port = ask("Management UI port", ui_port)
        api_port = ask("Mihomo API port", api_port)
        mixed_port = ask("Mixed proxy port", mixed_port)
        api_secret = ask_secret("API secret", api_secret)

    print()
    print("  ── Provider URL " + "─" * 26)
    print("  If your proxy provider gave you a Clash/Mihomo API URL, enter")
    print("  it here. It typically looks like:")
    print("    https://provider.example.com/api?token=abc123")
    print()
    print("  This must be a Clash API URL — not a regular VPN config file,")
    print("  not an app download link. If unsure, leave blank. You can add")
    print("  it later via the web UI or by running:")
    print("    sudo python3 pimiproxy-core.py configure")
    print("  " + "─" * 42)
    print()
    subscription_url = input("  Provider URL [leave blank to skip]: ").strip()

    if not express:
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

    # ── iptables: required by clash-system on distros that don't bundle it ──────
    if not shutil.which("iptables"):
        _ipt_pkg = {
            "fedora": "iptables",
            "arch":   "iptables",
            "suse":   "iptables",
        }.get(distro)
        if _ipt_pkg:
            with Spinner("Installing iptables"):
                pkg_install(distro, _ipt_pkg)
            info("iptables installed.")

    # ── firewalld: trust docker0 so containers can reach the network ──────────
    # firewalld is the default on Fedora/RHEL and is sometimes present on other
    # distros (openSUSE, Arch with firewalld installed). Handle it wherever it
    # is active, not just on Fedora.
    if shutil.which("firewall-cmd") and run_ok(
            ["systemctl", "is-active", "--quiet", "firewalld"]):
        with Spinner("Configuring firewalld for Docker"):
            run(["firewall-cmd", "--permanent", "--zone=trusted",
                 "--add-interface=docker0"], silent=True, check=False)
            run(["firewall-cmd", "--permanent", "--zone=trusted",
                 "--add-masquerade"], silent=True, check=False)
            run(["firewall-cmd", "--reload"], silent=True, check=False)
        info("firewalld: docker0 added to trusted zone.")

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
            warn("Run after install:  sudo python3 pimiproxy-core.py refresh")

    cfg_text = build_config(cfg)
    (install_path / "config" / "config.yaml").write_text(cfg_text)
    (install_path / ".pimiproxy-config.json").write_text(
        json.dumps(cfg, indent=2))
    info("config.yaml written.")

    # ── copy script + install daily refresh cron ──────────────────────────────
    step("Refresh script")

    script_dest = install_path / "pimiproxy-core.py"
    shutil.copy2(__file__, script_dest)
    info(f"Script copied to {script_dest}.")

    cron_path = Path("/etc/cron.daily/pimiproxy-refresh")
    cron_lines = [
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

    # ── clash-system ──────────────────────────────────────────────────────────
    step("clash-system utility")

    cs_path = Path("/usr/local/bin/clash-system")
    cs_path.write_text(build_clash_system())
    cs_path.chmod(0o755)
    info("/usr/local/bin/clash-system installed.")

    # ── bypass service (Pi traffic goes direct by default after reboot) ───────
    step("Pi traffic bypass service")

    BYPASS_SERVICE_PATH.write_text(build_bypass_service())
    run(["systemctl", "daemon-reload"], silent=True)
    run(["systemctl", "enable", "pimiproxy-bypass"], silent=True)
    run(["systemctl", "start", "pimiproxy-bypass"], silent=True, check=False)
    info("pimiproxy-bypass.service installed and enabled.")

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

    # ── login banner ──────────────────────────────────────────────────────────
    step("Login banner")

    BANNER_PATH.write_text(build_banner(cfg, meta))
    BANNER_PATH.chmod(0o644)
    info(f"Login banner installed ({BANNER_PATH}).")

    # ── launch ────────────────────────────────────────────────────────────────
    step("Starting services")

    compose_run(compose, "up", "-d", "--force-recreate", cwd=install_path)
    info("Containers started.")

    # ── summary ───────────────────────────────────────────────────────────────
    _r = run(["hostname", "-I"], capture=True, check=False)
    lan_ip = (_r.stdout.strip().split() or ["<pi-ip>"])[0]

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

    SEP = f"  {'─'*42}"
    B, G, Y, D, N = C.BOLD, C.GREEN, C.YELLOW, C.DIM, C.NC

    C2 = C.CYAN
    print()
    print(f"{B}{G}  pimiproxy — installation complete{N}")
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
    print(f"  {C2}Dashboard{N}  →  http://{hostname_new}.lan:{dashboard_port}")
    print(f"             →  http://{lan_ip}:{dashboard_port}")
    print(f"  {C2}UI{N}         →  http://{hostname_new}.lan:{ui_port}")
    print(f"  {C2}API{N}        →  http://{hostname_new}.lan:{api_port}")
    print(f"  {B}{C2}Proxy{N}      →  {B}{hostname_new}.lan:{mixed_port}  (HTTP/SOCKS5){N}")
    print(SEP)
    print(f"  {D}clash-system [on|off|status|restart]{N}")
    print(f"  {D}pimiproxy-core.py [configure|status|logs|debug]{N}")
    print(SEP)
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
    if not save_subscription(install_dir, url):
        die("Provider refresh failed.")

    # Reload cfg after save_subscription updated it, rebuild config.yaml
    cfg = json.loads(ext_cfg_path.read_text())
    cfg_path = install_dir / "config" / "config.yaml"
    cfg_path.write_text(build_config(cfg))
    info("config.yaml rebuilt.")

    # Restart Mihomo to pick up the new inline proxies
    compose = (meta["compose_bin"], meta["compose_sub"])
    compose_run(compose, "restart", "mihomo", cwd=install_dir, silent=False)
    info("Mihomo restarted.")

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

    # ── routing rules ─────────────────────────────────────────────────────────
    step("Clearing routing rules")

    cs = Path("/usr/local/bin/clash-system")
    if cs.exists():
        run([str(cs), "off"], check=False, silent=True)
    info("Routing rules cleared.")

    # ── files ─────────────────────────────────────────────────────────────────
    step("Removing files")

    ui_secret = install_dir / "ui-secret.txt"
    if ui_secret.exists():
        ui_secret.unlink()
        info("Removed ui-secret.txt")

    if install_dir.exists():
        shutil.rmtree(install_dir)
        info(f"Removed {install_dir}")

    if cs.exists():
        cs.unlink()
        info("Removed /usr/local/bin/clash-system")

    cron_path = Path("/etc/cron.daily/pimiproxy-refresh")
    if cron_path.exists():
        cron_path.unlink()
        info("Removed daily refresh cron.")

    if BANNER_PATH.exists():
        BANNER_PATH.unlink()
        info(f"Removed login banner ({BANNER_PATH}).")

    sysctl_conf = Path("/etc/sysctl.d/99-pimiproxy.conf")
    if sysctl_conf.exists():
        sysctl_conf.unlink()
        run(["sysctl", "-w", "net.ipv4.ip_forward=0"],
            check=False,
            silent=True)
        info("Removed IP forwarding config and disabled forwarding.")

    if BYPASS_SERVICE_PATH.exists():
        run(["systemctl", "stop", "pimiproxy-bypass"],
            check=False,
            silent=True)
        run(["systemctl", "disable", "pimiproxy-bypass"],
            check=False,
            silent=True)
        BYPASS_SERVICE_PATH.unlink()
        run(["systemctl", "daemon-reload"], silent=True, check=False)
        # Clean up any leftover iptables mark rule
        run([
            "iptables", "-t", "mangle", "-D", "OUTPUT", "-j", "MARK",
            "--set-mark", "666"
        ],
            check=False,
            silent=True)
        info("Removed bypass service and cleared iptables rules.")

    # ── hostname ──────────────────────────────────────────────────────────────
    step("Hostname")

    current = run(["hostname"], capture=True).stdout.strip()
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
    "debug": do_debug,
    "export": do_export,
}

# Commands that accept an optional second argument
_TAKES_ARG = {"hostname", "logs", "restart", "debug", "export"}


def main():
    if os.geteuid() != 0:
        print("Error: pimiproxy-core.py must be run as root.")
        print("       sudo python3 pimiproxy-core.py [command]")
        sys.exit(1)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    cmd = args[0] if args else "install"
    if cmd not in COMMANDS:
        simple = "|".join(k for k in COMMANDS if k not in _TAKES_ARG)
        print(f"Usage: sudo python3 pimiproxy-core.py [{simple}]")
        print(
            "       sudo python3 pimiproxy-core.py logs     [mihomo|dashboard]"
        )
        print(
            "       sudo python3 pimiproxy-core.py restart  [mihomo|dashboard]"
        )
        print("       sudo python3 pimiproxy-core.py hostname <new-hostname>")
        print("       sudo python3 pimiproxy-core.py debug    [url]")
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
