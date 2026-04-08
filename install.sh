#!/bin/bash

# --- Default Variables ---
INSTALL_DIR="/opt/pimiproxy"
PROXY_HOSTNAME="pimiproxy"
PROXY_PORT="7890"
DASHBOARD_PORT="8080"
API_PORT="9292"

# Explicit GHCR Paths (Never leave these to default to docker.io)
GHCR_ENGINE="ghcr.io/metacubex/mihomo:latest"
GHCR_METACUBEXD="ghcr.io/metacubex/metacubexd:latest"
GHCR_ZASHBOARD="ghcr.io/zephyr-dev/zashboard:latest"

# Mirror Fallbacks (Using hub.rat.dev which was successful in your logs)
MIRROR_ENGINE="hub.rat.dev/metacubex/mihomo:latest"
MIRROR_ZASHBOARD="hub.rat.dev/zephyr-dev/zashboard:latest"

GREEN='\033[0;32m'
NC='\033[0m'

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $1"; }
error() { echo -e "\033[0;31m[ERROR]\033[0m $1"; exit 1; }

if [[ $EUID -ne 0 ]]; then error "Please run as root (sudo)."; fi

echo -e "${GREEN}--- pimiproxy Installer v1.23 ---${NC}"

# --- 1. MODE SELECTION ---
echo -e "Choose installation mode:"
echo -e "1) Express (Accept default ports/paths)"
echo -e "2) Manual  (Customize ports/paths)"
read -p "Selection [1]: " MODE_CHOICE
INSTALL_MODE="express"
[[ "$MODE_CHOICE" == "2" ]] && INSTALL_MODE="manual"

# --- 2. CONFIGURATION ---
if [ "$INSTALL_MODE" == "manual" ]; then
    read -p "Install directory [$INSTALL_DIR]: " USER_DIR
    INSTALL_DIR=${USER_DIR:-$INSTALL_DIR}
    read -p "System hostname [$PROXY_HOSTNAME]: " USER_HOST
    PROXY_HOSTNAME=${USER_HOST:-$PROXY_HOST}
    read -p "Proxy Port [$PROXY_PORT]: " USER_PORT
    PROXY_PORT=${USER_PORT:-$PROXY_PORT}
    read -p "Dashboard Port [$DASHBOARD_PORT]: " USER_DASH
    DASHBOARD_PORT=${USER_DASH:-$DASHBOARD_PORT}
fi

# --- 3. MANDATORY DASHBOARD SELECTION ---
echo -e "\n--- Dashboard Selection ---"
echo "1) MetaCube(XD) [standard]"
echo "2) Zashboard    [power users]"
read -p "Selection [1]: " DASH_CHOICE
if [[ "$DASH_CHOICE" == "2" ]]; then
    log "Zashboard selected."
    DASHBOARD_IMAGE=$GHCR_ZASHBOARD
    DASH_FALLBACK=$MIRROR_ZASHBOARD
else
    log "MetaCube(XD) selected."
    DASHBOARD_IMAGE=$GHCR_METACUBEXD
    DASH_FALLBACK=""
fi

# --- 4. DOCKER & NETWORK PREP ---
if ! command -v docker &> /dev/null; then
    log "Installing Docker..."
    curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
    systemctl enable --now docker
fi

# Clear any session that might cause 'denied' errors on GHCR
docker logout ghcr.io >/dev/null 2>&1

log "Setting identity to '$PROXY_HOSTNAME.local'..."
hostnamectl set-hostname "$PROXY_HOSTNAME"
sed -i "s/127.0.1.1.*/127.0.1.1 $PROXY_HOSTNAME/" /etc/hosts
apt-get update && apt-get install -y avahi-daemon

# --- 5. PULL ENGINE ---
log "Pulling Engine from GHCR..."
if ! docker pull "$GHCR_ENGINE"; then
    warn "Direct GHCR pull failed. Trying mirror fallback..."
    docker pull "$MIRROR_ENGINE" && docker tag "$MIRROR_ENGINE" "$GHCR_ENGINE"
fi

# --- 6. PULL DASHBOARD ---
log "Pulling Dashboard from GHCR..."
if ! docker pull "$DASHBOARD_IMAGE"; then
    if [ -n "$DASH_FALLBACK" ]; then
        warn "Dashboard GHCR pull failed. Trying mirror fallback..."
        docker pull "$DASH_FALLBACK" && docker tag "$DASH_FALLBACK" "$DASHBOARD_IMAGE"
    else
        error "Failed to pull $DASHBOARD_IMAGE. GHCR is denying the request and no fallback is set."
    fi
fi

# --- 7. GENERATE STACK ---
log "Generating Docker stack..."
mkdir -p "$INSTALL_DIR/config"
cat <<EOF > "$INSTALL_DIR/docker-compose.yml"
services:
  mihomo:
    image: $GHCR_ENGINE
    container_name: pimiproxy-engine
    restart: always
    volumes: ["./config:/etc/mihomo"]
    ports: ["$PROXY_PORT:$PROXY_PORT", "$API_PORT:$API_PORT"]
  dashboard:
    image: $DASHBOARD_IMAGE
    container_name: pimiproxy-ui
    restart: always
    ports: ["$DASHBOARD_PORT:80"]
    environment:
      - DEFAULT_HOST=$PROXY_HOSTNAME.local
      - DEFAULT_PORT=$API_PORT
EOF

if [ ! -f "$INSTALL_DIR/config/config.yaml" ]; then
    cat <<EOF > "$INSTALL_DIR/config/config.yaml"
mixed-port: $PROXY_PORT
allow-lan: true
mode: rule
external-controller: 0.0.0.0:$API_PORT
secret: ""
EOF
fi

# --- 8. LAUNCH ---
cd "$INSTALL_DIR"
docker compose up -d || error "Failed to start containers."

echo -e "\n${GREEN}INSTALLATION SUCCESSFUL${NC}"
echo -e "Dashboard: http://$PROXY_HOSTNAME.local:$DASHBOARD_PORT"
