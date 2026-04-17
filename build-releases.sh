#!/usr/bin/env bash
# build-releases.sh — generate release archives for pimiproxy and pimiproxy-ultralite
# Requires: Docker images already built (via rebuild.sh) and mihomo binaries in bin/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASES="$SCRIPT_DIR/releases"
DOCKER_IMAGES="$SCRIPT_DIR/docker-images"
BIN_DIR="$SCRIPT_DIR/bin"

bold=$(tput bold 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
cyan=$(tput setaf 6 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)

step()  { echo "${bold}${cyan}→ $*${reset}"; }
ok()    { echo "${bold}${green}✓ $*${reset}"; }
fail()  { echo "${bold}${red}✗ $*${reset}" >&2; }

usage() {
    cat <<EOF
${bold}Usage:${reset} $0 [--full] [--ultralite] [--all]

Build release archives in ./releases/

${bold}Options:${reset}
  --full          Build pimiproxy (Docker) archives: arm64, amd64
  --ultralite     Build pimiproxy-ultralite archives: arm64, amd64, armv7
  --all           Build all of the above (default if no flags given)

${bold}Prerequisites:${reset}
  Full variant:
    - Docker images built via rebuild.sh (multi-arch .tar.gz in docker-images/)
    - Geodata files in docker-images/ (geoip.metadb, geosite.dat, country.mmdb)

  Ultralite variant:
    - Mihomo .gz binaries in bin/ (mihomo-linux-amd64-*.gz, etc.)
    - Geodata files in docker-images/

${bold}Output:${reset}
  releases/pimiproxy-arm64.tar.gz
  releases/pimiproxy-amd64.tar.gz
  releases/pimiproxy-ultralite-arm64.tar.gz
  releases/pimiproxy-ultralite-amd64.tar.gz
  releases/pimiproxy-ultralite-armv7.tar.gz
EOF
    exit 0
}

# ── Argument parsing ─────────────────────────────────────────────────────────
DO_FULL=0
DO_ULTRALITE=0

if [[ $# -eq 0 ]]; then
    DO_FULL=1; DO_ULTRALITE=1
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --full)      DO_FULL=1 ;;
        --ultralite) DO_ULTRALITE=1 ;;
        --all)       DO_FULL=1; DO_ULTRALITE=1 ;;
        -h|--help)   usage ;;
        *)           echo "Unknown flag: $1"; usage ;;
    esac
    shift
done

# ── Verify prerequisites ────────────────────────────────────────────────────
_check_geodata() {
    for f in geoip.metadb geosite.dat country.mmdb; do
        [[ -f "$DOCKER_IMAGES/$f" ]] || { fail "Missing: docker-images/$f"; exit 1; }
    done
}

mkdir -p "$RELEASES"

