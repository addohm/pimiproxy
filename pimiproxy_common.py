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

import contextlib
import fcntl
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


# Set once at startup by each core's main() (see set_version_tag) so every
# banner can show what version the user is running without threading meta
# through every call site.
_VERSION_TAG = None


def set_version_tag(version):
    """Record the running version for banner(). "unknown"/empty disables it."""
    global _VERSION_TAG
    version = str(version or "").strip()
    _VERSION_TAG = version if version and version != "unknown" else None


def banner(msg, colour=C.CYAN):
    print(f"\n{colour}{C.BOLD}{SEP_LINE}")
    if _VERSION_TAG:
        print(f"  {msg}{C.NC}{colour}{C.DIM}  v{_VERSION_TAG}{C.NC}{colour}{C.BOLD}")
    else:
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
    'rules', and 'dns' (the raw dns block is parsed further by
    parse_dns_overrides, never emitted verbatim). Works whether the URL
    returns a full config or a proxies-only list.
    """
    target_keys = {"proxies", "proxy-groups", "rules", "dns"}
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


def parse_dns_overrides(dns_yaml: str) -> dict:
    """
    Pull `fake-ip-filter` entries and `nameserver-policy` mappings out of a
    provider's raw `dns:` block without PyYAML (the cores must stay
    dependency-free on a fresh OS). Everything else in the provider's dns
    block is deliberately ignored — the generated dns section (nameserver,
    fallback, hijack, bootstrap ordering) is load-bearing and stays local.

    Returns {"fake_ip_filter": [pattern, ...],
             "nameserver_policy": [[pattern, [server, ...]], ...]}  (ordered).

    Handles the styles real configs emit: block list items at or below the
    key's indent, inline flow lists ("key: [a, b]"), single inline values,
    quoted or bare keys and values, comments and blank lines. On a duplicate
    nameserver-policy pattern the last one wins (YAML map semantics).
    """
    def _unquote(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
            s = s[1:-1]
        return s.strip()

    def _split_servers(val: str) -> list:
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            parts = val[1:-1].split(",")
        else:
            parts = [val]
        return [x for x in (_unquote(p) for p in parts) if x]

    fake_ip: list = []
    fake_seen: set = set()
    ns_policy: list = []          # ordered [pattern, [servers]] entries
    ns_index: dict = {}           # pattern -> entry (for last-wins updates)
    section = None                # "fake" | "policy" | None
    section_indent = -1
    cur_pattern = None

    for raw in dns_yaml.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()

        if stripped.startswith("- ") or stripped == "-":
            item = _unquote(stripped[1:])
            if not item:
                continue
            if section == "fake":
                if item not in fake_seen:
                    fake_seen.add(item)
                    fake_ip.append(item)
            elif section == "policy" and cur_pattern is not None:
                ns_index[cur_pattern][1].append(item)
            continue

        # Split "key: value". A quoted key may itself contain ":" (mihomo
        # policy patterns like 'geosite:cn'), so close the quote first.
        if stripped[0] in ('"', "'"):
            _q_end = stripped.find(stripped[0], 1)
            _rest = stripped[_q_end + 1:] if _q_end != -1 else ""
            if _q_end == -1 or not _rest.lstrip().startswith(":"):
                continue
            key = stripped[1:_q_end].strip()
            val = _rest.lstrip()[1:].strip()
        else:
            if ":" not in stripped:
                continue
            key_part, _, val_part = stripped.partition(":")
            key = key_part.strip()
            val = val_part.strip()

        if section == "policy" and indent > section_indent and key:
            # A nameserver-policy pattern, with servers inline or on
            # following "- " lines.
            servers = _split_servers(val) if val else []
            if key in ns_index:
                ns_index[key][1][:] = servers
            else:
                entry = [key, servers]
                ns_index[key] = entry
                ns_policy.append(entry)
            cur_pattern = key
            continue
        if section == "fake" and indent > section_indent:
            continue  # unexpected nesting inside fake-ip-filter — skip

        # A key at or above the open section's indent closes it.
        cur_pattern = None
        if key == "fake-ip-filter":
            section, section_indent = "fake", indent
            if val:  # inline flow list: fake-ip-filter: [a, b]
                for item in _split_servers(val):
                    if item not in fake_seen:
                        fake_seen.add(item)
                        fake_ip.append(item)
                section = None
        elif key == "nameserver-policy":
            section, section_indent = "policy", indent
        else:
            section, section_indent = None, -1

    return {
        "fake_ip_filter": fake_ip,
        "nameserver_policy": [e for e in ns_policy if e[1]],
    }


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

    # The provider's dns block is never emitted verbatim (the generated dns
    # section is load-bearing), but its fake-ip-filter and nameserver-policy
    # are lifted out and merged into the generated one by build_config.
    if "dns" in blocks:
        overrides = parse_dns_overrides(blocks.pop("dns"))
        if overrides["fake_ip_filter"]:
            blocks["fake_ip_filter"] = overrides["fake_ip_filter"]
            info(f"Found {len(overrides['fake_ip_filter'])} fake-ip-filter "
                 "entries from provider — will merge (deduplicated).")
        if overrides["nameserver_policy"]:
            blocks["nameserver_policy"] = overrides["nameserver_policy"]
            info(f"Found {len(overrides['nameserver_policy'])} nameserver-policy "
                 "entries from provider — will merge (provider wins on overlap).")

    return blocks


def subscription_updates(blocks: dict) -> dict:
    """
    The .pimiproxy-config.json key updates a fetched subscription implies.
    Shared by save_subscription (CLI / cron / installer) and the API's
    refresh view so the two refresh paths can never drift. Keys listed in
    SUBSCRIPTION_KEYS must match exactly what this returns.
    """
    return {
        "subscription_proxies_yaml": blocks.get("proxies", ""),
        "subscription_groups_yaml": blocks.get("proxy-groups", ""),
        "subscription_rules_yaml": blocks.get("rules", ""),
        "subscription_fake_ip_filter": blocks.get("fake_ip_filter", []),
        "subscription_nameserver_policy": blocks.get("nameserver_policy", []),
    }


def save_subscription(install_path, url: str, test_cmd=None, variant: str = "full") -> bool:
    """
    Fetch the subscription and store the extracted blocks into
    .pimiproxy-config.json so build_config can inject them verbatim.

    If test_cmd is given (a mihomo_test_cmd_* command naming
    CONFIG_CANDIDATE), the merged config is validated with `mihomo -t`
    and installed as config.yaml *before* the JSON is persisted — so a
    provider payload Mihomo would reject leaves both files untouched and
    the previous working config stays live. Without test_cmd only the
    JSON is written (installer use, before the engine exists).

    Returns True on success.
    """
    # Fetch first (up to 30 s), then take the lock only for the write, and
    # update just the subscription keys — so a provider URL or rule edited
    # while the fetch was in flight is never overwritten with a stale copy.
    blocks = fetch_subscription(url)
    if not blocks:
        return False

    install_path = Path(install_path)
    cfg_file = config_path(install_path)
    with config_lock(cfg_file):
        cfg = load_config(cfg_file)
        cfg.update(subscription_updates(blocks))
        if test_cmd is not None:
            ok, why = install_config(install_path / "config",
                                     build_config(cfg, variant), test_cmd)
            if not ok:
                warn(f"Mihomo rejected the refreshed config — keeping the previous one.")
                warn(f"Reason: {why}")
                return False
            info("config.yaml validated and written.")
        write_config(cfg_file, cfg)
    return True


# =============================================================================
# CONFIG FILE I/O
# .pimiproxy-config.json has three independent writers: the CLI, the daily
# cron job, and the pimiproxy-api container (which sees the same file through
# a bind mount). A plain read-modify-write lets a slow refresh overwrite a
# change made while its fetch was in flight — the "provider URL reverted"
# bug. Every writer goes through these helpers:
#   - config_lock():   exclusive flock. Works across the bind mount because
#                      the container and the host share the inode.
#   - write_config():  atomic (temp file + rename) so readers never see a
#                      torn file. Does NOT lock — call under config_lock()
#                      or from code that owns the file (the installer).
#   - update_config(): lock → re-read → mutate → atomic write. The mutator
#                      only touches the keys it owns, so concurrent writers
#                      compose instead of clobbering each other.
# =============================================================================
CONFIG_JSON_NAME = ".pimiproxy-config.json"

# Keys owned by the subscription refresh path. A refresh must only write these.
# Must stay in lockstep with subscription_updates().
SUBSCRIPTION_KEYS = (
    "subscription_proxies_yaml",
    "subscription_groups_yaml",
    "subscription_rules_yaml",
    "subscription_fake_ip_filter",
    "subscription_nameserver_policy",
)


def config_path(install_dir) -> Path:
    return Path(install_dir) / CONFIG_JSON_NAME


@contextlib.contextmanager
def config_lock(cfg_path):
    """Hold an exclusive advisory lock on <cfg_path>.lock. Not re-entrant."""
    cfg_path = Path(cfg_path)
    lock_path = cfg_path.with_name(cfg_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_config(cfg_path) -> dict:
    """Read the config JSON; {} if it does not exist yet."""
    cfg_path = Path(cfg_path)
    if not cfg_path.exists():
        return {}
    return json.loads(cfg_path.read_text())


def write_config(cfg_path, cfg: dict):
    """Atomically replace cfg_path with json.dumps(cfg). Preserves file mode."""
    cfg_path = Path(cfg_path)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    mode = cfg_path.stat().st_mode & 0o777 if cfg_path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(prefix=cfg_path.name + ".", suffix=".tmp",
                               dir=cfg_path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(cfg, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, cfg_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def update_config(cfg_path, mutate) -> dict:
    """
    Lock, re-read the current file, apply mutate(cfg) in place, write
    atomically, and return the resulting dict. mutate may return a non-None
    value to abort without writing; that value is then returned instead.
    """
    with config_lock(cfg_path):
        cfg = load_config(cfg_path)
        abort = mutate(cfg)
        if abort is not None:
            return abort
        write_config(cfg_path, cfg)
        return cfg


# =============================================================================
# CONFIG GENERATION
# Builds config.yaml as plain text. We write YAML by hand (no PyYAML
# dependency) so the installers stay self-contained on a fresh OS.
# =============================================================================
def _effective_match_group(cfg: dict) -> str:
    """
    Target of the first MATCH rule in the combined rule order (user rules
    first, then provider rules) — i.e. the group that carries otherwise
    unmatched traffic. The DoH DNS fallback tunnels through it so fallback
    queries always leave via the proxy. Returns '' when no MATCH rule
    exists yet (fresh install), which degrades to untunneled DoH.
    """
    for r in cfg.get("rules", []):
        if isinstance(r, str) and r.strip().upper().startswith("MATCH,"):
            return r.strip().split(",", 1)[1].strip()
    m = re.search(r"^\s*-\s*MATCH,(.+?)\s*$",
                  cfg.get("subscription_rules_yaml", ""), re.MULTILINE)
    return m.group(1).strip() if m else ""


def build_config(cfg: dict, variant: str = "full") -> str:
    """
    Build the Mihomo config.yaml content as a string from a structured dict.
    Returns the complete file content.

    This is the single source of truth for config generation. It is used by
    pimiproxy-core.py, pimiproxy-ultralite-core.py, and the pimiproxy-api
    container (which loads this file from the mounted install dir), so all
    three always emit byte-identical config.yaml for the same input.

    variant: "full" or "ultralite" — only affects the header comment.
    """
    api_port = cfg["api_port"]
    api_secret = cfg["api_secret"]
    mixed_port = cfg["mixed_port"]
    subscription_url = cfg.get("subscription_url", "")

    lines = []

    def L(s=""):
        lines.append(s)

    if variant == "ultralite":
        L("# Mihomo config — managed by pimiproxy-ultralite")
        L("# Edit via:  sudo pimiproxy-ultralite configure")
    else:
        L("# Mihomo config — managed by pimiproxy")
        L("# Edit via the pimiproxy web UI or run:  sudo pimiproxy configure")
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
    # Proxy-node hostnames must resolve WITHOUT the fallback below, or the
    # tunneled DoH creates a bootstrap deadlock (DoH needs the proxy, the
    # proxy's hostname needs DNS, DNS waits on the DoH). Mainland resolvers
    # have always answered the provider's domains correctly.
    L("  proxy-server-nameserver:")
    L("    - 223.5.5.5")
    L("    - 119.29.29.29")
    # Plain-UDP overseas resolvers are GFW-poisoned (e.g. Meta/Oculus domains
    # get bogus or third-party IPs), which silently broke every domain the
    # fake-ip-filter exempts. Use DoH addressed by IP (no bootstrap lookup),
    # tunneled through the group the final MATCH rule targets so the queries
    # leave via the proxy. geoip:true keeps the fast mainland answers for CN
    # domains and swaps in the clean DoH answer for everything foreign.
    _fb_group = _effective_match_group(cfg)
    _fb_via = f"#{_fb_group}" if _fb_group else ""
    L("  fallback:")
    L(f'    - "https://8.8.8.8/dns-query{_fb_via}"')
    L(f'    - "https://1.1.1.1/dns-query{_fb_via}"')
    L("  fallback-filter:")
    L("    geoip: true")
    L("    geoip-code: CN")
    # Captive-portal check domains are DNS-hijacked by mainland ISPs to
    # ISP-local IPs, which become unreachable the moment the connection
    # leaves via the proxy — the device then reports "limited connection"
    # and (on Quest) gates the store and app downloads. The geoip fallback
    # filter cannot catch this hijack because the forged answers are CN
    # IPs, so pin these domains to the tunneled DoH for real addresses.
    # Provider-supplied nameserver-policy entries (lifted from the
    # subscription's dns block at refresh time) are merged on top: the
    # provider wins on a pattern both define, and its extra patterns are
    # appended after the local baseline. Local defaults still apply for
    # anything the provider doesn't set.
    _ns_policy = []            # ordered [pattern, [servers]] entries
    _ns_index = {}
    for _cc in ("connectivitycheck.gstatic.com",
                "connectivitycheck.android.com",
                "captive.apple.com",
                "+.msftconnecttest.com",
                "+.msftncsi.com"):
        _entry = [_cc, [f"https://8.8.8.8/dns-query{_fb_via}",
                        f"https://1.1.1.1/dns-query{_fb_via}"]]
        _ns_index[_cc] = _entry
        _ns_policy.append(_entry)
    for _sub_entry in cfg.get("subscription_nameserver_policy") or []:
        try:
            _pat, _servers = _sub_entry[0], list(_sub_entry[1])
        except (IndexError, TypeError, ValueError):
            continue
        # A quote or newline in a pattern/server would break the hand-built
        # YAML — no legitimate entry contains either.
        if (not _pat or not _servers or '"' in _pat or "\n" in _pat
                or any('"' in s or "\n" in s for s in _servers)):
            continue
        if _pat in _ns_index:
            _ns_index[_pat][1][:] = _servers
        else:
            _entry = [_pat, _servers]
            _ns_index[_pat] = _entry
            _ns_policy.append(_entry)
    L("  nameserver-policy:")
    for _pat, _servers in _ns_policy:
        L(f'    "{_pat}":')
        for _srv in _servers:
            L(f'      - "{_srv}"')
    L("  fake-ip-filter:")
    _fake_seen = set()

    def _emit_fake(entry):
        if entry and entry not in _fake_seen and '"' not in entry \
                and "\n" not in entry:
            _fake_seen.add(entry)
            L(f'    - "{entry}"')

    for entry in [
            # LAN / mDNS — never fake-IP local hostnames
            # Double-suffix patterns (*.lan.lan, *.local.lan) handle resolv.conf
            # search domain appending — e.g. armiproxy.lan → armiproxy.lan.lan
            "*.lan",
            "*.local",
            "*.lan.lan",
            "*.local.lan",
            "*.lan.localdomain",
            "*.local.localdomain",
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
            "+.oculusvr.com",
            "+.fbsbx.com",
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
        _emit_fake(entry)
    # User-managed additions (pimiproxy configure → fake-IP DNS exceptions)
    for entry in cfg.get("fake_ip_filter_extra", []):
        _emit_fake(entry)
    # Provider-supplied entries (lifted from the subscription's dns block at
    # refresh time). The local baseline stays authoritative and emits first;
    # anything the provider adds beyond it is appended, deduplicated — so a
    # provider that carries the same fleet-proven list produces no duplicates.
    for entry in cfg.get("subscription_fake_ip_filter") or []:
        _emit_fake(entry)
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

    # ── proxy-groups ──────────────────────────────────────────────────────────
    # Detect the indent the provider uses for top-level group entries so
    # appended groups land as siblings, not nested inside the last group's
    # proxies list.
    def _detect_group_indent(text: str) -> str:
        for ln in text.splitlines():
            stripped = ln.lstrip()
            if stripped.startswith("- "):
                return ln[: len(ln) - len(stripped)]
        return "  "

    # Helper: write a single group dict as YAML lines at the given indent
    def _write_group(g: dict, gi: str):
        ci = gi + "  "
        ii = gi + "    "
        L(f'{gi}- name: "{g["name"]}"')
        L(f'{ci}type: {g["type"]}')
        if "use" in g:
            L(f"{ci}use:")
            for u in g["use"]:
                L(f"{ii}- {u}")
        if "proxies" in g:
            L(f"{ci}proxies:")
            for p in g["proxies"]:
                L(f"{ii}- {yaml_quote(p)}")
        if "url" in g:
            L(f'{ci}url: {g["url"]}')
        if "interval" in g:
            L(f'{ci}interval: {g["interval"]}')
        if "tolerance" in g:
            L(f'{ci}tolerance: {g["tolerance"]}')

    if sub_groups:
        # Provider groups verbatim, then user groups
        gi = _detect_group_indent(sub_groups)
        ci = gi + "  "
        ii = gi + "    "
        L()
        for line in sub_groups.splitlines():
            L(line)
        for g in user_groups:
            _write_group(g, gi)
        L()
    else:
        # No provider groups — use user groups + Quest fallback
        if subscription_url and not user_groups:
            user_groups = [{
                "name": "Proxy",
                "type": "select",
                "proxies": ["DIRECT"]
            }]
        if user_groups:
            L("proxy-groups:")
            for g in user_groups:
                _write_group(g, "  ")
            L()

    # ── rules ─────────────────────────────────────────────────────────────────
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
        # Order: user rules (highest priority) → provider rules
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
        for r in user_rules:
            L(f"  - {r}")
        L()

    return "\n".join(lines)


# =============================================================================
# ENGINE: VALIDATE CONFIG, PROBE CONTROLLER
# Mihomo has a test mode (`mihomo -t -f <file>`) that parses and fully
# resolves a config without starting it. Every write path runs the new
# config through it *before* replacing config.yaml, so an invalid provider
# payload or a bad user rule is reported to the caller instead of leaving
# the engine crash-looping while the UI says "saved".
# =============================================================================
CONFIG_CANDIDATE = "config.yaml.candidate"
_MIHOMO_MOUNT = "/root/.config/mihomo"


def mihomo_test_cmd_docker(engine_image: str, host_config_dir, filename: str) -> list:
    """Command to test <host_config_dir>/<filename> with the engine image.
    Runs a throwaway container so it works even if `mihomo` is crash-looping."""
    return ["docker", "run", "--rm",
            "-v", f"{host_config_dir}:{_MIHOMO_MOUNT}",
            engine_image,
            "-t", "-d", _MIHOMO_MOUNT, "-f", f"{_MIHOMO_MOUNT}/{filename}"]


def mihomo_test_cmd_native(binary, config_dir, filename: str) -> list:
    """Command to test <config_dir>/<filename> with a native mihomo binary."""
    return [str(binary), "-t", "-d", str(config_dir), "-f", f"{config_dir}/{filename}"]


def _mihomo_error_summary(output: str) -> str:
    """Reduce mihomo -t output to the error message(s) a human needs."""
    msgs = re.findall(r'level=error msg="((?:[^"\\]|\\.)*)"', output)
    if msgs:
        return "; ".join(m.replace('\\"', '"') for m in msgs)
    tail = [l for l in output.strip().splitlines() if l.strip()]
    return tail[-1] if tail else "mihomo -t failed with no output"


def install_config(config_dir, text: str, test_cmd: list, timeout: int = 60):
    """
    Write `text` as a candidate file in config_dir, validate it with
    `test_cmd` (a mihomo_test_cmd_* command naming CONFIG_CANDIDATE), and
    only if mihomo accepts it move it into place as config.yaml.

    Returns (True, "") on success, (False, reason) on rejection. The live
    config.yaml is untouched on failure.
    """
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    candidate = config_dir / CONFIG_CANDIDATE
    candidate.write_text(text)
    try:
        try:
            r = subprocess.run(test_cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as e:
            return False, f"cannot run validator: {e}"
        except subprocess.TimeoutExpired:
            return False, f"validator timed out after {timeout}s"
        if r.returncode != 0:
            return False, _mihomo_error_summary(r.stdout + r.stderr)
        os.replace(candidate, config_dir / "config.yaml")
        return True, ""
    finally:
        with contextlib.suppress(OSError):
            candidate.unlink()


def probe_controller(host: str, api_port, secret: str, timeout: float = 3.0):
    """
    GET /version on the Mihomo external controller. Returns (True, version)
    when the engine answers with its secret accepted, else (False, reason).
    This is the only reliable "is the engine actually serving" check — the
    TUN interface can exist while the process is dead or belongs to a
    different mihomo entirely.
    """
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        f"http://{host}:{api_port}/version",
        headers={"Authorization": f"Bearer {secret}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            return True, str(data.get("version", "unknown"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "controller answered but rejected the API secret"
        return False, f"controller HTTP {e.code}"
    except Exception as e:
        return False, f"controller not answering on {host}:{api_port} ({e.__class__.__name__})"


def tun_up(device: str = "mihomo") -> bool:
    return Path(f"/sys/class/net/{device}").exists()


def port_owner(port) -> str:
    """
    Describe the process listening on TCP `port` as "pid N (comm)", or ""
    if nothing is or it cannot be determined (ss needs root to see pids
    of other users' sockets). Used to spot a foreign mihomo that already
    holds the mixed port, which is otherwise indistinguishable from ours.
    """
    try:
        r = subprocess.run(["ss", "-tlnpH", f"sport = :{port}"],
                           capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', r.stdout)
    if m:
        return f"pid {m.group(2)} ({m.group(1)})"
    return "unknown process" if r.stdout.strip() else ""


def engine_health(process_state: str, process_running: bool, api_port, secret: str,
                  mixed_port, host: str = "127.0.0.1") -> dict:
    """
    One health verdict for the engine, shared by the CLIs, the API, and
    the login banner. `process_state`/`process_running` come from the
    variant (docker inspect or systemctl is-active); everything else is
    probed here.

      healthy     — process running AND controller answering with our secret
      controller  — (ok, detail): detail is the version or the reason
      tun         — /sys/class/net/mihomo exists
      port_owner  — who holds the mixed port (see port_owner())
      foreign     — a listener holds the mixed port while our process is
                    not running: another mihomo is serving in our place
    """
    ctl_ok, ctl_detail = probe_controller(host, api_port, secret)
    owner = port_owner(mixed_port)
    return {
        "process": process_state,
        "running": process_running,
        "controller": ctl_ok,
        "controller_detail": ctl_detail,
        "tun": tun_up(),
        "port_owner": owner,
        "foreign": bool(owner) and not process_running,
        "healthy": process_running and ctl_ok,
    }


def print_engine_health(h: dict, what: str, logs_cmd: str) -> None:
    """Print an engine_health() verdict in the CLI house style.
    `what` names the process ("container" / "service")."""
    if h["running"]:
        info(f"Mihomo {what}: {h['process']}")
    else:
        warn(f"Mihomo {what}: {h['process']}")
    if h["controller"]:
        info(f"Controller: answering ({h['controller_detail']})")
    else:
        warn(f"Controller: {h['controller_detail']}")
    if h["tun"]:
        info("TUN interface: up")
    else:
        warn("TUN interface: down")
    if h["foreign"]:
        warn(f"The mixed port is held by {h['port_owner']} — that is NOT pimiproxy's "
             f"{what}. Another Mihomo is serving in its place; changes made here "
             "will not take effect until it is stopped.")
    elif h["running"] and not h["controller"]:
        warn(f"The {what} is up but the engine is not answering — it is probably "
             f"crash-looping on a bad config. Check:  {logs_cmd}")
    if h["healthy"]:
        info("Engine healthy.")
    else:
        warn("Engine NOT healthy.")


def preflight_conflicts(mixed_port, api_port, own_unit_marker: str = None,
                        own_binary=None, own_process_running: bool = False) -> list:
    """
    Things already on this host that would collide with a fresh install:
    another mihomo systemd unit, a mihomo binary, a TUN device named
    mihomo, or a listener on the ports we are about to claim. Returns
    human-readable findings (empty = clear).

      own_unit_marker     — text identifying our own unit (reinstall is fine)
      own_binary          — path of the binary we install ourselves
      own_process_running — our own engine from a previous install is up,
                            so port ownership is expected; skip port checks
    """
    findings = []
    try:
        r = subprocess.run(["systemctl", "show", "mihomo", "-p", "LoadState",
                            "-p", "ActiveState", "-p", "FragmentPath"],
                           capture_output=True, text=True, timeout=5)
        props = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        props = {}
    if props.get("LoadState") == "loaded":
        path = props.get("FragmentPath", "")
        text = Path(path).read_text() if path and Path(path).exists() else ""
        if own_unit_marker and own_unit_marker in text:
            # Our own unit from a previous install; if it is up, the TUN
            # device and ports are expected to be ours.
            if props.get("ActiveState") == "active":
                own_process_running = True
        else:
            findings.append(f"a mihomo systemd unit already exists: {path} "
                            f"({props.get('ActiveState', '?')})")
    for b in (Path("/usr/bin/mihomo"), Path("/usr/local/bin/mihomo")):
        if b.exists() and (own_binary is None or b != Path(own_binary)):
            findings.append(f"a mihomo binary is installed at {b}")
    if tun_up() and not own_process_running:
        findings.append("a TUN device named 'mihomo' is already up")
    if not own_process_running:
        for label, port in (("mixed proxy", mixed_port), ("Mihomo API", api_port)):
            owner = port_owner(port)
            if owner:
                findings.append(f"port {port} ({label}) is already in use by {owner}")
    return findings


def wait_for_controller(host: str, api_port, secret: str, timeout: float = 20.0):
    """Poll probe_controller() until it succeeds or `timeout` seconds pass."""
    import time
    deadline = time.monotonic() + timeout
    last = "not attempted"
    while True:
        ok, last = probe_controller(host, api_port, secret, timeout=2.0)
        if ok:
            return True, last
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(0.5)


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


# =============================================================================
# PRIOR INSTALL DISCOVERY
# Reinstalling over an existing install used to start from hardcoded defaults
# and write a fresh config, silently discarding the provider URL, user groups
# and rules, DNS exceptions, and resetting ports and the API secret. The
# installer now reads what is already there first so it can seed its prompts
# and preserve the user layer.
# =============================================================================
def load_prior_install(meta_path, default_install_dir):
    """
    Read an existing install's config for seeding installer prompts.

    The meta file lives at a fixed path per variant and records the real
    install_dir (which an advanced install may have moved), so it is read
    first and the config is then looked up relative to it.

    Returns (cfg, install_dir). `cfg` is empty when there is no prior install
    or its config can't be parsed — in which case the caller behaves exactly
    as it did before this existed.
    """
    meta_path = Path(meta_path)
    install_dir = default_install_dir
    try:
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            install_dir = meta.get("install_dir") or default_install_dir
    except (OSError, ValueError):
        warn(f"{meta_path} is unreadable — ignoring previous install metadata.")

    cfg_file = config_path(install_dir)
    try:
        if cfg_file.exists():
            loaded = json.loads(cfg_file.read_text())
            if isinstance(loaded, dict):
                return loaded, install_dir
            warn(f"{cfg_file} is not a config object — ignoring it.")
    except (OSError, ValueError):
        warn(f"{cfg_file} exists but could not be parsed — "
             "ignoring previous settings.")
    return {}, install_dir


# =============================================================================
# BOOT-TIME HOST-TRAFFIC BYPASS
# `clash-system on|off` toggles host traffic at runtime; pimiproxy-bypass.service
# re-applies the "off" (direct) choice after every boot. The unit's ordering
# dependency differs per variant (docker.service vs mihomo.service), so each core
# supplies its own unit text while the systemd mechanics live here.
#
# State is read back from systemd rather than stored in config: the unit is the
# single source of truth, so a manual `systemctl disable` can never leave the
# recorded state lying.
# =============================================================================
def boot_bypass_enabled(unit_path) -> bool:
    """True when the bypass unit is installed AND enabled at boot."""
    unit_path = Path(unit_path)
    if not unit_path.exists():
        return False
    return run_ok(["systemctl", "is-enabled", unit_path.name])


def apply_boot_bypass(enabled: bool, unit_path, unit_text: str) -> bool:
    """
    Install+enable (enabled=True) or disable+remove (False) the bypass unit.
    Idempotent: safe to call when already in the requested state. Returns True
    on success, False when systemd isn't available to act on.
    """
    unit_path = Path(unit_path)
    if not shutil.which("systemctl"):
        warn("systemctl not found — boot-time bypass needs systemd.")
        return False

    if enabled:
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit_text)
        run(["systemctl", "daemon-reload"], silent=True, check=False)
        run(["systemctl", "enable", unit_path.name], silent=True, check=False)
        # Starting it applies the bypass now, so the running state matches what
        # the next boot will do — no surprise on reboot.
        run(["systemctl", "start", unit_path.name], silent=True, check=False)
    else:
        if unit_path.exists():
            # Stopping runs ExecStop (`clash-system on`), restoring host traffic
            # to the proxy immediately rather than at the next boot.
            run(["systemctl", "disable", "--now", unit_path.name],
                silent=True, check=False)
            unit_path.unlink()
            run(["systemctl", "daemon-reload"], silent=True, check=False)
    return True


# =============================================================================
# VERSIONING
# The VERSION file at the repo root is the single source of truth. Release
# archives ship a copy next to the core script; images carry it as the
# io.pimiproxy.version label. Installs record it in .pimiproxy-meta.json.
# =============================================================================
def read_local_version() -> str:
    """
    Version stamped next to this file (source tree or extracted release
    archive). Returns "unknown" when no VERSION file is present — e.g. an
    install dir from a pre-8.0 release.
    """
    try:
        v = (Path(__file__).resolve().parent / "VERSION").read_text().strip()
        return v or "unknown"
    except OSError:
        return "unknown"


def resolve_version(meta: dict = None) -> str:
    """
    The version this install is running: `version` from .pimiproxy-meta.json
    (recorded at install time) first, falling back to a VERSION file sitting
    next to this module (source tree or extracted release archive).
    "unknown" when neither is available — e.g. a pre-8.0 install.
    """
    if meta:
        v = str(meta.get("version") or "").strip()
        if v:
            return v
    return read_local_version()


def parse_version(v: str):
    """
    "8.0.0" → (8, 0, 0). Tolerates a leading "v" and non-numeric tails
    ("8.1.0-rc1" → (8, 1, 0)). Returns None for unparseable/unknown input,
    so callers can treat "can't compare" separately from "older".
    """
    if not v:
        return None
    m = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", v.strip())
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())


def version_newer(candidate: str, current: str) -> bool:
    """
    True when `candidate` is strictly newer than `current`. If the CURRENT
    version is unknown (pre-versioning install) any parseable candidate
    counts as newer; an unparseable candidate never does.
    """
    cand = parse_version(candidate)
    if cand is None:
        return False
    cur = parse_version(current)
    if cur is None:
        return True
    return cand > cur
