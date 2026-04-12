#!/usr/bin/env python3
"""
pimiproxy — install / uninstall / configure
Raspberry Pi OS (Bookworm/Bullseye) · Debian 11/12
Run as root: sudo python3 pimiproxy.py [install|uninstall|configure]
"""

import os
import sys
import shutil
import subprocess
import textwrap
import json
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
    warn("Restart avahi-daemon for mDNS to pick up the new name:")
    warn("  sudo systemctl restart avahi-daemon")


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
        # Strip GEOIP rules — they require the MaxMind MMDB database which
        # Mihomo downloads from GitHub on first run. On networks that can't
        # reach GitHub (or during initial setup) this causes a fatal crash.
        # The provider's ruleset already contains explicit IP-CIDR entries
        # for China and other regions, so GEOIP rules are redundant here.
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
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run([
        "apt-get", "install", "-y", "-qq", "--no-install-recommends", *packages
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
        info(f"Loading {archive} from disk...")
        result = run(f"gunzip -c {archive} | docker load", capture=True)
        output = result.stdout + result.stderr
        for line in output.splitlines():
            if "Loaded image:" in line:
                tag = line.split("Loaded image:")[-1].strip()
                info(f"Loaded: {tag}")
                return tag
        warn(f"Could not parse image tag from {archive} — will pull instead.")

    info(f"Pulling {fallback} ({arch})...")
    result = run(["docker", "pull", "--platform", arch, fallback],
                 check=False,
                 silent=True)
    if result.returncode != 0:
        die(f"Failed to pull {fallback}. Check network and registry access.")
    return fallback


# =============================================================================
# METADATA  (stored as JSON — no quoting issues, no sourcing issues)
# =============================================================================
META_PATH = Path("/opt/pimiproxy/.pimiproxy-meta.json")

# Docker image references
MIHOMO_IMAGE     = "ghcr.io/metacubex/mihomo:latest"
METACUBEXD_IMAGE = "ghcr.io/metacubex/metacubexd:latest"
ZASHBOARD_IMAGE  = "ghcr.io/zephyruso/zashboard:latest"


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
    L("  auto-route: false")
    L("  auto-detect-interface: false")
    L("  interface-name: eth0")
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
            "*.lan", "*.local", "+.facebook.com", "+.fbcdn.net",
            "+.messenger.com", "+.doubleclick.net",
            "connectivitycheck.gstatic.com"
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

    if sub_groups:
        # Proxy groups from provider — inject verbatim
        L()
        for line in sub_groups.splitlines():
            L(line)
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
        if proxy_groups:
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
            L()

    if sub_rules:
        # Rules from provider — inject verbatim
        L()
        for line in sub_rules.splitlines():
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
    lines = []

    def L(s=""):
        lines.append(s)

    L("#!/usr/bin/env bash")
    L("# clash-system -- toggle transparent proxy routing through the mihomo TUN"
      )
    L()
    L("RED='\\033[0;31m'; GREEN='\\033[0;32m'; NC='\\033[0m'")
    L()
    L("[[ $EUID -eq 0 ]] || exec sudo \"$0\" \"$@\"")
    L()
    L("iface_up() { [[ -d /sys/class/net/mihomo ]]; }")
    L()
    L("case \"${1:-}\" in")
    L("  on)")
    L("    iface_up || { echo -e \"${RED}X${NC} mihomo TUN not found -- is the container running?\"; exit 1; }"
      )
    L("    ip rule add to 10.0.0.0/8 table main priority 90  2>/dev/null || true"
      )
    L("    ip rule add fwmark 666     table main priority 100 2>/dev/null || true"
      )
    L("    ip rule add not fwmark 666 table 101  priority 101 2>/dev/null || true"
      )
    L("    ip route replace default dev mihomo table 101")
    L("    echo -e \"System proxy: ${GREEN}ON${NC}\"")
    L("    ;;")
    L("  off)")
    L("    ip rule  del priority 90  2>/dev/null || true")
    L("    ip rule  del priority 100 2>/dev/null || true")
    L("    ip rule  del priority 101 2>/dev/null || true")
    L("    ip route flush table 101  2>/dev/null || true")
    L("    echo -e \"System proxy: ${RED}OFF${NC}\"")
    L("    ;;")
    L("  status)")
    L("    if ip rule show | grep -q 'priority 101'; then")
    L("      echo -e \"System proxy: ${GREEN}ON${NC}\"")
    L("    else")
    L("      echo -e \"System proxy: ${RED}OFF${NC}\"")
    L("    fi")
    L("    iface_up && echo '  TUN interface: up' || echo '  TUN interface: down (container not running?)'"
      )
    L("    ;;")
    L("  restart)")
    L("    \"$0\" off; sleep 1; \"$0\" on")
    L("    ;;")
    L("  *)")
    L("    echo 'Usage: clash-system [on|off|status|restart]'")
    L("    exit 1")
    L("    ;;")
    L("esac")

    return "\n".join(lines)


# =============================================================================
# STATUS
# =============================================================================
def do_status():
    import time
    import urllib.request
    import urllib.error

    meta        = read_meta()
    install_dir = Path(meta["install_dir"])
    compose     = (meta["compose_bin"], meta["compose_sub"])
    mixed_port  = meta.get("mixed_port", "7890")
    api_port    = meta.get("api_port", "9090")
    dash_port   = meta.get("dashboard_port", "8080")
    hostname    = meta.get("hostname", "pimiproxy")

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
    handler = urllib.request.ProxyHandler({"http": proxy_addr, "https": proxy_addr})
    opener  = urllib.request.build_opener(handler)
    all_ok  = True
    for label, test_url in tests:
        try:
            t0 = time.monotonic()
            with opener.open(test_url, timeout=10) as resp:
                ms = (time.monotonic() - t0) * 1000
                info(f"{label}  {test_url}  ->  {resp.status}  ({ms:.0f}ms)")
        except Exception as exc:
            warn(f"{label}  {test_url}  ->  FAILED  ({exc})")
            all_ok = False
    if all_ok:
        info("Proxy is working correctly.")
    else:
        warn("Proxy test failed — check Mihomo logs:")
        warn("  sudo python3 pimiproxy-core.py logs")

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
    meta        = read_meta()
    install_dir = Path(meta["install_dir"])
    compose     = (meta["compose_bin"], meta["compose_sub"])

    service = sys.argv[2] if len(sys.argv) > 2 else "mihomo"
    if service not in {"mihomo", "dashboard"}:
        die(f"Unknown service '{service}'. Use: mihomo  or  dashboard")

    step(f"Logs: {service}  (Ctrl-C to stop)")
    try:
        run(compose_cmd(compose, "logs", "-f", "--tail=50", service),
            cwd=install_dir, check=False)
    except KeyboardInterrupt:
        print()


# =============================================================================
# RESTART
# =============================================================================
def do_restart():
    meta        = read_meta()
    install_dir = Path(meta["install_dir"])
    compose     = (meta["compose_bin"], meta["compose_sub"])

    service = sys.argv[2] if len(sys.argv) > 2 else None
    if service and service not in {"mihomo", "dashboard"}:
        die(f"Unknown service '{service}'. Use: mihomo, dashboard, or omit for all")

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
    meta        = read_meta()
    install_dir = Path(meta["install_dir"])
    compose     = (meta["compose_bin"], meta["compose_sub"])

    banner("pimiproxy update", C.CYAN)
    step("Pulling latest images")
    compose_run(compose, "pull", cwd=install_dir)

    step("Recreating containers with new images")
    compose_run(compose, "up", "-d", "--force-recreate", cwd=install_dir)
    info("Update complete.")


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

    compose         = (meta["compose_bin"], meta["compose_sub"])
    changed         = False
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
                    compose_run(compose, "restart", "mihomo",
                                cwd=install_dir, silent=False)
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
            cur_dash  = cfg.get("dashboard_port",
                                meta.get("dashboard_port", "8080"))
            cur_api   = cfg.get("api_port", meta.get("api_port", "9090"))
            cur_mixed = cfg.get("mixed_port", meta.get("mixed_port", "7890"))
            print(f"\n  {C.BOLD}Current ports:{C.NC}")
            print(f"    Dashboard  port : {cur_dash}")
            print(f"    Mihomo API port : {cur_api}")
            print(f"    Mixed proxy port: {cur_mixed}")
            print()
            new_dash  = ask("Dashboard port",   cur_dash)
            new_api   = ask("Mihomo API port",  cur_api)
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
            other    = "zashboard" if cur_dash == "metacubexd" else "metacubexd"
            other_img = (ZASHBOARD_IMAGE if other == "zashboard"
                         else METACUBEXD_IMAGE)
            other_arc = (f"{other}.tar.gz")
            print(f"\n  Current dashboard: {cur_dash}")
            if confirm(f"Switch to {other}?"):
                step(f"Loading {other} image")
                arch = meta.get("arch", "linux/arm64")
                new_img = load_image(other_arc, other_img, arch)
                cfg["dashboard_image"] = new_img
                cfg["dash_name"]       = other
                info(f"Dashboard switched to {other} — save and apply to activate.")
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

            # Restart: recreate all containers if compose changed, otherwise
            # just bounce Mihomo (config.yaml is re-read on start)
            if compose_changed:
                if confirm("Recreate containers now to apply changes?",
                           default=True):
                    compose_run(compose, "up", "-d", "--force-recreate",
                                cwd=install_dir)
                    info("Containers recreated.")
            else:
                if confirm("Restart Mihomo now to apply changes?",
                           default=True):
                    compose_run(compose, "restart", "mihomo",
                                cwd=install_dir, silent=False)
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
    hostname_new = _cur if _cur.lower() not in _DEFAULT_HOSTNAMES else "pimiproxy"
    dashboard_port = "8080"
    api_port = "9090"
    mixed_port = "7890"
    api_secret = "pimiproxy"
    arch = "linux/arm64"

    # ── prompts ───────────────────────────────────────────────────────────────
    step("Configuration")

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
    print("  it later by running:  sudo python3 pimiproxy.py configure")
    print("  " + "─" * 42)
    print()
    subscription_url = input("  Clash API URL [leave blank to skip]: ").strip()

    print()
    dash_choice = choose("Dashboard", [
        "Metacubexd  (standard, default)",
        "Zashboard   (power user, more stats)",
    ])

    # ── hostname ──────────────────────────────────────────────────────────────
    step("Hostname")

    if hostname_new == current_hostname():
        info(f"Hostname unchanged: {hostname_new}")
    else:
        set_hostname(hostname_new)

    # ── packages ──────────────────────────────────────────────────────────────
    step("Packages")

    run(["apt-get", "update", "-qq"])

    apt_install("avahi-daemon", "libnss-mdns")
    run(["systemctl", "enable", "--now", "avahi-daemon"], silent=True)
    info("avahi-daemon ready.")

    if not shutil.which("docker"):
        info("Installing docker.io...")
        apt_install("docker.io")
    else:
        v = run(["docker", "--version"], capture=True).stdout.strip()
        info(f"Docker already present: {v}")
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

    # ── launch ────────────────────────────────────────────────────────────────
    step("Starting services")

    compose_run(compose, "up", "-d", cwd=install_path)
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
    warn(f"{hostname_new}.lan resolves via your router's DNS once the Pi "
         "renews its DHCP lease (usually within seconds).")
    warn(f"{hostname_new}.local also works on devices that support mDNS.")
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
        run(["apt-get", "remove", "-y", "avahi-daemon", "libnss-mdns"],
            silent=True,
            check=False)
        info("avahi-daemon removed.")

    print(f"\n{C.BOLD}{C.GREEN}Uninstall complete.{C.NC}\n")


# =============================================================================
# ENTRYPOINT
# =============================================================================
COMMANDS = {
    "install":   do_install,
    "uninstall": do_uninstall,
    "configure": do_configure,
    "refresh":   do_refresh,
    "status":    do_status,
    "logs":      do_logs,
    "restart":   do_restart,
    "update":    do_update,
    "hostname":  do_set_hostname,
}

# Commands that accept an optional second argument
_TAKES_ARG = {"hostname", "logs", "restart"}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "install"
    if cmd not in COMMANDS:
        simple = "|".join(k for k in COMMANDS if k not in _TAKES_ARG)
        print(f"Usage: sudo python3 pimiproxy-core.py [{simple}]")
        print( "       sudo python3 pimiproxy-core.py logs     [mihomo|dashboard]")
        print( "       sudo python3 pimiproxy-core.py restart  [mihomo|dashboard]")
        print( "       sudo python3 pimiproxy-core.py hostname <new-hostname>")
        sys.exit(1)
    try:
        COMMANDS[cmd]()
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
