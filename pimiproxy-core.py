#!/usr/bin/env python3
"""
pimiproxy — install / uninstall / configure
Raspberry Pi OS (Bookworm/Bullseye) · Debian 11/12
Run as root: sudo python3 pimiproxy.py [install|uninstall|configure]
"""

import os
import re
import sys
import shutil
import subprocess
import textwrap
import json
import threading
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

    warn(f"{name}.lan won't resolve until the Pi renews its DHCP lease.")
    if confirm("Reboot now so the router picks up the new hostname?",
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


def apt_install(*packages):
    with Spinner(f"Installing {', '.join(packages)}"):
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        run([
            "apt-get", "install", "-y", "-qq", "--no-install-recommends",
            *packages
        ],
            silent=True)


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
def load_image(archive: str, fallback: str, arch: str) -> str:
    """
    Load a Docker image from a local .tar.gz archive if present,
    otherwise pull from the registry. Returns the image tag.
    """
    p = Path(archive)
    if p.exists():
        with Spinner(f"Loading {archive} from disk"):
            result = run(f"gunzip -c {archive} | docker load", capture=True)
        output = result.stdout + result.stderr
        for line in output.splitlines():
            if "Loaded image:" in line:
                tag = line.split("Loaded image:")[-1].strip()
                info(f"Loaded: {tag}")
                return tag
        warn(f"Could not parse image tag from {archive} — will pull instead.")

    # docker pull shows its own layer progress — don't silence it
    info(f"Pulling {fallback} ({arch})...")
    result = run(["docker", "pull", "--platform", arch, fallback], check=False)
    if result.returncode != 0:
        die(f"Failed to pull {fallback}. Check network and registry access.")
    return fallback


# =============================================================================
# METADATA  (stored as JSON — no quoting issues, no sourcing issues)
# =============================================================================
META_PATH = Path("/opt/pimiproxy/.pimiproxy-meta.json")
BANNER_PATH = Path("/etc/profile.d/pimiproxy-banner.sh")

# Docker image references
MIHOMO_IMAGE = "ghcr.io/metacubex/mihomo:latest"
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
    L(f"# Edit here or run:  sudo python3 pimiproxy.py configure")
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
    # Strategy:
    #   - If the subscription provided these blocks (fetched by save_subscription),
    #     inject them verbatim — the provider's config is authoritative.
    #   - If only proxies were fetched (no groups/rules from provider), fall back
    #     to pimiproxy-generated simple groups and rules.
    #   - If no subscription at all, use MATCH,DIRECT pass-through.
    #   - User-added proxy_groups / rules (from configure mode) are used only
    #     when no subscription blocks are present.

    sub_proxies = cfg.get("subscription_proxies_yaml", "")
    sub_groups = cfg.get("subscription_groups_yaml", "")
    sub_rules = cfg.get("subscription_rules_yaml", "")

    if sub_proxies:
        # Inline proxies from provider — inject verbatim
        L()
        for line in sub_proxies.splitlines():
            L(line)
        L()
    elif subscription_url:
        # URL set but subscription not yet fetched; placeholder so config is valid
        L()
        L("proxies: []")
        L()

    # Individual proxy server names — used in Quest group so the user can
    # pick any specific server directly, same as every other proxy group.
    _sub_proxy_names = re.findall(
        r'^\s*-\s*name:\s*["\']?([^"\'\n]+?)["\']?\s*$', sub_proxies,
        re.MULTILINE) if sub_proxies else []

    if sub_groups:
        # Proxy groups from provider — inject verbatim, then append Quest group
        L()
        for line in sub_groups.splitlines():
            L(line)
        L('  - name: "🧭 Meta Quest"')
        L('    type: select')
        L('    proxies:')
        L('      - "DIRECT"')
        L('      - "Wannaflix"')
        for name in _sub_proxy_names:
            L(f'      - {yaml_quote(name)}')
        L()
    else:
        # Fall back: user-configured groups or simple generated defaults
        proxy_groups = cfg.get("proxy_groups", [])
        if subscription_url and not proxy_groups:
            proxy_groups = [
                {
                    "name": "Proxy",
                    "type": "select",
                    "proxies": ["DIRECT"]
                },
            ]
        L("proxy-groups:")
        for g in proxy_groups:
            L(f'  - name: "{g["name"]}"')
            L(f'    type: {g["type"]}')
            if "use" in g:
                L("    use:")
                for u in g["use"]:
                    L(f"      - {u}")
            if "proxies" in g:
                L("    proxies:")
                for p in g["proxies"]:
                    L(f"      - {p}")
            if "url" in g:
                L(f'    url: {g["url"]}')
            if "interval" in g:
                L(f'    interval: {g["interval"]}')
            if "tolerance" in g:
                L(f'    tolerance: {g["tolerance"]}')
        L('  - name: "Quest"')
        L('    type: select')
        L('    proxies:')
        L('      - "DIRECT"')
        L('      - "Wannaflix"')
        L()

    # Quest domain rules — always prepended before the subscription catch-all
    # so traffic for these domains actually reaches the Quest group.
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
        # Write rules: header ourselves, inject Quest rules first,
        # then write subscription rules skipping their own rules: header.
        # Detect the indentation the subscription uses so Quest rules match.
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
        for line in sub_rules.splitlines():
            if line.strip() == "rules:":
                continue
            L(line)
        L()
    else:
        # Fall back: user-configured rules or minimal defaults
        rules = cfg.get("rules", [])
        if not rules:
            if subscription_url or cfg.get("proxy_groups"):
                rules = [
                    "DOMAIN-SUFFIX,lan,DIRECT",
                    "DOMAIN-SUFFIX,local,DIRECT",
                    "IP-CIDR,192.168.0.0/16,DIRECT",
                    "IP-CIDR,10.0.0.0/8,DIRECT",
                    "IP-CIDR,172.16.0.0/12,DIRECT",
                    "MATCH,Proxy",
                ]
            else:
                rules = ["MATCH,DIRECT"]
        L("rules:")
        for r in _quest_rules:
            L(f"  - {r}")
        for r in rules:
            L(f"  - {r}")
        L()

    return "\n".join(lines)


def build_compose(cfg: dict) -> str:
    engine = cfg["engine_image"]
    dashboard = cfg["dashboard_image"]
    dash_name = cfg["dash_name"]
    dash_port = cfg["dashboard_port"]

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
    L("# clash-system — control whether the Pi's own traffic routes through Mihomo"
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
    L("    echo -e \"System proxy: ${GREEN}ON${NC}  (Pi traffic → Mihomo)\"")
    L("    ;;")
    L("  off)")
    L("    bypass_set || iptables -t mangle -A OUTPUT -j MARK --set-mark $MARK"
      )
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
    L("    echo '  off  — Pi uses direct internet (default); gateway clients still proxied'"
      )
    L("    echo '  on   — Pi traffic also routes through Mihomo'")
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
    L("Description=pimiproxy — restore Pi traffic bypass after network is up")
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
    L("# ── Proxy engine (mihomo container) ─────────────────────────────────")
    L("if docker inspect -f '{{.State.Running}}' mihomo 2>/dev/null | grep -q true; then"
      )
    L("  printf '  Proxy engine    %s\\n' \"${_G}●  running${_N}\"")
    L("else")
    L("  printf '  Proxy engine    %s\\n' \"${_R}●  stopped${_N}\"")
    L("fi")
    L()
    L("# ── System proxy (clash-system routing rules) ────────────────────────"
      )
    L("if ip rule show 2>/dev/null | grep -q 'priority 101'; then")
    L("  printf '  System proxy    %s\\n' \"${_G}●  ON${_N}\"")
    L("else")
    L("  printf '  System proxy    %s\\n' \"${_R}○  OFF${_N}\"")
    L("fi")
    L()
    L("# ── Proxy count ──────────────────────────────────────────────────────"
      )
    L(f"_COUNT=$(grep -c '- name:' '{cfg_file}' 2>/dev/null || echo 0)")
    L('if [[ "$_COUNT" -gt 0 ]]; then')
    L("  printf '  Proxies loaded  %s\\n' \"${_D}${_COUNT}${_N}\"")
    L("fi")
    L()
    L("# ── AES hardware acceleration (baked in at install time) ─────────────"
      )
    if aes_ok:
        L("  printf '  AES hardware    %s\\n' \"${_G}●  available${_N}\"")
    else:
        L("  printf '  AES hardware    %s\\n' \"${_Y}○  software fallback — higher latency${_N}\""
          )
    L()
    L('printf "%s\\n" "$_S"')
    L(f'printf "  Dashboard  →  %s\\n" "http://{hostname}.lan:{dash_port}"')
    L(f'printf "  API        →  %s\\n" "http://{hostname}.lan:{api_port}"')
    L(f'printf "  Proxy      →  %s\\n" "{hostname}.lan:{mixed_port}  (HTTP/SOCKS5)"'
      )
    L('printf "%s\\n" "$_S"')
    L('printf "  %s\\n" "${_D}clash-system [on|off|status|restart]${_N}"')
    L('printf "  %s\\n" "${_D}pimiproxy-core.py [status|logs|configure|debug]${_N}"'
      )
    L('printf "%s\\n" "$_S"')
    L('printf "  ${_Y}Tip:${_N} Some apps (e.g. Unity VR games) ignore the system\\n"'
      )
    L('printf "  proxy setting. To proxy all traffic from a device, set\\n"')
    L(f'printf "  its WiFi gateway and DNS to this Pi\'s IP instead of the router.\\n"'
      )
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

    # ── subscription ──────────────────────────────────────────────────────────
    step("Subscription")
    ext_cfg_path = install_dir / ".pimiproxy-config.json"
    if ext_cfg_path.exists():
        cfg = json.loads(ext_cfg_path.read_text())
        url = cfg.get("subscription_url", "")
        if url:
            info(f"URL: {url}")
            count = cfg.get("subscription_proxies_yaml", "").count("- name:")
            info(f"Proxies loaded: {count}")
        else:
            info("No subscription URL configured.")
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
    print()
    print(f"  Dashboard  ->  http://{hostname}.lan:{dash_port}")
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
    if service not in {"mihomo", "dashboard"}:
        die(f"Unknown service '{service}'. Use: mihomo  or  dashboard")

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
    if service and service not in {"mihomo", "dashboard"}:
        die(f"Unknown service '{service}'. Use: mihomo, dashboard, or omit for all"
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

    banner("pimiproxy update", C.CYAN)
    step("Pulling latest images")
    compose_run(compose, "pull", cwd=install_dir)

    step("Recreating containers with new images")
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
        print(f"\n{C.BOLD}── Configure pimiproxy ─────────────────────{C.NC}")
        print("  1) Manage subscription URL")
        print("  2) Manage proxy groups")
        print("  3) Manage rules")
        print("  4) Show current config")
        print("  5) Refresh subscription now")
        print("  6) Change API secret")
        print("  7) Change ports")
        print("  8) Switch dashboard")
        print("  9) Save and apply")
        print(" 10) Exit without saving")
        print()
        choice = input("  Selection [1-10]: ").strip()

        # ── subscription URL ─────────────────────────────────────────────────
        if choice == "1":
            current = cfg.get("subscription_url", "")
            print(f"\n  Current subscription URL: {current or '(none)'}")
            print()
            print("  Your provider must give you a Clash/Mihomo API URL.")
            print("  It typically looks like:")
            print("    https://provider.example.com/api?token=abc123")
            print("  Leave blank to clear.")
            print()
            url = input("  Clash API URL: ").strip()
            cfg["subscription_url"] = url
            if url:
                info(f"Subscription URL set.")
            else:
                info("Subscription URL cleared.")
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
                                "No providers configured. Add a subscription URL first."
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

        # ── refresh subscription ─────────────────────────────────────────────
        elif choice == "5":
            url = cfg.get("subscription_url", "")
            if not url:
                warn("No subscription URL configured (option 1).")
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
            cur_api = cfg.get("api_port", meta.get("api_port", "9090"))
            cur_mixed = cfg.get("mixed_port", meta.get("mixed_port", "7890"))
            print(f"\n  {C.BOLD}Current ports:{C.NC}")
            print(f"    Dashboard  port : {cur_dash}")
            print(f"    Mihomo API port : {cur_api}")
            print(f"    Mixed proxy port: {cur_mixed}")
            print()
            new_dash = ask("Dashboard port", cur_dash)
            new_api = ask("Mihomo API port", cur_api)
            new_mixed = ask("Mixed proxy port", cur_mixed)
            if new_dash != cur_dash:
                cfg["dashboard_port"] = new_dash
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
                arch = meta.get("arch", "linux/arm64")
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
            for key in ("dashboard_port", "api_port", "mixed_port",
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
    api_secret = "pimiproxy"
    arch = "linux/arm64"
    dash_choice = 1

    # ── express / advanced ────────────────────────────────────────────────────
    step("Install mode")
    print()
    print("  Express install uses all defaults — only asks for hostname and")
    print(
        "  your Clash API subscription URL. Everything else is pre-configured."
    )
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
        api_port = ask("Mihomo API port", api_port)
        mixed_port = ask("Mixed proxy port", mixed_port)
        api_secret = ask_secret("API secret", api_secret)

    print()
    print("  ── Clash API Subscription URL " + "─" * 12)
    print("  If your proxy provider gave you a Clash/Mihomo API URL, enter")
    print("  it here. It typically looks like:")
    print("    https://provider.example.com/api?token=abc123")
    print()
    print("  This must be a Clash API URL — not a regular VPN config file,")
    print("  not an app download link. If unsure, leave blank. You can add")
    print("  it later by running:  sudo python3 pimiproxy-core.py configure")
    print("  " + "─" * 42)
    print()
    subscription_url = input("  Clash API URL [leave blank to skip]: ").strip()

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

    with Spinner("Updating package lists"):
        run(["apt-get", "update", "-qq"], silent=True)
    info("Package lists updated.")

    apt_install("avahi-daemon", "libnss-mdns")
    with Spinner("Enabling avahi-daemon"):
        run(["systemctl", "enable", "--now", "avahi-daemon"], silent=True)
    info("avahi-daemon ready.")

    if not shutil.which("docker"):
        apt_install("docker.io")
    else:
        v = run(["docker", "--version"], capture=True).stdout.strip()
        info(f"Docker already present: {v}")
    with Spinner("Enabling Docker"):
        run(["systemctl", "enable", "--now", "docker"], silent=True)

    compose = find_compose()
    if compose is None:
        info("Installing docker-compose-plugin...")
        try:
            apt_install("docker-compose-plugin")
        except Exception:
            apt_install("docker-compose")
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

    # ── directories ───────────────────────────────────────────────────────────
    step("Directories")

    install_path = Path(install_dir)
    (install_path / "config" / "providers").mkdir(parents=True, exist_ok=True)
    info(f"Created {install_dir}")

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
        "# pimiproxy daily subscription refresh — generated by installer",
        f'exec python3 "{script_dest}" refresh',
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
    result = run(["hostname", "-I"], capture=True)
    lan_ip = result.stdout.strip().split()[0]

    print(f"\n{C.BOLD}{C.GREEN}{'─'*42}")
    print("  Installation complete!")
    print(f"{'─'*42}{C.NC}")
    print()
    print(f"  Dashboard    ->  http://{hostname_new}.lan:{dashboard_port}")
    print(
        f"               ->  http://{lan_ip}:{dashboard_port}  (IP fallback)")
    print(f"  Mihomo API   ->  http://{hostname_new}.lan:{api_port}")
    print(f"  Proxy        ->  {lan_ip}:{mixed_port}  (HTTP/SOCKS5)")
    print()
    print("  Pi routing control:")
    print("    sudo clash-system on      # route Pi traffic via proxy")
    print("    sudo clash-system off     # revert to direct")
    print("    sudo clash-system status")
    print()
    print("  Add or change proxy groups and rules:")
    print(f"    sudo python3 /opt/pimiproxy/pimiproxy-core.py configure")
    print()
    if has_aes_hardware():
        info(
            "Hardware AES acceleration: available — optimal encryption performance."
        )
    else:
        warn("Hardware AES acceleration: not available on this CPU.")
        warn(
            "Encryption will run in software — expect higher latency on heavy "
            "or high-speed connections. A Pi 3 or newer is recommended.")
    print()
    if _hostname_changed:
        warn(f"{hostname_new}.lan won't resolve until the Pi renews its DHCP "
             "lease — a reboot is the easiest way to trigger this.")
        warn(f"{hostname_new}.local is available immediately via mDNS.")
        if confirm("Reboot now so the router picks up the new hostname?",
                   default=False):
            run(["reboot"])
    else:
        warn(f"{hostname_new}.local is available via mDNS.")
        warn(f"{hostname_new}.lan resolves via your router's DNS.")
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
        info("No subscription URL configured — nothing to refresh.")
        return

    step("Refreshing subscription")
    # save_subscription updates .pimiproxy-config.json with new blocks
    if not save_subscription(install_dir, url):
        die("Subscription refresh failed.")

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
            for name in ["mihomo", "metacubexd", "zashboard"]:
                run(["docker", "stop", name], check=False, silent=True)
                run(["docker", "rm", name], check=False, silent=True)
    else:
        warn("No docker-compose.yml found — stopping containers by name.")
        for name in ["mihomo", "metacubexd", "zashboard"]:
            run(["docker", "stop", name], check=False, silent=True)
            run(["docker", "rm", name], check=False, silent=True)

    # ── remove images ─────────────────────────────────────────────────────────
    step("Removing Docker images")

    for img in [
            "ghcr.io/metacubex/mihomo:latest",
            "ghcr.io/metacubex/metacubexd:latest",
            "ghcr.io/zephyruso/zashboard:latest",
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
            "Remove avahi-daemon? Only if nothing else on this Pi needs mDNS."
    ):
        with Spinner("Removing avahi-daemon"):
            run(["apt-get", "remove", "-y", "avahi-daemon", "libnss-mdns"],
                silent=True,
                check=False)
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
    "status": do_status,
    "logs": do_logs,
    "restart": do_restart,
    "update": do_update,
    "hostname": do_set_hostname,
    "debug": do_debug,
}

# Commands that accept an optional second argument
_TAKES_ARG = {"hostname", "logs", "restart", "debug"}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "install"
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
        COMMANDS[cmd]()
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
