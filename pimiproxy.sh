#!/usr/bin/env bash
# =============================================================================
# pimiproxy bootstrap
# Detects the platform, installs all prerequisites (Python 3, Docker, avahi),
# then launches pimiproxy-core.py to perform the requested action.
# Supports: Debian/Ubuntu/Raspbian, Fedora/RHEL, Arch, openSUSE, Alpine.
#
# Usage:
#   sudo ./pimiproxy.sh [install|uninstall|configure]
#
# Offline image preparation (run on a machine with internet + Docker):
#   Use rebuild.sh --third-party to pull mihomo/metacubexd/zashboard for both
#   arm64 and amd64 and save them as multi-arch tarballs. Archives contain both
#   <image>:arm64 and <image>:amd64 tags; pimiproxy-core.py picks the right one.
#
#   ./rebuild.sh --third-party   # mihomo.tar.gz, metacubexd.tar.gz, zashboard.tar.gz
#   ./rebuild.sh                 # pimiproxy-api.tar.gz, pimiproxy-ui.tar.gz
#
#   pimiproxy-api and pimiproxy-ui can also be built from source at install time.
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

info() { echo -e "${GREEN}  ✔${NC}  $*"; }
step() { echo -e "\n${CYAN}${BOLD}▸ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }
die()  { echo -e "${RED}  ✖  $*${NC}" >&2; exit 1; }

# ── Spinner ────────────────────────────────────────────────────────────────────
# spin "Label" cmd arg1 arg2...
# Runs cmd silently in the background, shows a braille spinner until it finishes.
# On success prints ✔ label. On failure prints ✖ label and dumps the last 20
# lines of output to stderr.
_SPIN_LOG="/tmp/pimiproxy-bootstrap.log"

spin() {
    local msg="$1"; shift
    local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local n=${#frames}
    local i=0 rc=0

    "$@" >"$_SPIN_LOG" 2>&1 &
    local pid=$!

    while kill -0 "$pid" 2>/dev/null; do
        printf "\r  ${CYAN}${frames:$i:1}${NC}  %s" "$msg"
        i=$(( (i + 1) % n ))
        sleep 0.1
    done

    wait "$pid" || rc=$?
    if [[ $rc -eq 0 ]]; then
        printf "\r${GREEN}  ✔${NC}  %s\n" "$msg"
    else
        printf "\r${RED}  ✖${NC}  %s\n" "$msg" >&2
        echo -e "${RED}  Last output:${NC}" >&2
        tail -20 "$_SPIN_LOG" >&2
        return $rc
    fi
}

# ── Timed banner for operations that produce their own output ──────────────────
# timed_step "Label" cmd arg1 arg2...
# Like spin but lets stdout/stderr through (for curl|sh etc.) and prints elapsed.
timed_step() {
    local msg="$1"; shift
    warn "$msg — this may take several minutes..."
    local start=$SECONDS
    "$@"
    local elapsed=$(( SECONDS - start ))
    info "$msg — done (${elapsed}s)"
}

[[ $EUID -eq 0 ]] || die "Run as root:  sudo bash $0 [install|uninstall|configure]"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/pimiproxy-core.py"

PIMIPROXY_CMD="${1:-install}"

if [[ "$PIMIPROXY_CMD" == "-h" || "$PIMIPROXY_CMD" == "--help" ]]; then
    cat <<'EOF'
Usage: sudo ./pimiproxy.sh [subcommand]

Bootstrap & install:
  install             Install pimiproxy (default if no subcommand given)
                      Detects distro, installs prerequisites (Python, Docker,
                      curl, cron, avahi), then launches the interactive installer.

All other subcommands are passed through to pimiproxy-core.py:
  uninstall           Stop containers, remove images, delete all files
  configure           Interactive menu: provider URL, groups, rules, ports
  refresh             Re-fetch provider URL and restart Mihomo
  status              Container health, TUN state, proxy connectivity test
  test [--url URL]    Test all proxy groups, or a single URL with rule info
  logs [service]      Tail container logs (mihomo, dashboard, pimiproxy-api, pimiproxy-ui)
  restart [service]   Restart one container or all
  update              Pull latest Docker images and recreate containers
  hostname <name>     Change hostname (max 24 chars, alphanumeric + hyphens)
  debug [url]         Fetch a provider URL with Clash User-Agent and inspect
  export [dir]        Export config to a directory
  china-repos         Switch system package repos to Aliyun mirrors
  standard-repos      Restore original system package repos from backups

Examples:
  sudo ./pimiproxy.sh                              # install with defaults
  sudo ./pimiproxy.sh status                       # check proxy health
  sudo ./pimiproxy.sh test --url https://youtube.com
  sudo ./pimiproxy.sh logs mihomo
EOF
    exit 0
fi

# Non-install commands skip the full bootstrap and pass straight through to Python.
# This lets users run e.g. `sudo ./pimiproxy.sh logs` instead of invoking
# pimiproxy-core.py directly.
if [[ "$PIMIPROXY_CMD" != "install" ]]; then
    [[ -f "$PYTHON_SCRIPT" ]] || die "pimiproxy-core.py not found at ${PYTHON_SCRIPT}."
    exec python3 "$PYTHON_SCRIPT" "$PIMIPROXY_CMD" "${@:2}"
fi

echo -e "\n${CYAN}${BOLD}══════════════════════════════════════════"
echo -e "  pimiproxy bootstrap"
echo -e "══════════════════════════════════════════${NC}\n"

# =============================================================================
# 1. DETECT PLATFORM
# =============================================================================
step "Detecting platform"

OS_ID=""
OS_LIKE=""
PKG_MGR=""
IS_RASPI=false
ARCH="$(uname -m)"

if [[ -f /etc/os-release ]]; then
    source /etc/os-release
    OS_ID="${ID:-}"
    OS_LIKE="${ID_LIKE:-}"
fi

# Raspberry Pi detection
if [[ -f /proc/device-tree/model ]]; then
    MODEL="$(tr -d '\0' < /proc/device-tree/model)"
    if echo "$MODEL" | grep -qi "raspberry"; then
        IS_RASPI=true
        info "Raspberry Pi detected: $MODEL"
    fi
fi

info "OS: ${PRETTY_NAME:-$OS_ID}  |  Arch: $ARCH"

# Map distro to package manager
case "$OS_ID" in
    debian|raspbian|ubuntu|linuxmint|pop)  PKG_MGR="apt"   ;;
    fedora|rhel|centos|rocky|alma)         PKG_MGR="dnf"   ;;
    arch|manjaro|endeavouros)              PKG_MGR="pacman" ;;
    opensuse*|sles)                        PKG_MGR="zypper" ;;
    alpine)                                PKG_MGR="apk"   ;;
    *)
        if   echo "$OS_LIKE" | grep -q "debian"; then PKG_MGR="apt"
        elif echo "$OS_LIKE" | grep -q "fedora\|rhel"; then PKG_MGR="dnf"
        elif echo "$OS_LIKE" | grep -q "arch"; then PKG_MGR="pacman"
        else die "Unsupported distribution: $OS_ID. Install Python 3, Docker, and avahi-daemon manually, then run: sudo python3 pimiproxy-core.py $PIMIPROXY_CMD"
        fi
    ;;
