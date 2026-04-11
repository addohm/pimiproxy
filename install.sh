#!/bin/bash
# --- Preparation Commands ---
# docker pull --platform linux/arm64 ghcr.io/zephyruso/zashboard:latest
# docker pull --platform linux/arm64 ghcr.io/metacubex/metacubexd:latest
# docker pull --platform linux/arm64 ghcr.io/metacubex/mihomo:latest
# docker save ghcr.io/zephyruso/zashboard:latest | gzip > zashboard.tar.gz
# docker save ghcr.io/metacubex/metacubexd:latest | gzip > metacubexd.tar.gz
# docker save metacubex/mihomo:latest | gzip > mihomo.tar.gz

INSTALL_DIR="/opt/pimiproxy"
PROXY_HOSTNAME="pimiproxy"
DASHBOARD_PORT="8080"

FALLBACK_ENGINE="ghcr.io/metacubex/mihomo:latest"
FALLBACK_METACUBEXD="ghcr.io/metacubex/metacubexd:latest"
FALLBACK_ZASHBOARD="ghcr.io/zephyruso/zashboard:latest"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# FIXED: Redirect logs to stderr (>&2) so they aren't captured by variables
log() { echo -e "${GREEN}[INFO]${NC} $1" >&2; }
error() { 
    echo -e "${RED}[ERROR]${NC} $1" >&2
    if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
        echo "Debug: Showing hidden characters in YAML (Look for ^I or M-):" >&2
        cat -A "$INSTALL_DIR/docker-compose.yml" | head -n 15 >&2
    fi
    exit 1 
}

if [[ $EUID -ne 0 ]]; then error "Please run as root (sudo)."; fi

echo -e "${GREEN}--- pimiproxy Installer v1.34 ---${NC}"

# --- Setup Mode ---
echo "Choose installation mode:"
echo "1) Express (Default settings)"
echo "2) Manual (Custom ports/paths)"
read -p "Selection [1-2]: " MODE

if [[ "$MODE" == "2" ]]; then
    read -p "Install Directory [$INSTALL_DIR]: " input_dir
    INSTALL_DIR=${input_dir:-$INSTALL_DIR}
    read -p "Hostname [$PROXY_HOSTNAME]: " input_host
    PROXY_HOSTNAME=${input_host:-$PROXY_HOSTNAME}
    read -p "Dashboard Port [$DASHBOARD_PORT]: " input_d_port
    DASHBOARD_PORT=${input_d_port:-$DASHBOARD_PORT}
fi

# --- System & mDNS ---
log "Configuring hostname and mDNS..."
hostnamectl set-hostname "$PROXY_HOSTNAME"
raspi-config nonint do_hostname "$PROXY_HOSTNAME"
if [ -f /etc/cloud/cloud.cfg ]; then
    sed -i 's/preserve_hostname: false/preserve_hostname: true/' /etc/cloud/cloud.cfg
fi
sed -i "s/127.0.1.1.*/127.0.1.1\t$PROXY_HOSTNAME/g" /etc/hosts

# --- Dependencies ---
log "Ensuring Docker and mDNS are ready..."
apt-get update -qq && apt-get install -y avahi-daemon docker.io docker-compose-v3 > /dev/null 2>&1 || \
apt-get install -y avahi-daemon docker.io docker-compose-plugin > /dev/null

systemctl enable --now avahi-daemon docker

# --- Image Loading (Fixed capturing logic) ---
load_and_identify() {
    local archive=$1
    local fallback=$2
    local tag=""

    if [[ -f "$archive" ]]; then
        log "Loading $archive..." # This now goes to stderr safely
        # Capture ONLY the tag and strip all whitespace/hidden chars
        tag=$(gunzip -c "$archive" | docker load | grep "Loaded image:" | tail -n 1 | awk '{print $NF}' | tr -d '\r\n[:cntrl:]')
    fi

    if [[ -z "$tag" ]]; then
        log "$archive not found. Pulling $fallback..."
        docker pull --platform linux/arm64 "$fallback" > /dev/null 2>&1
        tag="$fallback"
    fi

    # Output ONLY the clean tag to stdout for variable assignment
    echo -n "$tag"
}

# Acquire Engine
FINAL_ENGINE=$(load_and_identify "mihomo.tar.gz" "$FALLBACK_ENGINE") || error "Failed to acquire Mihomo."
log "Engine Tag: $FINAL_ENGINE"

# Dashboard Selection
echo -e "\nSelect Dashboard:"
echo "1) Standard (metacubexd)"
echo "2) Power User (zashboard)"
read -p "Selection [1-2]: " DASH_CHOICE

if [[ "$DASH_CHOICE" == "2" ]]; then
    FINAL_DASHBOARD=$(load_and_identify "zashboard.tar.gz" "$FALLBACK_ZASHBOARD") || error "Failed to acquire Zashboard."
    DASH_NAME="zashboard"
else
    FINAL_DASHBOARD=$(load_and_identify "metacubexd.tar.gz" "$FALLBACK_METACUBEXD") || error "Failed to acquire Metacubexd."
    DASH_NAME="metacubexd"
fi
log "Dashboard Tag: $FINAL_DASHBOARD"

# --- Deployment ---
log "Generating docker-compose.yml..."
mkdir -p "$INSTALL_DIR"

# Using EOF without indentation to ensure clean spaces
cat <<EOF > "$INSTALL_DIR/docker-compose.yml"
services:
  mihomo:
    image: $FINAL_ENGINE
    container_name: mihomo
    restart: always
    network_mode: host
    volumes:
      - ./config:/root/.config/mihomo

  dashboard:
    image: $FINAL_DASHBOARD
    container_name: $DASH_NAME
    restart: always
    ports:
      - "$DASHBOARD_PORT:80"
EOF

# Final safety scrub of the YAML file
sed -i 's/\t/  /g' "$INSTALL_DIR/docker-compose.yml"

log "Starting stack..."
cd "$INSTALL_DIR" || error "Path error."

if docker compose version >/dev/null 2>&1; then
    docker compose up -d || error "Compose V2 failed."
else
    docker-compose up -d || error "Compose V1 failed."
fi

echo -e "\n${GREEN}================================================"
echo "          INSTALLATION SUCCESSFUL"
echo "================================================"
log "Access: http://$PROXY_HOSTNAME.local:$DASHBOARD_PORT"
