#!/bin/bash

INSTALL_DIR="/opt/pimiproxy"

if [[ $EUID -ne 0 ]]; then echo "Run as root."; exit 1; fi

echo "Stopping and removing pimiproxy..."
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR" && docker compose down 2>/dev/null
    rm -rf "$INSTALL_DIR"
    echo "Containers and directory removed."
fi

echo "Uninstallation complete."