esac

info "Package manager: $PKG_MGR"

# =============================================================================
# 2. SYSTEM UPDATE (package lists only)
# =============================================================================
step "Updating package lists"

case "$PKG_MGR" in
    apt)    spin "apt-get update"        apt-get update -qq ;;
    dnf)    spin "dnf check-update"      bash -c 'dnf check-update -q; true' ;;
    pacman) spin "pacman -Sy"            pacman -Sy --noconfirm ;;
    zypper) spin "zypper refresh"        zypper refresh -q ;;
    apk)    spin "apk update"            apk update -q ;;
esac

# =============================================================================
# 3. PYTHON 3
# =============================================================================
step "Python 3"

if command -v python3 &>/dev/null; then
    PY_VER="$(python3 --version 2>&1)"
    info "Already installed: $PY_VER"
else
    case "$PKG_MGR" in
        apt)    spin "Installing Python 3"  apt-get install -y -qq --no-install-recommends python3 python3-pip ;;
        dnf)    spin "Installing Python 3"  dnf install -y -q python3 python3-pip ;;
        pacman) spin "Installing Python 3"  pacman -S --noconfirm python python-pip ;;
        zypper) spin "Installing Python 3"  zypper install -y -q python3 python3-pip ;;
        apk)    spin "Installing Python 3"  apk add -q python3 py3-pip ;;
    esac
