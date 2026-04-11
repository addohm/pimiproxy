#!/bin/bash

###################################################
# Toggle your use of a remote mihomo proxy on\off #
###################################################

# CONFIGURATION
PROXY_IP="10.0.0.250"
# Automatically detects your current router IP to use as fallback
DEFAULT_GW=$(ip route | grep default | awk '{print $3}' | head -n 1)
GATEWAY_FILE="/tmp/original_gateway"

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then 
  echo "Please run as root (sudo)"
  exit
fi

case "$1" in
  on)
    echo "Enabling Mihomo Proxy..."
    # Save current gateway if not already saved
    if [ ! -f $GATEWAY_FILE ]; then
        echo $DEFAULT_GW > $GATEWAY_FILE
    fi
    
    # Change Default Gateway
    ip route del default
    ip route add default via $PROXY_IP
    
    # Backup and Change DNS
    cp /etc/resolv.conf /etc/resolv.conf.bak
    echo "nameserver $PROXY_IP" > /etc/resolv.conf
    
    echo "Proxy is now ON. Traffic routed through $PROXY_IP"
    ;;
    
  off)
    echo "Disabling Mihomo Proxy..."
    # Retrieve original gateway
    if [ -f $GATEWAY_FILE ]; then
        ORIGINAL_GW=$(cat $GATEWAY_FILE)
    else
        ORIGINAL_GW="10.0.0.1" # Manual fallback
    fi
    
    # Restore Default Gateway
    ip route del default
    ip route add default via $ORIGINAL_GW
    
    # Restore DNS
    if [ -f /etc/resolv.conf.bak ]; then
        mv /etc/resolv.conf.bak /etc/resolv.conf
    else
        echo "nameserver $ORIGINAL_GW" > /etc/resolv.conf
    fi
    
    echo "Proxy is now OFF. Traffic routed through $ORIGINAL_GW"
    ;;

  status)
    CURRENT_GW=$(ip route | grep default | awk '{print $3}')
    echo "Current Gateway: $CURRENT_GW"
    if [ "$CURRENT_GW" == "$PROXY_IP" ]; then
        echo "Status: PROXY ENABLED"
    else
        echo "Status: PROXY DISABLED"
    fi
    ;;

  *)
    echo "Usage: sudo $0 {on|off|status}"
    exit 1
esac
