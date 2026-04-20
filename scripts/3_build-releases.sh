#!/usr/bin/env bash
# 3_build-releases.sh — generate release archives for pimiproxy and pimiproxy-ultralite
# Requires: Docker images built via 2_rebuild.sh and mihomo binaries in bin/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RELEASES="$ROOT_DIR/releases"
DOCKER_IMAGES="$ROOT_DIR/docker-images"
BIN_DIR="$ROOT_DIR/bin"

# Architectures we try to produce releases for.
# Full variant: no 386 — requires metacubexd, zashboard, and
# pimiproxy-ui which all lack 386 upstream builds.
# Ultralite variant: all four — mihomo ships a 386 binary.
FULL_ARCHES=(amd64 arm64 armv7)
ULTRALITE_ARCHES=(amd64 arm64 armv7 386)

bold=$(tput bold 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
cyan=$(tput setaf 6 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)
dim=$(tput dim 2>/dev/null || true)

step()  { echo "${bold}${cyan}→ $*${reset}"; }
ok()    { echo "${bold}${green}✓ $*${reset}"; }
skip()  { echo "  ${dim}↷ $*${reset}"; }
fail()  { echo "${bold}${red}✗ $*${reset}" >&2; }

usage() {
    cat <<EOF
${bold}Usage:${reset} $0 [--full] [--ultralite] [--all]

Build release archives in ./releases/

${bold}Options:${reset}
  --full          Build pimiproxy (Docker) archives for all available arches
  --ultralite     Build pimiproxy-ultralite archives for all available arches
  --all           Both (default if no flags given)

${bold}Supported architectures:${reset}
  amd64   (x86_64)    — Intel/AMD 64-bit PCs, servers, most VMs
  arm64   (aarch64)   — Modern 64-bit ARM SBCs (Pi 4/5, etc.)
  armv7               — 32-bit ARM (Pi 3, older SBCs)
  386     (i386/x86)  — 32-bit Intel/AMD

${bold}Behaviour:${reset}
  If a Docker image or mihomo binary for a given arch is not present,
  that arch is skipped silently (not all upstream images publish every
  platform variant — armv7 and 386 availability varies).

${bold}Output:${reset}
  releases/pimiproxy-<arch>.tar.gz              (full variant)
  releases/pimiproxy-ultralite-<arch>.tar.gz    (ultralite variant)
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

# ── Check if a Docker image archive contains the requested arch ─────────────
_has_arch_tag() {
    local archive="$1" arch="$2" img="$3"
    # Load and check for the tag
    docker load -i "$archive" >/dev/null 2>&1 || return 1
    docker image inspect "${img}:${arch}" >/dev/null 2>&1
}

# ── Build a pimiproxy full archive for one arch ──────────────────────────────
_build_full() {
    local arch="$1"
    local archive="$RELEASES/pimiproxy-${arch}.tar.gz"

    # Required images — without any of these, a full release can't be built
    local required_imgs=(mihomo pimiproxy-api pimiproxy-ui)
    # Dashboard images — need at least ONE (metacubexd preferred, zashboard fallback)
    local dashboard_imgs=(metacubexd zashboard)

    local missing_imgs=()
    for img in "${required_imgs[@]}"; do
        local src="$DOCKER_IMAGES/${img}.tar.gz"
        if [[ ! -f "$src" ]]; then
            missing_imgs+=("${img} (archive missing)")
            continue
        fi
        if ! _has_arch_tag "$src" "$arch" "$img"; then
            missing_imgs+=("${img}:${arch}")
        fi
    done

    # Check which dashboards are available for this arch
    local available_dashboards=()
    for img in "${dashboard_imgs[@]}"; do
        local src="$DOCKER_IMAGES/${img}.tar.gz"
        [[ -f "$src" ]] || continue
        if _has_arch_tag "$src" "$arch" "$img"; then
            available_dashboards+=("${img}")
        fi
    done

    if [[ ${#available_dashboards[@]} -eq 0 ]]; then
        missing_imgs+=("no dashboard (need metacubexd or zashboard)")
    fi

    if [[ ${#missing_imgs[@]} -gt 0 ]]; then
        skip "pimiproxy-${arch}: ${missing_imgs[*]}"
        return 0
    fi

    step "pimiproxy-${arch}  ${dim}(dashboards: ${available_dashboards[*]})${reset}"

    # Extract single-arch Docker images
    local img_tmp
    img_tmp=$(mktemp -d)
    for img in "${required_imgs[@]}" "${available_dashboards[@]}"; do
        docker save "${img}:${arch}" | gzip > "$img_tmp/${img}.tar.gz"
    done

    # Stage the archive
    local staging
    staging=$(mktemp -d)
    mkdir -p "$staging/pimiproxy/docker-images" \
             "$staging/pimiproxy/pimiproxy-api" \
             "$staging/pimiproxy/pimiproxy-ui"

    cp "$ROOT_DIR/pimiproxy" "$ROOT_DIR/pimiproxy-core.py" \
       "$ROOT_DIR/pimiproxy_common.py" "$staging/pimiproxy/"
    cp "$img_tmp"/*.tar.gz "$staging/pimiproxy/docker-images/"
    cp "$DOCKER_IMAGES/geoip.metadb" "$DOCKER_IMAGES/geosite.dat" "$DOCKER_IMAGES/country.mmdb" \
       "$staging/pimiproxy/docker-images/"

    rsync -a --exclude='__pycache__' "$ROOT_DIR/pimiproxy-api/" "$staging/pimiproxy/pimiproxy-api/"
    rsync -a --exclude='__pycache__' --exclude='node_modules' "$ROOT_DIR/pimiproxy-ui/" "$staging/pimiproxy/pimiproxy-ui/"

    tar czf "$archive" -C "$staging" pimiproxy
    ok "$(basename "$archive")  ($(du -sh "$archive" | cut -f1))"

    rm -rf "$img_tmp" "$staging"
}

# ── Build a pimiproxy-ultralite archive for one arch ─────────────────────────
_build_ultralite() {
    local arch="$1"
    local archive="$RELEASES/pimiproxy-ultralite-${arch}.tar.gz"

    # Map arch to binary naming (mihomo uses amd64/386/arm64/armv7)
    local bin_match
    bin_match=$(ls "$BIN_DIR"/mihomo-linux-${arch}-*.gz 2>/dev/null | head -1)
    if [[ -z "$bin_match" ]]; then
        skip "pimiproxy-ultralite-${arch}: no mihomo binary in bin/"
        return 0
    fi

    step "pimiproxy-ultralite-${arch}"

    local staging
    staging=$(mktemp -d)
    mkdir -p "$staging/pimiproxy-ultralite/bin" \
             "$staging/pimiproxy-ultralite/docker-images"

    cp "$ROOT_DIR/pimiproxy-ultralite" "$ROOT_DIR/pimiproxy-ultralite-core.py" \
       "$ROOT_DIR/pimiproxy_common.py" "$staging/pimiproxy-ultralite/"
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

# Clear only the archives we're about to rebuild (don't touch the other variant)
if [[ $DO_FULL -eq 1 ]]; then
    for a in amd64 arm64 armv7 386; do
        rm -f "$RELEASES"/pimiproxy-${a}.tar.gz 2>/dev/null
    done
fi
if [[ $DO_ULTRALITE -eq 1 ]]; then
    rm -f "$RELEASES"/pimiproxy-ultralite-*.tar.gz 2>/dev/null
fi

if [[ $DO_FULL -eq 1 ]]; then
    for arch in "${FULL_ARCHES[@]}"; do
        _build_full "$arch"
    done
fi

if [[ $DO_ULTRALITE -eq 1 ]]; then
    for arch in "${ULTRALITE_ARCHES[@]}"; do
        _build_ultralite "$arch"
    done
fi

echo
echo "${bold}────────────────────────────────────────${reset}"
if ls "$RELEASES"/*.tar.gz &>/dev/null; then
    echo "${bold}${green}Done.${reset} Archives in ${bold}releases/${reset}:"
    ls -lh "$RELEASES"/*.tar.gz | awk '{printf "  %-50s %s\n", $NF, $5}'
else
    fail "No archives built — check prerequisites."
fi
echo