fi

python3 -c "import sys; assert sys.version_info >= (3,7), 'Python 3.7+ required'" \
|| die "Python 3.7 or newer is required."

# =============================================================================
# 4. ESSENTIAL TOOLS (curl, iproute2, procps — may be missing on minimal installs)
# =============================================================================
step "Essential tools"

_missing=()
command -v curl &>/dev/null || _missing+=(curl)
command -v ip   &>/dev/null || _missing+=(ip)
command -v sysctl &>/dev/null || _missing+=(sysctl)

if [[ ${#_missing[@]} -eq 0 ]]; then
    info "curl, ip, sysctl — all present."
else
    info "Installing missing tools: ${_missing[*]}"
    case "$PKG_MGR" in
        apt)
            _pkgs=""
            [[ " ${_missing[*]} " == *" curl "* ]]   && _pkgs+=" curl"
            [[ " ${_missing[*]} " == *" ip "* ]]     && _pkgs+=" iproute2"
            [[ " ${_missing[*]} " == *" sysctl "* ]] && _pkgs+=" procps"
            spin "Installing${_pkgs}"  apt-get install -y -qq --no-install-recommends $_pkgs
        ;;
        dnf)
            _pkgs=""
            [[ " ${_missing[*]} " == *" curl "* ]]   && _pkgs+=" curl"
            [[ " ${_missing[*]} " == *" ip "* ]]     && _pkgs+=" iproute"
            [[ " ${_missing[*]} " == *" sysctl "* ]] && _pkgs+=" procps-ng"
            spin "Installing${_pkgs}"  dnf install -y -q $_pkgs
        ;;
        pacman)
            _pkgs=""
            [[ " ${_missing[*]} " == *" curl "* ]]   && _pkgs+=" curl"
            [[ " ${_missing[*]} " == *" ip "* ]]     && _pkgs+=" iproute2"
            [[ " ${_missing[*]} " == *" sysctl "* ]] && _pkgs+=" procps-ng"
            spin "Installing${_pkgs}"  pacman -S --noconfirm $_pkgs
        ;;
        zypper)
            _pkgs=""
            [[ " ${_missing[*]} " == *" curl "* ]]   && _pkgs+=" curl"
            [[ " ${_missing[*]} " == *" ip "* ]]     && _pkgs+=" iproute2"
            [[ " ${_missing[*]} " == *" sysctl "* ]] && _pkgs+=" procps"
            spin "Installing${_pkgs}"  zypper install -y -q $_pkgs
        ;;
        apk)
            _pkgs=""
            [[ " ${_missing[*]} " == *" curl "* ]]   && _pkgs+=" curl"
            [[ " ${_missing[*]} " == *" ip "* ]]     && _pkgs+=" iproute2"
            [[ " ${_missing[*]} " == *" sysctl "* ]] && _pkgs+=" procps"
            spin "Installing${_pkgs}"  apk add -q $_pkgs
        ;;
    esac
fi

# =============================================================================
# 5. DOCKER
# =============================================================================
step "Docker"

