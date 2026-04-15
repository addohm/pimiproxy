#!/usr/bin/env bash
# =============================================================================
# pimiproxy-ultralite bootstrap
# Detects the platform, installs all prerequisites (Python 3, avahi),
# then launches pimiproxy-ultralite.py to perform the requested action.
# Supports: Debian/Ubuntu/Raspbian, Fedora/RHEL, Arch, openSUSE, Alpine.
#
# Usage:
#   sudo ./pimiproxy-ultralite.sh [install|uninstall|configure]
#
# Mihomo binary preparation:
#   Download the appropriate release binary from:
#     https://github.com/MetaCubeX/mihomo/releases
#   Decompress the .gz file and place the binary in bin/ next to this script:
#
#     bin/mihomo-aarch64   (Raspberry Pi 4/5, modern 64-bit SBCs)
#     bin/mihomo-armv7l    (Raspberry Pi 3, 32-bit ARM SBCs)
#     bin/mihomo-x86_64    (PC / server / VM / x86 mini-PCs)
#
#   The installer detects host architecture automatically and picks the right one.
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

info() { echo -e "${GREEN}  ✔${NC}  $*"; }
step() { echo -e "\n${CYAN}${BOLD}▸ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }
die()  { echo -e "${RED}  ✖  $*${NC}" >&2; exit 1; }

# ── Spinner ────────────────────────────────────────────────────────────────────
_SPIN_LOG="/tmp/pimiproxy-ultralite-bootstrap.log"

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

[[ $EUID -eq 0 ]] || die "Run as root:  sudo bash $0 [install|uninstall|configure]"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/pimiproxy-ultralite.py"

PIMIPROXY_CMD="${1:-install}"

# Non-install commands skip the full bootstrap and pass straight through to Python.
# This lets users run e.g. `sudo ./pimiproxy-ultralite.sh logs` instead of invoking
# pimiproxy-ultralite.py directly.
if [[ "$PIMIPROXY_CMD" != "install" ]]; then
    [[ -f "$PYTHON_SCRIPT" ]] || die "pimiproxy-ultralite.py not found at ${PYTHON_SCRIPT}."
    exec python3 "$PYTHON_SCRIPT" "$PIMIPROXY_CMD" "${@:2}"
fi

echo -e "\n${CYAN}${BOLD}══════════════════════════════════════════"
echo -e "  pimiproxy-ultralite bootstrap"
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
        else die "Unsupported distribution: $OS_ID. Install Python 3 and avahi-daemon manually, then run: sudo python3 pimiproxy-ultralite.py $PIMIPROXY_CMD"
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
# 4. CHECK MIHOMO BINARY
# =============================================================================
step "Mihomo binary"

# Map uname -m to the naming convention used in bin/
case "$ARCH" in
    aarch64|arm64) BIN_NAME="mihomo-aarch64" ;;
    armv7l|armhf)  BIN_NAME="mihomo-armv7l"  ;;
    x86_64|amd64)  BIN_NAME="mihomo-x86_64"  ;;
    *)             BIN_NAME="mihomo-${ARCH}"  ;;
esac

if [[ -f "${SCRIPT_DIR}/bin/${BIN_NAME}" ]]; then
    info "Found: bin/${BIN_NAME}"
elif [[ -f "${SCRIPT_DIR}/bin/mihomo" ]]; then
    info "Found: bin/mihomo (generic)"
elif [[ -f "/usr/local/bin/mihomo" ]]; then
    info "Found: /usr/local/bin/mihomo (already installed)"
else
    warn "No mihomo binary found for arch '${ARCH}' (expected bin/${BIN_NAME})."
    warn "Download the matching release from https://github.com/MetaCubeX/mihomo/releases"
    warn "decompress the .gz file, rename it to '${BIN_NAME}', and place it in bin/."
    warn "The installer will fail at the binary-copy step if the file is not present."
fi

# =============================================================================
# 5. AVAHI / mDNS
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
# 6. RASPI-CONFIG  (Raspberry Pi only)
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
# 7. ENSURE PIMIPROXY-ULTRALITE.PY IS PRESENT
# =============================================================================
step "pimiproxy-ultralite.py"

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    die "pimiproxy-ultralite.py not found at ${PYTHON_SCRIPT}.\nPlace pimiproxy-ultralite.py in the same directory as this bootstrap script."
fi

info "Found: $PYTHON_SCRIPT"

# =============================================================================
# 8. HAND OFF TO PYTHON
# =============================================================================
echo -e "\n${CYAN}${BOLD}▸ Launching pimiproxy-ultralite.py ${PIMIPROXY_CMD}...${NC}\n"
exec python3 "$PYTHON_SCRIPT" "$PIMIPROXY_CMD"
