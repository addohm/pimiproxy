
# Define colors for better visibility
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_step() { echo -e "${GREEN}[STEP]${NC} $1"; }
log_err() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo -e "\n---------- Clash System-wide Proxy  ----------"
echo "Script path: /usr/local/bin/clash-system"
echo "Login Toast: /etc/profile.d/clash-system-status.sh"
echo -e "\n"

# 1. Check if Mihomo is running
if [ ! -d /sys/class/net/mihomo ]; then
    log_err "Mihomo service is not running. Interface 'mihomo' not found."
fi

case "$1" in
                     on)
    log_step "Initializing System-Wide Proxy..."
    
    # 2. Cleanup old rules
    log_step "Cleaning up existing routing rules..."
    sudo ip rule del to 10.0.0.0/24 table main 2>/dev/null
    sudo ip rule del fwmark 666 table main 2>/dev/null
    sudo ip rule del not fwmark 666 table 101 2>/dev/null
    sudo ip route flush table 101 2>/dev/null

    # 3. Apply Bypass for Local/SSH
    log_step "Applying SSH and Local network bypass (10.0.0.0/24)..."
    sudo ip rule add to 10.0.0.0/24 table main priority 90 || log_err "Failed to add local bypa>

    # 4. Apply Clash Bypass (Routing Mark)
    log_step "Applying Clash engine bypass (Mark 666)..."
    sudo ip rule add fwmark 666 table main priority 100 || log_err "Failed to add routing mark >
    
    # 5. Redirect System Traffic
    log_step "Redirecting all other system traffic to Mihomo (Table 101)..."
    sudo ip rule add not fwmark 666 table 101 priority 101 || log_err "Failed to add system red>
    sudo ip route add default dev mihomo table 101 || log_err "Failed to add default route to T>
    
    log_step "System-wide proxy is now ACTIVE."
    ;;
  off)
    log_step "Disabling System-Wide Proxy..."
    sudo ip rule del to 10.0.0.0/24 table main 2>/dev/null
    sudo ip rule del fwmark 666 table main 2>/dev/null
    sudo ip rule del not fwmark 666 table 101 2>/dev/null
    sudo ip route flush table 101 2>/dev/null
    log_step "System-wide proxy is now DISABLED."
    ;;
  status)
    # Check if priority 101 exists in the routing rules
    if ip rule show | grep -q "^101:"; then
        echo -e "clash-system System Proxy: ${GREEN}ON${NC}"
    else
        echo -e "clash-system System Proxy: ${RED}OFF${NC}"
    fi
    ;;  *)
    echo "Usage: clash-system [on|off|status]"
    ;;
esac