if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    info "Docker + Compose already installed: $(docker --version | cut -d' ' -f3 | tr -d ',')"
else
    if command -v docker &>/dev/null; then
        warn "Docker found but Compose plugin missing. Attempting to add..."
        case "$PKG_MGR" in
            apt)    spin "Installing docker-compose-plugin"  bash -c 'apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || true' ;;
            dnf)    spin "Installing docker-compose-plugin"  bash -c 'dnf install -y -q docker-compose-plugin 2>/dev/null || true' ;;
            pacman) spin "Installing docker-compose"         bash -c 'pacman -S --noconfirm docker-compose >/dev/null 2>&1 || true' ;;
        esac
    fi

    if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null 2>&1; then
        echo
        read -r -p "  Is this device located in China? (use Aliyun mirrors for Docker CE) [y/N]: " _china_ans
        CHINA_MIRRORS=false
        [[ "${_china_ans,,}" == "y" || "${_china_ans,,}" == "yes" ]] && CHINA_MIRRORS=true

        if [[ "$CHINA_MIRRORS" == "true" ]]; then
            # ── Switch system package repos to Aliyun mirrors ────────────────
            # Docker CE dependencies (git, pigz, etc.) come from system repos,
            # not the Docker CE repo. If the VM's default mirrors are broken or
            # blocked, the install will fail even though the Docker repo is fine.
            case "$PKG_MGR" in
                apt)
                    spin "Switching system repos to Aliyun"  bash -c '
                        DISTRO="$(. /etc/os-release && echo "$ID")"
                        CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
                        # Back up and disable existing sources
                        [[ -f /etc/apt/sources.list ]] && \
                            mv /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true
                        for f in /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do
                            [[ -f "$f" ]] && mv "$f" "${f}.bak" 2>/dev/null || true
                        done
                        case "$DISTRO" in
                            ubuntu)
                                # Ubuntu: main + updates + security all from one mirror
                                cat > /etc/apt/sources.list.d/aliyun-system.sources <<SRCEOF
Types: deb
URIs: https://mirrors.aliyun.com/ubuntu
Suites: ${CODENAME} ${CODENAME}-updates ${CODENAME}-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
SRCEOF
                            ;;
                            debian|raspbian)
                                # Debian: main + updates from /debian, security from /debian-security
                                cat > /etc/apt/sources.list.d/aliyun-system.sources <<SRCEOF
