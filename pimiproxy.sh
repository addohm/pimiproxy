#!/usr/bin/env bash
# =============================================================================
# pimiproxy — install / uninstall
# Tested on: Raspberry Pi OS (Bookworm/Bullseye), Debian 11/12
# Run as root: sudo bash pimiproxy.sh [install|uninstall]
# =============================================================================

set -Eeuo pipefail
trap 'echo -e "\n${RED}[FATAL]${NC} Unexpected error on line ${LINENO}. Aborting." >&2' ERR

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

# All log functions write to stderr — never pollute captured stdout
info()    { echo -e "${GREEN}  v${NC}  $*" >&2; }
step()    { echo -e "\n${CYAN}${BOLD}> $*${NC}" >&2; }
warn()    { echo -e "${YELLOW}  !${NC}  $*" >&2; }
die()     { echo -e "${RED}  X  $*${NC}" >&2; exit 1; }
confirm() { read -rp "$(echo -e "${YELLOW}  ?${NC}  $* [y/N]: ")" _r; [[ "${_r,,}" == "y" ]]; }

# ── root guard ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Run as root:  sudo bash $0 [install|uninstall]"

# =============================================================================
# DEFAULTS
# =============================================================================
INSTALL_DIR="/opt/pimiproxy"
HOSTNAME_NEW="pimiproxy"
DASHBOARD_PORT="8080"
MIXED_PORT="7890"
API_PORT="9090"
API_SECRET="pimiproxy"
SUBSCRIPTION_URL=""
ARCH="linux/arm64"

MIHOMO_IMAGE="ghcr.io/metacubex/mihomo:latest"
METACUBEXD_IMAGE="ghcr.io/metacubex/metacubexd:latest"
ZASHBOARD_IMAGE="ghcr.io/zephyruso/zashboard:latest"

# =============================================================================
# HELPERS
# =============================================================================

apt_install() {
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    --no-install-recommends "$@" >/dev/null
}

# Detect and set the global COMPOSE variable — never sourced from a file
detect_compose() {
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_BIN="docker"
        COMPOSE_SUB="compose"
        elif command -v docker-compose &>/dev/null; then
        COMPOSE_BIN="docker-compose"
        COMPOSE_SUB=""
    else
        die "No Docker Compose found after install."
    fi
    info "Using compose: ${COMPOSE_BIN} ${COMPOSE_SUB}"
}

# Run compose — always call this instead of $COMPOSE directly
compose_run() {
    if [[ -n "$COMPOSE_SUB" ]]; then
        "$COMPOSE_BIN" "$COMPOSE_SUB" "$@"
    else
        "$COMPOSE_BIN" "$@"
    fi
}

# Load image from local archive or pull from registry.
# Only the image tag is printed to stdout. Everything else goes to stderr.
load_image() {
    local archive="$1"
    local image="$2"
    
    if [[ -f "$archive" ]]; then
        info "Loading ${archive} from disk..."
        local out
        out=$(gunzip -c "$archive" | docker load 2>&1)
        info "$out"
        local tag
        tag=$(echo "$out" | awk '/Loaded image:/{print $NF}' | tail -1)
        if [[ -n "$tag" ]]; then
            printf '%s' "$tag"
            return
        fi
        warn "Could not parse image tag from archive — will pull instead."
    fi
    
    info "Pulling ${image} (platform: ${ARCH})..."
    docker pull --platform "$ARCH" "$image" >/dev/null 2>&1 \
    || die "Pull failed for ${image} — check network / registry access."
    printf '%s' "$image"
}

# Write the metadata file — one key per line, all values shell-quoted
write_meta() {
    local file="${INSTALL_DIR}/.pimiproxy-meta"
    # Use printf with %q so any value containing spaces or special chars
    # is safely quoted and can be sourced back without eval tricks
    {
        printf 'INSTALL_DIR=%q\n'    "$INSTALL_DIR"
        printf 'HOSTNAME_NEW=%q\n'   "$HOSTNAME_NEW"
        printf 'DASHBOARD_PORT=%q\n' "$DASHBOARD_PORT"
        printf 'API_PORT=%q\n'       "$API_PORT"
        printf 'MIXED_PORT=%q\n'     "$MIXED_PORT"
        printf 'COMPOSE_BIN=%q\n'    "$COMPOSE_BIN"
        printf 'COMPOSE_SUB=%q\n'    "$COMPOSE_SUB"
    } > "$file"
}

