#!/usr/bin/env bash
# =============================================================================
# pimiproxy bootstrap
# Detects the platform, installs all prerequisites (Python 3, Docker, avahi),
# then launches pimiproxy-core.py to perform the requested action.
# Supports: Debian/Ubuntu/Raspbian, Fedora/RHEL, Arch, openSUSE, Alpine.
#
# Usage:
#   sudo bash pimiproxy.sh [install|uninstall|configure]
#
# Offline image preparation (run on a machine with internet + Docker):
#   docker pull --platform linux/arm64 ghcr.io/metacubex/metacubexd:latest
#   docker pull --platform linux/arm64 ghcr.io/zephyruso/zashboard:latest
#   docker pull --platform linux/arm64 ghcr.io/metacubex/mihomo:latest
#   docker save ghcr.io/metacubex/metacubexd | gzip > docker-images/metacubexd.tar.gz
#   docker save ghcr.io/zephyruso/zashboard  | gzip > docker-images/zashboard.tar.gz
#   docker save ghcr.io/metacubex/mihomo     | gzip > docker-images/mihomo.tar.gz
#   # pimiproxy-api and pimiproxy-ui are built locally from source at install time
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

info() { echo -e "${GREEN}  ✔${NC}  $*"; }
step() { echo -e "\n${CYAN}${BOLD}▸ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠${NC}  $*"; }
die()  { echo -e "${RED}  ✖  $*${NC}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root:  sudo bash $0 [install|uninstall|configure]"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/pimiproxy-core.py"

PIMIPROXY_CMD="${1:-install}"

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
        # Fallback: check ID_LIKE
        if echo "$OS_LIKE" | grep -q "debian"; then PKG_MGR="apt"
            elif echo "$OS_LIKE" | grep -q "fedora\|rhel"; then PKG_MGR="dnf"
            elif echo "$OS_LIKE" | grep -q "arch"; then PKG_MGR="pacman"
        else die "Unsupported distribution: $OS_ID. Install Python 3, Docker, and avahi-daemon manually, then run: sudo python3 pimiproxy-core.py $PIMIPROXY_CMD"
        fi
    ;;
esac

info "Package manager: $PKG_MGR"

# =============================================================================
# 2. SYSTEM UPDATE (lightweight — headers only, not full upgrade)
# =============================================================================
step "Updating package lists"

case "$PKG_MGR" in
    apt)    apt-get update -qq ;;
    dnf)    dnf check-update -q || true ;;
    pacman) pacman -Sy --noconfirm >/dev/null ;;
    zypper) zypper refresh -q ;;
    apk)    apk update -q ;;
esac

# =============================================================================
# 3. PYTHON 3
# =============================================================================
step "Python 3"

if command -v python3 &>/dev/null; then
    PY_VER="$(python3 --version 2>&1)"
    info "Already installed: $PY_VER"
else
    info "Installing Python 3..."
    case "$PKG_MGR" in
        apt)    apt-get install -y -qq --no-install-recommends python3 python3-pip >/dev/null ;;
        dnf)    dnf install -y -q python3 python3-pip ;;
        pacman) pacman -S --noconfirm python python-pip >/dev/null ;;
        zypper) zypper install -y -q python3 python3-pip ;;
        apk)    apk add -q python3 py3-pip ;;
    esac
    info "Python 3 installed."
fi

# Confirm we can actually run it
python3 -c "import sys; assert sys.version_info >= (3,7), 'Python 3.7+ required'" \
|| die "Python 3.7 or newer is required."

# =============================================================================
# 4. DOCKER
# =============================================================================
step "Docker"