Types: deb
URIs: https://mirrors.aliyun.com/debian
Suites: ${CODENAME} ${CODENAME}-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: https://mirrors.aliyun.com/debian-security
Suites: ${CODENAME}-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SRCEOF
                            ;;
                        esac
                        apt-get update -qq'
                ;;
                dnf)
                    spin "Switching system repos to Aliyun"  bash -c '
                        # Switch core repos to Aliyun baseurl
                        for f in /etc/yum.repos.d/fedora.repo /etc/yum.repos.d/fedora-updates.repo; do
                            [[ -f "$f" ]] || continue
                            cp "$f" "${f}.bak" 2>/dev/null || true
                            sed -i "s|^metalink=|#metalink=|g" "$f"
                            sed -i "s|^#baseurl=http://download.example/pub/fedora/linux|baseurl=https://mirrors.aliyun.com/fedora|g" "$f"
                        done
                        # Disable third-party repos that have no Chinese mirror and whose
                        # metalinks are unreachable from China (cisco-openh264, etc.)
                        for f in /etc/yum.repos.d/fedora-cisco-openh264.repo \
                                 /etc/yum.repos.d/fedora-updates-testing.repo \
                                 /etc/yum.repos.d/fedora-modular.repo \
                                 /etc/yum.repos.d/fedora-updates-modular.repo; do
                            [[ -f "$f" ]] && sed -i "s|^enabled=1|enabled=0|g" "$f" 2>/dev/null || true
                        done'
                ;;
                pacman)
                    spin "Switching system repos to Aliyun"  bash -c '
                        [[ -f /etc/pacman.d/mirrorlist ]] && \
                            cp /etc/pacman.d/mirrorlist /etc/pacman.d/mirrorlist.bak
                        echo "Server = https://mirrors.aliyun.com/archlinux/\$repo/os/\$arch" \
                            > /etc/pacman.d/mirrorlist
                        pacman -Sy --noconfirm'
                ;;
            esac

            # ── Install Docker CE from Chinese mirrors ───────────────────────
            case "$PKG_MGR" in
                apt)
                    spin "Installing prerequisites"  apt-get install -y -qq ca-certificates curl gnupg
                    spin "Adding Aliyun Docker GPG key"  bash -c '
                        DISTRO="$(. /etc/os-release && echo "$ID")"
                        install -m 0755 -d /etc/apt/keyrings
                        curl -fsSL "https://mirrors.aliyun.com/docker-ce/linux/${DISTRO}/gpg" \
                            | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
                        chmod a+r /etc/apt/keyrings/docker.gpg'
                    spin "Adding Aliyun Docker CE repo"  bash -c '
                        DISTRO="$(. /etc/os-release && echo "$ID")"
                        CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
                        # Fall back to latest known-supported codename if this one is not in the mirror yet
                        if ! curl -fsI "https://mirrors.aliyun.com/docker-ce/linux/${DISTRO}/dists/${CODENAME}/Release" \
                                -o /dev/null 2>/dev/null; then
                            case "$DISTRO" in
                                ubuntu) CODENAME="noble" ;;
                                *)      CODENAME="bookworm" ;;
                            esac
                        fi
                        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://mirrors.aliyun.com/docker-ce/linux/${DISTRO} ${CODENAME} stable" \
                            > /etc/apt/sources.list.d/docker.list
                        apt-get update -qq'
                    timed_step "Installing Docker CE via Aliyun" \
                        apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
                ;;
                dnf)
                    spin "Installing prerequisites"  dnf install -y -q curl
                    spin "Adding Aliyun Docker CE repo"  bash -c '
                        # Download repo file directly — avoids dnf4/dnf5 config-manager differences
                        curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/fedora/docker-ce.repo \
                            -o /etc/yum.repos.d/docker-ce.repo
                        # Rewrite base URLs to Aliyun mirror
                        sed -i "s|download.docker.com|mirrors.aliyun.com/docker-ce|g" \
                            /etc/yum.repos.d/docker-ce.repo
                        # If current Fedora version is not in the mirror yet, pin to previous
                        RELEASEVER=$(rpm -E %fedora)
                        ARCH=$(uname -m)
                        if ! curl -fsI \
                                "https://mirrors.aliyun.com/docker-ce/linux/fedora/${RELEASEVER}/${ARCH}/stable/repodata/repomd.xml" \
                                -o /dev/null 2>/dev/null; then
                            RELEASEVER=$((RELEASEVER - 1))
                            sed -i "s|\$releasever|${RELEASEVER}|g" /etc/yum.repos.d/docker-ce.repo
                        fi'
                    timed_step "Installing Docker CE via Aliyun" \
                        dnf install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
                ;;
                pacman)
                    timed_step "Installing Docker via pacman" \
                        pacman -S --noconfirm docker docker-compose
                    # Ensure docker compose plugin is wired up
                    if ! docker compose version &>/dev/null 2>&1; then
                        mkdir -p /usr/lib/docker/cli-plugins
                        ln -sf /usr/bin/docker-compose /usr/lib/docker/cli-plugins/docker-compose
                    fi
                ;;
                *)
                    warn "China mirror mode is not supported for this package manager. Falling back to get.docker.com."
                    timed_step "Installing Docker CE via get.docker.com" \
                        bash -c 'curl -fsSL https://get.docker.com | sh'
                ;;
            esac
        else
            if ! command -v curl &>/dev/null; then
                case "$PKG_MGR" in
                    apt)    spin "Installing curl"  apt-get install -y -qq curl ;;
                    dnf)    spin "Installing curl"  dnf install -y -q curl ;;
                    pacman) spin "Installing curl"  pacman -S --noconfirm curl ;;
                    zypper) spin "Installing curl"  zypper install -y -q curl ;;
                    apk)    spin "Installing curl"  apk add -q curl ;;
                esac
            fi
            timed_step "Installing Docker CE via get.docker.com" \
                bash -c 'curl -fsSL https://get.docker.com | sh'
        fi
    fi

    if command -v systemctl &>/dev/null; then
        spin "Enabling Docker service"  bash -c 'systemctl enable --now docker 2>/dev/null || true'
    fi