# Read the metadata file written by write_meta — sets globals
read_meta() {
    local file="/opt/pimiproxy/.pimiproxy-meta"
    if [[ -f "$file" ]]; then
        # Each line is already shell-quoted by %q so direct source is safe
        # shellcheck disable=SC1090
        source "$file"
        info "Loaded install metadata."
    else
        warn "No metadata found — using defaults."
        INSTALL_DIR="/opt/pimiproxy"
        HOSTNAME_NEW="pimiproxy"
        COMPOSE_BIN="docker"
        COMPOSE_SUB="compose"
    fi
}

# =============================================================================
# WRITE CONFIG.YAML
# Write using a Python heredoc-free approach: every line is an explicit
# printf call so there are no tab/space ambiguities and no variable leakage.
# =============================================================================
write_config() {
    local cfg="${INSTALL_DIR}/config/config.yaml"
    
    # Helper — write a literal line to the config (no interpretation)
    cl() { printf '%s\n' "$1" >> "$cfg"; }
    
    # Wipe and start fresh
    : > "$cfg"
    
    cl "# Mihomo config generated by pimiproxy installer"
    cl "# Edit this file, then restart: cd ${INSTALL_DIR} && $(compose_run echo 2>/dev/null || echo 'docker compose') restart mihomo"
    cl ""
    cl "external-controller: 0.0.0.0:${API_PORT}"
    cl "secret: \"${API_SECRET}\""
    cl ""
    cl "mixed-port: ${MIXED_PORT}"
    cl "routing-mark: 666"
    cl "allow-lan: true"
    cl "mode: rule"
    cl "log-level: info"
    cl "ipv6: false"
    cl ""
    cl "tun:"
    cl "  enable: true"
    cl "  stack: gvisor"
    cl "  device: mihomo"
    cl "  auto-route: false"
    cl "  auto-detect-interface: false"
    cl "  interface-name: eth0"
    cl "  dns-hijack:"
    cl "    - any:53"
    cl ""
    cl "dns:"
    cl "  enable: true"
    cl "  listen: 0.0.0.0:53"
    cl "  enhanced-mode: fake-ip"
    cl "  nameserver:"
    cl "    - 223.5.5.5"
    cl "    - 119.29.29.29"
    cl "  fallback:"
    cl "    - 8.8.8.8"
    cl "    - 1.1.1.1"
    cl "  fake-ip-filter:"
    cl "    - \"*.lan\""
    cl "    - \"*.local\""
    cl "    - \"+.facebook.com\""
    cl "    - \"+.fbcdn.net\""
    cl "    - \"+.messenger.com\""
    cl "    - \"+.doubleclick.net\""
    cl "    - \"connectivitycheck.gstatic.com\""
    cl ""
    
    if [[ -n "$SUBSCRIPTION_URL" ]]; then
        mkdir -p "${INSTALL_DIR}/config/providers"
        cl "proxy-providers:"
        cl "  subscription:"
        cl "    type: http"
        cl "    url: \"${SUBSCRIPTION_URL}\""
        cl "    interval: 86400"
        cl "    path: ./providers/subscription.yaml"
        cl "    health-check:"
        cl "      enable: true"
        cl "      url: https://www.gstatic.com/generate_204"
        cl "      interval: 300"
        cl ""
        cl "proxy-groups:"
        cl "  - name: \"Proxy\""
        cl "    type: select"
        cl "    use:"
        cl "      - subscription"
        cl ""
        cl "  - name: \"Auto - Fastest\""
        cl "    type: url-test"
        cl "    use:"
        cl "      - subscription"
        cl "    url: https://www.gstatic.com/generate_204"
        cl "    interval: 300"
        cl "    tolerance: 50"
        cl ""
        cl "rules:"
        cl "  - DOMAIN-SUFFIX,lan,DIRECT"
        cl "  - DOMAIN-SUFFIX,local,DIRECT"
        cl "  - IP-CIDR,192.168.0.0/16,DIRECT"
        cl "  - IP-CIDR,10.0.0.0/8,DIRECT"
        cl "  - IP-CIDR,172.16.0.0/12,DIRECT"
        cl "  - MATCH,Proxy"
        info "Subscription URL configured."
    else
        cl "# No subscription URL provided."
        cl "# To add one later: edit this file, add a proxy-providers block,"
        cl "# then restart mihomo. See: https://wiki.metacubex.one/en/config/proxy-providers/"
        cl "proxies: []"
        cl ""
        cl "proxy-groups: []"
        cl ""
        cl "rules:"
        cl "  - MATCH,DIRECT"
        warn "No subscription URL — traffic passes through directly until you configure a provider."
    fi
    
    info "config.yaml written."
}

