#!/bin/bash

# --- Configuration (Matches installer.sh) ---
INSTALL_DIR="/opt/pimiproxy"
DEFAULT_HOSTNAME="pidev"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "\033[0;33m[WARN]\033[0m $1"; }

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: Run as root (sudo).${NC}"
   exit 1
fi

echo -e "${RED}--- pimiproxy Surgical Uninstaller ---${NC}"
echo "This will remove services and system changes, but keep installer archives."
read -p "Proceed with uninstall? (y/n): " CONFIRM
if [[ "$CONFIRM" != "y" ]]; then exit 0; fi

# 1. Docker Stack & Volume Cleanup
if [ -d "$INSTALL_DIR" ]; then
    log "Stopping containers and wiping volumes..."
    cd "$INSTALL_DIR" || exit
    # -v removes the named volumes (clean state for next install)
    docker compose down -v --remove-orphans 2>/dev/null || docker-compose down -v --remove-orphans 2>/dev/null
    cd - > /dev/null
    
    log "Removing installation directory: $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
else
    warn "Install directory not found. Cleaning containers by name..."
    docker rm -f mihomo metacubexd zashboard 2>/dev/null
fi

# 2. Image Cleanup (Removing from Docker Engine)
log "Removing project images from Docker engine..."
docker rmi -f metacubex/mihomo:latest ghcr.io/metacubex/metacubexd:latest ghcr.io/zephyruso/zashboard:latest 2>/dev/null

# 3. System Reversion
log "Reverting hostname to $DEFAULT_HOSTNAME..."
hostnamectl set-hostname "$DEFAULT_HOSTNAME"
raspi-config nonint do_hostname "$DEFAULT_HOSTNAME"

log "Restoring /etc/hosts..."
sed -i "s/127.0.1.1.*/127.0.1.1\t$DEFAULT_HOSTNAME/g" /etc/hosts

if [ -f /etc/cloud/cloud.cfg ]; then
    log "Reverting cloud-init persistence..."
    sed -i 's/preserve_hostname: true/preserve_hostname: false/' /etc/cloud/cloud.cfg
fi

# 4. mDNS Cleanup
read -p "Disable mDNS (Avahi)? (y/n): " REMOVE_AVAHI
if [[ "$REMOVE_AVAHI" == "y" ]]; then
    systemctl stop avahi-daemon
    systemctl disable avahi-daemon
    log "mDNS service disabled."
fi

echo -e "\n${GREEN}================================================"
echo "          UNINSTALL COMPLETE"
echo "================================================"
log "System restored. Installer archives (.tar.gz) were preserved."
read -p "Reboot now? (y/n): " REBOOT
if [[ "$REBOOT" == "y" ]]; then
    reboot
fi