# ── Helper: build a pimiproxy full archive for one arch ──────────────────────
_build_full() {
    local arch="$1"  # arm64 or amd64
    local tag="${arch}"
    local archive="$RELEASES/pimiproxy-${arch}.tar.gz"

    step "pimiproxy-${arch}"

    # Extract single-arch Docker images from multi-arch tarballs
    local img_tmp
    img_tmp=$(mktemp -d)
    for img in mihomo metacubexd zashboard pimiproxy-api pimiproxy-ui; do
        local src="$DOCKER_IMAGES/${img}.tar.gz"
        [[ -f "$src" ]] || { fail "Missing: docker-images/${img}.tar.gz"; rm -rf "$img_tmp"; exit 1; }

        # Load multi-arch, save single-arch
        docker load -i "$src" >/dev/null 2>&1
        docker save "${img}:${tag}" | gzip > "$img_tmp/${img}.tar.gz"
    done

    # Stage the archive
    local staging
    staging=$(mktemp -d)
    mkdir -p "$staging/pimiproxy/docker-images" \
             "$staging/pimiproxy/pimiproxy-api" \
             "$staging/pimiproxy/pimiproxy-ui"

    cp "$SCRIPT_DIR/pimiproxy.sh" "$SCRIPT_DIR/pimiproxy-core.py" "$staging/pimiproxy/"
    cp "$img_tmp"/*.tar.gz "$staging/pimiproxy/docker-images/"
    cp "$DOCKER_IMAGES/geoip.metadb" "$DOCKER_IMAGES/geosite.dat" "$DOCKER_IMAGES/country.mmdb" \
       "$staging/pimiproxy/docker-images/"

    rsync -a --exclude='__pycache__' "$SCRIPT_DIR/pimiproxy-api/" "$staging/pimiproxy/pimiproxy-api/"
    rsync -a --exclude='__pycache__' --exclude='node_modules' "$SCRIPT_DIR/pimiproxy-ui/" "$staging/pimiproxy/pimiproxy-ui/"

    tar czf "$archive" -C "$staging" pimiproxy
    ok "$(basename "$archive")  ($(du -sh "$archive" | cut -f1))"

    rm -rf "$img_tmp" "$staging"
}

# ── Helper: build a pimiproxy-ultralite archive for one arch ─────────────────
_build_ultralite() {
    local arch="$1"      # arm64, amd64, or armv7
    local archive="$RELEASES/pimiproxy-ultralite-${arch}.tar.gz"

    step "pimiproxy-ultralite-${arch}"

    # Find the matching mihomo binary
    local bin_match
    bin_match=$(ls "$BIN_DIR"/mihomo-linux-${arch}*.gz 2>/dev/null | head -1)
    if [[ -z "$bin_match" ]]; then
        # Try alternative naming: amd64 → x86_64, arm64 → aarch64
        case "$arch" in
            amd64) bin_match=$(ls "$BIN_DIR"/mihomo-linux-amd64*.gz "$BIN_DIR"/mihomo-x86_64*.gz 2>/dev/null | head -1) ;;
            arm64) bin_match=$(ls "$BIN_DIR"/mihomo-linux-arm64*.gz "$BIN_DIR"/mihomo-aarch64*.gz 2>/dev/null | head -1) ;;
            armv7) bin_match=$(ls "$BIN_DIR"/mihomo-linux-armv7*.gz "$BIN_DIR"/mihomo-armv7l*.gz 2>/dev/null | head -1) ;;
        esac
    fi
    [[ -n "$bin_match" ]] || { fail "No mihomo binary found for ${arch} in bin/"; return 1; }

    local staging
    staging=$(mktemp -d)
    mkdir -p "$staging/pimiproxy-ultralite/bin" \
             "$staging/pimiproxy-ultralite/docker-images"

    cp "$SCRIPT_DIR/pimiproxy-ultralite.sh" "$SCRIPT_DIR/pimiproxy-ultralite-core.py" \
       "$staging/pimiproxy-ultralite/"
    cp "$bin_match" "$staging/pimiproxy-ultralite/bin/"
    cp "$DOCKER_IMAGES/geoip.metadb" "$DOCKER_IMAGES/geosite.dat" "$DOCKER_IMAGES/country.mmdb" \
       "$staging/pimiproxy-ultralite/docker-images/"

    tar czf "$archive" -C "$staging" pimiproxy-ultralite
    ok "$(basename "$archive")  ($(du -sh "$archive" | cut -f1))"

    rm -rf "$staging"
}

# ── Build ────────────────────────────────────────────────────────────────────
echo
echo "${bold}${cyan}pimiproxy release builder${reset}"
echo "${bold}────────────────────────────────────────${reset}"
echo

_check_geodata

if [[ $DO_FULL -eq 1 ]]; then
    _build_full arm64
    _build_full amd64
fi

if [[ $DO_ULTRALITE -eq 1 ]]; then
    _build_ultralite arm64
    _build_ultralite amd64
    _build_ultralite armv7
fi

echo
echo "${bold}────────────────────────────────────────${reset}"
echo "${bold}${green}Done.${reset} Archives in ${bold}releases/${reset}:"
ls -lh "$RELEASES"/*.tar.gz 2>/dev/null | awk '{printf "  %-45s %s\n", $NF, $5}'
echo