# =============================================================================
# WRITE DOCKER-COMPOSE.YML
# Same approach — explicit printf per line, no heredocs.
# =============================================================================
write_compose() {
    local engine="$1"
    local dashboard="$2"
    local dash_name="$3"
    local cmp="${INSTALL_DIR}/docker-compose.yml"
    
    cl() { printf '%s\n' "$1" >> "$cmp"; }
    : > "$cmp"
    
    cl "services:"
    cl ""
    cl "  mihomo:"
    cl "    image: ${engine}"
    cl "    container_name: mihomo"
    cl "    restart: unless-stopped"
    cl "    network_mode: host"
    cl "    privileged: true"
    cl "    volumes:"
    cl "      - ./config:/root/.config/mihomo"
    cl ""
    cl "  dashboard:"
    cl "    image: ${dashboard}"
    cl "    container_name: ${dash_name}"
    cl "    restart: unless-stopped"
    cl "    ports:"
    cl "      - \"${DASHBOARD_PORT}:80\""
    
    info "docker-compose.yml written."
}

# =============================================================================
# INSTALL
# =============================================================================
do_install() {
    
    echo -e "\n${BOLD}${CYAN}=================================="
    echo -e "  pimiproxy installer"
    echo -e "==================================${NC}\n"
    
    # ── prompts ───────────────────────────────────────────────────────────────
    step "Configuration"
    
    read -rp "  Install directory  [${INSTALL_DIR}]: "   i; INSTALL_DIR=${i:-$INSTALL_DIR}
    read -rp "  Hostname           [${HOSTNAME_NEW}]: "  i; HOSTNAME_NEW=${i:-$HOSTNAME_NEW}
    read -rp "  Dashboard port     [${DASHBOARD_PORT}]: " i; DASHBOARD_PORT=${i:-$DASHBOARD_PORT}
    read -rp "  Mihomo API port    [${API_PORT}]: "       i; API_PORT=${i:-$API_PORT}
    read -rp "  Mixed proxy port   [${MIXED_PORT}]: "    i; MIXED_PORT=${i:-$MIXED_PORT}
    read -rsp "  API secret        [${API_SECRET}]: "    i; API_SECRET=${i:-$API_SECRET}; echo
    
    echo ""
    echo "  ── Clash API subscription URL ──────────────────────────────────────"
    echo "  If your proxy provider gave you a Clash/Mihomo-compatible API URL,"
    echo "  paste it here. It typically looks like:"
    echo "    https://provider.example.com/api?token=abc123"
    echo ""
    echo "  This must be a Clash API URL — not a VPN app download, not a"
    echo "  WireGuard or OpenVPN config file. If you are unsure, leave blank."
    echo "  You can add it to config.yaml at any time after install."
    echo "  ────────────────────────────────────────────────────────────────────"
    echo ""
    read -rp "  Clash API URL [leave blank to skip]: " SUBSCRIPTION_URL
    SUBSCRIPTION_URL="${SUBSCRIPTION_URL:-""}"
    
    echo ""
    echo "  Dashboard:"
    echo "    1) Metacubexd  (default)"
    echo "    2) Zashboard"
    read -rp "  Selection [1-2]: " DASH_CHOICE; DASH_CHOICE=${DASH_CHOICE:-1}
    
    # ── hostname ──────────────────────────────────────────────────────────────
    step "Hostname"
    
    local old_hostname
    old_hostname=$(hostname)
    hostnamectl set-hostname "$HOSTNAME_NEW"
    
    if grep -q "^127\.0\.1\.1" /etc/hosts; then
        sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t${HOSTNAME_NEW}/" /etc/hosts
    else
        printf '127.0.1.1\t%s\n' "$HOSTNAME_NEW" >> /etc/hosts
    fi
    
    if [[ -f /etc/cloud/cloud.cfg ]]; then
        sed -i 's/preserve_hostname: false/preserve_hostname: true/' /etc/cloud/cloud.cfg
        info "cloud-init: preserve_hostname set."
    fi
    
    info "Hostname: ${old_hostname} -> ${HOSTNAME_NEW}"
    
    # ── packages ──────────────────────────────────────────────────────────────
    step "Packages"
    
    apt-get update -qq
    
    apt_install avahi-daemon libnss-mdns
    systemctl enable --now avahi-daemon
    info "avahi-daemon ready."
    
    if ! command -v docker &>/dev/null; then
        info "Installing docker.io..."
        apt_install docker.io
    else
        info "Docker already present: $(docker --version | cut -d' ' -f3 | tr -d ',')"
    fi
    systemctl enable --now docker
    
    if ! docker compose version &>/dev/null 2>&1; then
        info "Installing docker-compose-plugin..."
        apt_install docker-compose-plugin 2>/dev/null \
        || apt_install docker-compose \
        || die "Cannot install any Docker Compose variant."
    fi
    detect_compose
    
    # ── images ────────────────────────────────────────────────────────────────
    step "Container images"
    
    local engine dashboard dash_name
    
    engine=$(load_image "mihomo.tar.gz" "$MIHOMO_IMAGE")
    info "Engine image:    ${engine}"
    
    if [[ "$DASH_CHOICE" == "2" ]]; then
        dashboard=$(load_image "zashboard.tar.gz" "$ZASHBOARD_IMAGE")
        dash_name="zashboard"
    else
        dashboard=$(load_image "metacubexd.tar.gz" "$METACUBEXD_IMAGE")
        dash_name="metacubexd"
    fi
    info "Dashboard image: ${dashboard} (${dash_name})"
    
    # ── directories ───────────────────────────────────────────────────────────
    step "Directories"
    mkdir -p "${INSTALL_DIR}/config"
    info "Created ${INSTALL_DIR}"
    
    # ── write config files ────────────────────────────────────────────────────
    step "Mihomo config"
    write_config
    
    step "Docker Compose"
    write_compose "$engine" "$dashboard" "$dash_name"
    
    # ── clash-system utility ──────────────────────────────────────────────────
    step "clash-system utility"
    
    # Write clash-system line by line — no heredoc, no tab risk
    local cs="/usr/local/bin/clash-system"
    cl() { printf '%s\n' "$1" >> "$cs"; }
    : > "$cs"
    
    cl "#!/usr/bin/env bash"
    cl "# clash-system -- toggle transparent proxy routing through the mihomo TUN"
    cl ""
    cl "RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'"
    cl ""
    cl "[[ \$EUID -eq 0 ]] || exec sudo \"\$0\" \"\$@\""
    cl ""
    cl "iface_up() { [[ -d /sys/class/net/mihomo ]]; }"
    cl ""
    cl "case \"\${1:-}\" in"
    cl "  on)"
    cl "    iface_up || { echo -e \"\${RED}X\${NC} mihomo TUN not found -- is the container running?\"; exit 1; }"
    cl "    ip rule add to 10.0.0.0/8 table main priority 90  2>/dev/null || true"
    cl "    ip rule add fwmark 666     table main priority 100 2>/dev/null || true"
    cl "    ip rule add not fwmark 666 table 101  priority 101 2>/dev/null || true"
    cl "    ip route replace default dev mihomo table 101"
    cl "    echo -e \"System proxy: \${GREEN}ON\${NC}\""
    cl "    ;;"
    cl "  off)"
    cl "    ip rule  del priority 90  2>/dev/null || true"
    cl "    ip rule  del priority 100 2>/dev/null || true"
    cl "    ip rule  del priority 101 2>/dev/null || true"
    cl "    ip route flush table 101  2>/dev/null || true"
    cl "    echo -e \"System proxy: \${RED}OFF\${NC}\""
    cl "    ;;"
    cl "  status)"
    cl "    if ip rule show | grep -q 'priority 101'; then"
    cl "      echo -e \"System proxy: \${GREEN}ON\${NC}\""
    cl "    else"
    cl "      echo -e \"System proxy: \${RED}OFF\${NC}\""
    cl "    fi"
    cl "    iface_up && echo '  TUN interface: up' || echo '  TUN interface: down (container not running?)'"
    cl "    ;;"
    cl "  restart)"
    cl "    \"\$0\" off; sleep 1; \"\$0\" on"
    cl "    ;;"
    cl "  *)"
    cl "    echo 'Usage: clash-system [on|off|status|restart]'"
    cl "    exit 1"
    cl "    ;;"
    cl "esac"
    
    chmod +x "$cs"
    info "/usr/local/bin/clash-system installed."
    
    # ── metadata ──────────────────────────────────────────────────────────────
    write_meta
    
    # ── launch ────────────────────────────────────────────────────────────────
    step "Starting services"
    cd "$INSTALL_DIR"
    compose_run up -d
    info "Containers started."
    
    # ── summary ───────────────────────────────────────────────────────────────
    local lan_ip
    lan_ip=$(hostname -I | awk '{print $1}')
    
    echo -e "\n${BOLD}${GREEN}=================================="
    echo -e "  Installation complete!"
    echo -e "==================================${NC}"
    echo ""
    echo -e "  Dashboard    ->  http://${HOSTNAME_NEW}.local:${DASHBOARD_PORT}"
    echo -e "               ->  http://${lan_ip}:${DASHBOARD_PORT}  (IP fallback)"
    echo -e "  Mihomo API   ->  http://${HOSTNAME_NEW}.local:${API_PORT}"
    echo -e "  Proxy        ->  ${lan_ip}:${MIXED_PORT}  (HTTP/SOCKS5)"
    echo ""
    echo -e "  Pi routing control:"
    echo -e "    sudo clash-system on      # route Pi traffic via proxy"
    echo -e "    sudo clash-system off     # revert to direct"
    echo -e "    sudo clash-system status"
    echo ""
    echo -e "  Config:  ${INSTALL_DIR}/config/config.yaml"
    echo -e "  Restart: cd ${INSTALL_DIR} && $(compose_run echo 2>/dev/null || echo 'docker compose') restart"
    echo ""
    warn ".local mDNS may take ~30s to propagate after first boot."
    warn "Your router's .lan suffix is router-controlled and separate from mDNS."
    echo ""
}