fi

docker compose version &>/dev/null 2>&1 \
|| die "Docker Compose is not available after install. Check the Docker installation."

info "Docker ready: $(docker --version | cut -d' ' -f3 | tr -d ',')"

# =============================================================================
# 6. AVAHI / mDNS
# =============================================================================
step "mDNS (avahi)"

if command -v avahi-daemon &>/dev/null; then
    info "avahi-daemon already installed."
else
    case "$PKG_MGR" in
        apt)    spin "Installing avahi-daemon"  apt-get install -y -qq --no-install-recommends avahi-daemon libnss-mdns ;;
        dnf)    spin "Installing avahi"         dnf install -y -q avahi nss-mdns ;;
        pacman) spin "Installing avahi"         pacman -S --noconfirm avahi nss-mdns ;;
        zypper) spin "Installing avahi"         zypper install -y -q avahi ;;
        apk)    spin "Installing avahi"         apk add -q avahi avahi-compat-libdns_sd ;;
    esac
fi

if command -v systemctl &>/dev/null; then
    spin "Enabling avahi-daemon"  bash -c 'systemctl enable --now avahi-daemon 2>/dev/null || true'
fi

info "avahi-daemon ready."

# =============================================================================
# 7. CRON (needed for daily provider refresh)
# =============================================================================
step "Cron"

if [[ -d /etc/cron.daily ]] && command -v crontab &>/dev/null; then
    info "cron already available."
else
    case "$PKG_MGR" in
        apt)    spin "Installing cron"  apt-get install -y -qq cron ;;
        dnf)    spin "Installing cronie"  dnf install -y -q cronie ;;
        pacman) spin "Installing cronie"  pacman -S --noconfirm cronie ;;
        zypper) spin "Installing cron"    zypper install -y -q cron ;;
        apk)    spin "Installing dcron"   apk add -q dcron ;;
    esac
    if command -v systemctl &>/dev/null; then
        spin "Enabling cron service"  bash -c '
            systemctl enable --now cronie 2>/dev/null || \
            systemctl enable --now cron   2>/dev/null || true'
    fi
    # Ensure cron.daily directory exists
    mkdir -p /etc/cron.daily
fi

info "cron ready."

# =============================================================================
# 8. RASPI-CONFIG  (Raspberry Pi only)
# =============================================================================
if [[ "$IS_RASPI" == "true" ]] && ! command -v raspi-config &>/dev/null; then
    step "raspi-config"
    case "$PKG_MGR" in
        apt) spin "Installing raspi-config"  apt-get install -y -qq raspi-config
             info "raspi-config installed." ;;
        *)   warn "raspi-config is only available on Raspberry Pi OS / Debian. Skipping." ;;
    esac
fi

# =============================================================================
# 9. ENSURE PIMIPROXY-CORE.PY IS PRESENT
# =============================================================================
step "pimiproxy-core.py"

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    die "pimiproxy-core.py not found at ${PYTHON_SCRIPT}.\nPlace pimiproxy-core.py in the same directory as this bootstrap script."
fi

info "Found: $PYTHON_SCRIPT"

# =============================================================================
# 10. HAND OFF TO PYTHON
# =============================================================================
echo -e "\n${CYAN}${BOLD}▸ Launching pimiproxy-core.py ${PIMIPROXY_CMD}...${NC}\n"
exec python3 "$PYTHON_SCRIPT" "$PIMIPROXY_CMD"