if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    info "Docker + Compose already installed: $(docker --version | cut -d' ' -f3 | tr -d ',')"
else
    if command -v docker &>/dev/null; then
        # Docker present but no compose plugin — try adding it first
        warn "Docker found but Compose plugin missing. Attempting to add..."
        case "$PKG_MGR" in
            apt)
                apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 \
                && info "docker-compose-plugin installed." \
                || true
            ;;
            dnf)    dnf install -y -q docker-compose-plugin 2>/dev/null || true ;;
            pacman) pacman -S --noconfirm docker-compose >/dev/null 2>&1 || true ;;
        esac
    fi
    
    # If still missing, install Docker
    if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null 2>&1; then
        echo
        read -r -p "  Is this device located in China? (use Aliyun mirrors for Docker CE) [y/N]: " _china_ans
        CHINA_MIRRORS=false
        [[ "${_china_ans,,}" == "y" || "${_china_ans,,}" == "yes" ]] && CHINA_MIRRORS=true

        if [[ "$CHINA_MIRRORS" == "true" ]]; then
            info "Installing Docker CE via Aliyun mirror (China mode)..."
            case "$PKG_MGR" in
                apt)
                    apt-get install -y -qq ca-certificates curl gnupg >/dev/null
                    install -m 0755 -d /etc/apt/keyrings
                    curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/debian/gpg \
                        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
                    chmod a+r /etc/apt/keyrings/docker.gpg
                    CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
                    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://mirrors.aliyun.com/docker-ce/linux/debian ${CODENAME} stable" \
                        > /etc/apt/sources.list.d/docker.list
                    apt-get update -qq
                    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
                ;;
                dnf)
                    dnf install -y -q dnf-plugins-core
                    dnf config-manager --add-repo \
                        https://mirrors.aliyun.com/docker-ce/linux/fedora/docker-ce.repo
                    dnf install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
                ;;
                *)
                    warn "China mirror mode is supported for apt/dnf only. Falling back to get.docker.com."
                    curl -fsSL https://get.docker.com | sh
                ;;
            esac
        else
            info "Installing Docker via official install script (get.docker.com)..."
            if ! command -v curl &>/dev/null; then
                case "$PKG_MGR" in
                    apt)    apt-get install -y -qq curl >/dev/null ;;
                    dnf)    dnf install -y -q curl ;;
                    pacman) pacman -S --noconfirm curl >/dev/null ;;
                    zypper) zypper install -y -q curl ;;
                    apk)    apk add -q curl ;;
                esac
            fi
            curl -fsSL https://get.docker.com | sh
        fi
        info "Docker installed."
    fi
    
    # Enable and start Docker
    if command -v systemctl &>/dev/null; then
        systemctl enable --now docker 2>/dev/null || true
    fi
fi

# Final check
docker compose version &>/dev/null 2>&1 \
|| die "Docker Compose is not available after install. Check the Docker installation."

info "Docker ready: $(docker --version | cut -d' ' -f3 | tr -d ',')"

# =============================================================================
# 5. AVAHI / mDNS
# =============================================================================
step "mDNS (avahi)"

if command -v avahi-daemon &>/dev/null; then
    info "avahi-daemon already installed."
else
    info "Installing avahi-daemon..."
    case "$PKG_MGR" in
        apt)    apt-get install -y -qq --no-install-recommends avahi-daemon libnss-mdns >/dev/null ;;
        dnf)    dnf install -y -q avahi nss-mdns ;;
        pacman) pacman -S --noconfirm avahi nss-mdns >/dev/null ;;
        zypper) zypper install -y -q avahi ;;
        apk)    apk add -q avahi avahi-compat-libdns_sd ;;
    esac
fi

if command -v systemctl &>/dev/null; then
    systemctl enable --now avahi-daemon 2>/dev/null || true
fi

info "avahi-daemon ready."

# =============================================================================
# 6. RASPI-CONFIG  (Raspberry Pi only)
# =============================================================================
if [[ "$IS_RASPI" == "true" ]] && ! command -v raspi-config &>/dev/null; then
    step "raspi-config"
    case "$PKG_MGR" in
        apt) apt-get install -y -qq raspi-config >/dev/null && info "raspi-config installed." ;;
        *)   warn "raspi-config is only available on Raspberry Pi OS / Debian. Skipping." ;;
    esac
fi

# =============================================================================
# 7. ENSURE PIMIPROXY-CORE.PY IS PRESENT
# =============================================================================
step "pimiproxy-core.py"

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    die "pimiproxy-core.py not found at ${PYTHON_SCRIPT}.\nPlace pimiproxy-core.py in the same directory as this bootstrap script."
fi

info "Found: $PYTHON_SCRIPT"

# =============================================================================
# 8. HAND OFF TO PYTHON
# =============================================================================
echo -e "\n${CYAN}${BOLD}▸ Launching pimiproxy-core.py ${PIMIPROXY_CMD}...${NC}\n"
exec python3 "$PYTHON_SCRIPT" "$PIMIPROXY_CMD"