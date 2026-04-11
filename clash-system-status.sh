#!/bin/bash
# Only run for interactive shells
if [[ $- == *i* ]]; then
    echo -e "\n---------- System-wide Proxy Status ----------"
    /usr/local/bin/clash-system status
    echo "------------------------------------"
    echo -e "\n"
fi
