#!/bin/bash

# --- CONFIGURATION ---
PI_USER="addohm"
PI_HOST="pimiproxy.local"  # Or use the IP address
PI_DEST="~/pi-proxy"

# Image Tags
ENGINE="metacubex/mihomo:latest"
METACUBEXD="ghcr.io/metacubex/metacubexd:latest"
ZASHBOARD="ghcr.io/zephyruso/zashboard:latest"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[PREP]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }

# 1. Cleanup old local tars
log "Cleaning up old archives..."
rm -f mihomo.tar.gz metacubexd.tar.gz zashboard.tar.gz

# 2. Pull ARM64 versions explicitly
log "Pulling ARM64 images from registries..."
docker pull --platform linux/arm64 $ENGINE
docker pull --platform linux/arm64 $METACUBEXD
docker pull --platform linux/arm64 $ZASHBOARD

# 3. Save and Compress
log "Saving images to compressed archives..."
docker save $ENGINE | gzip > mihomo.tar.gz
docker save $METACUBEXD | gzip > metacubexd.tar.gz
docker save $ZASHBOARD | gzip > zashboard.tar.gz

# 4. Verify Architecture (Optional but recommended)
log "Verifying internal architecture is arm64..."
docker image inspect $ENGINE --format '{{.Architecture}}'

# 5. Send to Pi
log "Transferring files to $PI_USER@$PI_HOST..."
# Ensure the directory exists on the Pi first
ssh $PI_USER@$PI_HOST "mkdir -p $PI_DEST"
# Use rsync to transfer the tars
rsync -avP mihomo.tar.gz metacubexd.tar.gz zashboard.tar.gz $PI_USER@$PI_HOST:$PI_DEST/

success "All images are ready on the Pi. You can now run sudo ./install.sh"
