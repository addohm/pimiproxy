#!/usr/bin/env bash
# test-proxy.sh — test proxy connectivity across all proxy groups
# Usage: ./test-proxy.sh [host:port]
# Default: 127.0.0.1:7890

set -euo pipefail

PROXY="${1:-127.0.0.1:7890}"
PROXY_URL="http://${PROXY}"
TIMEOUT=10

# ── Colours ──────────────────────────────────────────────────────────────────
G=$'\033[0;32m'   # green
R=$'\033[0;31m'   # red
Y=$'\033[1;33m'   # yellow
C=$'\033[0;36m'   # cyan
B=$'\033[1m'      # bold
D=$'\033[2m'      # dim
N=$'\033[0m'      # reset

SEP="${D}  ────────────────────────────────────────────────────────────${N}"

# ── Test helper ──────────────────────────────────────────────────────────────
pass=0; fail=0; total=0

test_url() {
    local group="$1" url="$2" expect="${3:-2}" # expect: 2=2xx, 3=3xx redirect
    local result code time_s

    total=$((total + 1))
    result=$(curl -x "$PROXY_URL" -s -o /dev/null \
        -w "%{http_code} %{time_total}" \
        --max-time "$TIMEOUT" "$url" 2>&1) || true

    code="${result%% *}"
    time_s="${result##* }"

    # Format latency
    local ms
    ms=$(awk "BEGIN {printf \"%.0f\", $time_s * 1000}" 2>/dev/null || echo "?")

    # Determine pass/fail
    local status icon
    if [[ "$code" == "000" ]]; then
        icon="${R}✗${N}"
        status="${R}TIMEOUT${N}"
        fail=$((fail + 1))
    elif [[ "$code" =~ ^[23] ]]; then
        icon="${G}✓${N}"
        status="${G}${code}${N} ${D}${ms}ms${N}"
        pass=$((pass + 1))
    elif [[ "$code" == "403" ]]; then
        # 403 means we reached the server (e.g. Cloudflare block on curl)
        icon="${Y}~${N}"
        status="${Y}${code}${N} ${D}${ms}ms (reached server, blocked without session)${N}"
        pass=$((pass + 1))
    else
        icon="${R}✗${N}"
        status="${R}${code}${N} ${D}${ms}ms${N}"
        fail=$((fail + 1))
    fi

    printf "  %s  %-20s  %-30s  %s\n" "$icon" "$group" "$url" "$status"
}

# ── Header ───────────────────────────────────────────────────────────────────
echo
echo "${C}${B}  pimiproxy proxy group test${N}"
echo "${SEP}"
echo "  Proxy: ${B}${PROXY}${N}"
echo "${SEP}"
echo

# ── Quick connectivity check ─────────────────────────────────────────────────
_check=$(curl -x "$PROXY_URL" -s -o /dev/null -w "%{http_code}" --max-time 5 http://www.gstatic.com/generate_204 2>&1) || true
if [[ "$_check" != "204" ]]; then
    echo "  ${R}✗  Proxy at ${PROXY} is not responding. Is Mihomo running?${N}"
    echo
    exit 1
fi
echo "  ${G}✓${N}  Proxy responding"
echo

# ── DIRECT (China — should bypass proxy) ─────────────────────────────────────
echo "  ${B}DIRECT${N} ${D}(China sites — routed direct, no proxy)${N}"
echo "${SEP}"
test_url "DIRECT" "http://www.baidu.com"
test_url "DIRECT" "https://www.bilibili.com"
echo

# ── General (general international) ────────────────────────────────────────
echo "  ${B}General${N} ${D}(general international traffic)${N}"
echo "${SEP}"
test_url "General" "https://www.google.com"
test_url "General" "https://www.twitter.com"
echo

# ── AI ───────────────────────────────────────────────────────────────────────
echo "  ${B}AI${N} ${D}(OpenAI, Anthropic, Copilot)${N}"
echo "${SEP}"
test_url "AI" "https://www.openai.com"
test_url "AI" "https://claude.ai"
echo

# ── Google/Youtube ───────────────────────────────────────────────────────────
echo "  ${B}Google/Youtube${N}"
echo "${SEP}"
test_url "Google/Youtube" "https://www.youtube.com"
test_url "Google/Youtube" "https://mail.google.com"
echo

# ── Netflix ──────────────────────────────────────────────────────────────────
echo "  ${B}Netflix${N}"
echo "${SEP}"
test_url "Netflix" "https://www.netflix.com"
echo

# ── International Media ──────────────────────────────────────────────────────
echo "  ${B}International Media${N}"
echo "${SEP}"
test_url "Intl Media" "https://www.bbc.co.uk"
test_url "Intl Media" "https://www.reddit.com"
echo

# ── Apple ────────────────────────────────────────────────────────────────────
echo "  ${B}Apple${N}"
echo "${SEP}"
test_url "Apple" "https://www.apple.com"
echo

# ── Online Storage ───────────────────────────────────────────────────────────
echo "  ${B}Online Storage${N}"
echo "${SEP}"
test_url "Storage" "https://www.dropbox.com"
test_url "Storage" "https://drive.google.com"
echo

# ── Summary ──────────────────────────────────────────────────────────────────
echo "${SEP}"
if [[ $fail -eq 0 ]]; then
    echo "  ${G}${B}All ${total} tests passed.${N}"
else
    echo "  ${B}${pass}/${total} passed${N}, ${R}${fail} failed${N}"
    echo "  ${D}Failures are typically caused by provider node issues, not pimiproxy.${N}"
fi
echo "${SEP}"
echo