# =============================================================================
# UNINSTALL
# =============================================================================
do_uninstall() {
    
    echo -e "\n${BOLD}${RED}=================================="
    echo -e "  pimiproxy uninstaller"
    echo -e "==================================${NC}\n"
    
    read_meta
    
    confirm "Stop and remove all pimiproxy containers, images, and files?" \
    || { echo "Aborted."; exit 0; }
    
    step "Stopping containers"
    if [[ -f "${INSTALL_DIR}/docker-compose.yml" ]]; then
        cd "$INSTALL_DIR"
        detect_compose
        compose_run down --remove-orphans 2>/dev/null \
        && info "Containers stopped." \
        || warn "Compose down failed — already stopped?"
    else
        for c in mihomo metacubexd zashboard; do
            docker stop "$c" 2>/dev/null && docker rm "$c" 2>/dev/null \
            && info "Removed container: ${c}" || true
        done
    fi
    
    step "Removing Docker images"
    for img in "$MIHOMO_IMAGE" "$METACUBEXD_IMAGE" "$ZASHBOARD_IMAGE"; do
        if docker image inspect "$img" &>/dev/null 2>&1; then
            docker rmi "$img" \
            && info "Removed: ${img}" \
            || warn "Could not remove ${img} — in use elsewhere?"
        fi
    done
    
    step "Clearing routing rules"
    [[ -x /usr/local/bin/clash-system ]] \
    && /usr/local/bin/clash-system off 2>/dev/null || true
    info "Routing rules cleared."
    
    step "Removing files"
    rm -rf "$INSTALL_DIR"
    info "Removed ${INSTALL_DIR}"
    rm -f /usr/local/bin/clash-system
    info "Removed /usr/local/bin/clash-system"
    
    step "Hostname"
    if [[ "$(hostname)" == "$HOSTNAME_NEW" ]]; then
        if confirm "Reset hostname '${HOSTNAME_NEW}' back to 'raspberrypi'?"; then
            hostnamectl set-hostname raspberrypi
            sed -i "s/^127\.0\.1\.1.*/127.0.1.1\traspberrypi/" /etc/hosts
            info "Hostname reset to 'raspberrypi'."
        fi
    fi
    
    step "Optional package removal"
    if confirm "Remove avahi-daemon? Only if nothing else on this Pi needs mDNS."; then
        apt-get remove -y avahi-daemon libnss-mdns >/dev/null \
        && info "avahi-daemon removed."
    fi
    
    echo -e "\n${BOLD}${GREEN}Uninstall complete.${NC}\n"
}

# =============================================================================
# ENTRYPOINT
# =============================================================================
case "${1:-install}" in
    install)   do_install   ;;
    uninstall) do_uninstall ;;
    *)
        echo "Usage: sudo bash $0 [install|uninstall]"
        exit 1
    ;;
esac