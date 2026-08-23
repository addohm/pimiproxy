#!/usr/bin/env bash
# 1_update-upstream.sh — download latest mihomo binaries and pull latest Docker images
# Run on the dev machine before building releases.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN_DIR="$ROOT_DIR/bin"
DOCKER_IMAGES="$ROOT_DIR/docker-images"

bold=$(tput bold 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
cyan=$(tput setaf 6 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)
dim=$(tput dim 2>/dev/null || true)

step()  { echo "${bold}${cyan}→ $*${reset}"; }
ok()    { echo "${bold}${green}✓ $*${reset}"; }
fail()  { echo "${bold}${red}✗ $*${reset}" >&2; }

# ── Architecture matrices ────────────────────────────────────────────────────
# Mihomo release binary names per host arch
BIN_ARCHES=(
    "amd64"    # x86_64 (Intel/AMD 64-bit)
    "386"      # x86 (32-bit Intel/AMD)
    "arm64"    # aarch64 (modern ARM 64-bit)
    "armv7"    # 32-bit ARM (Pi 3, older SBCs)
)

# Docker --platform strings per host arch — no 386 here because:
# metacubexd, zashboard, and the node-alpine base image used by
# pimiproxy-ui all lack 386 builds. A full 386 release is impossible
# regardless, so we don't waste time pulling partial images. Ultralite
# 386 still works because it uses the mihomo binary above, not Docker.
DOCKER_PLATFORMS=(
    "amd64:linux/amd64"
    "arm64:linux/arm64"
    "armv7:linux/arm/v7"
)

usage() {
    cat <<EOF

${bold}${cyan}1_update-upstream${reset} — fetch latest mihomo binaries and Docker images

${bold}Usage:${reset} $0 [options]

${bold}Options:${reset}
  --bins           Download latest mihomo release binaries (amd64, 386, arm64, armv7)
  --images         Pull latest Docker images for all supported platforms and
                   save as multi-arch tarballs (mihomo, metacubexd, zashboard)
  --geodata        Download geoip.metadb / geosite.dat / country.mmdb from
                   MetaCubeX/meta-rules-dat into docker-images/
  --all            All of the above (default if no flags given)
  --dry-run        Show what would be downloaded without doing it
  -h, --help       Show this help

${bold}Downloads:${reset}
  bin/mihomo-linux-amd64-<version>.gz
  bin/mihomo-linux-386-<version>.gz
  bin/mihomo-linux-arm64-<version>.gz
  bin/mihomo-linux-armv7-<version>.gz
  docker-images/mihomo.tar.gz        ${dim}(multi-arch)${reset}
  docker-images/metacubexd.tar.gz    ${dim}(multi-arch)${reset}
  docker-images/zashboard.tar.gz     ${dim}(multi-arch)${reset}
  docker-images/geoip.metadb
  docker-images/geosite.dat
  docker-images/country.mmdb

${bold}Note:${reset} pimiproxy-api and pimiproxy-ui images are built locally
via 2_rebuild.sh — this script does not touch them.

EOF
    exit 0
}

# ── Argument parsing ─────────────────────────────────────────────────────────
DO_BINS=0
DO_IMAGES=0
DO_GEODATA=0
DRY_RUN=0

if [[ $# -eq 0 ]]; then
    DO_BINS=1; DO_IMAGES=1; DO_GEODATA=1
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bins)    DO_BINS=1 ;;
        --images)  DO_IMAGES=1 ;;
        --geodata) DO_GEODATA=1 ;;
        --all)     DO_BINS=1; DO_IMAGES=1; DO_GEODATA=1 ;;
        --dry-run) DRY_RUN=1; DO_BINS=1; DO_IMAGES=1; DO_GEODATA=1 ;;
        -h|--help) usage ;;
        *)         echo "Unknown flag: $1"; usage ;;
    esac
    shift
done

echo
echo "${bold}${cyan}1_update-upstream${reset}"
echo "${bold}────────────────────────────────────────${reset}"
echo

# ── Mihomo binaries ──────────────────────────────────────────────────────────
if [[ $DO_BINS -eq 1 ]]; then
    step "Fetching latest mihomo release info from GitHub..."

    RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest" 2>/dev/null) || {
        fail "Could not reach GitHub API. Check your internet connection."
        exit 1
    }
    TAG=$(echo "$RELEASE_JSON" | grep -oP '"tag_name"\s*:\s*"\K[^"]+')

    echo "  Latest release: ${bold}${TAG}${reset}"
    echo

    BASE_URL="https://github.com/MetaCubeX/mihomo/releases/download/${TAG}"
    mkdir -p "$BIN_DIR"

    # Remove any old-version binaries first
    if [[ $DRY_RUN -eq 0 ]]; then
        for old in "$BIN_DIR"/mihomo-linux-*.gz; do
            [[ -f "$old" ]] || continue
            case "$(basename "$old")" in
                *"${TAG}"*) ;;
                *) rm -f "$old"; echo "  ${dim}Removed old: $(basename "$old")${reset}" ;;
            esac
        done
    fi

    downloaded=0
    for arch in "${BIN_ARCHES[@]}"; do
        asset="mihomo-linux-${arch}-${TAG}.gz"
        dest="$BIN_DIR/${asset}"

        if [[ -f "$dest" ]]; then
            echo "  ${dim}${asset} — already present${reset}"
            continue
        fi

        url="${BASE_URL}/${asset}"

        if [[ $DRY_RUN -eq 1 ]]; then
            echo "  ${dim}[dry-run]${reset} Would download: ${asset}"
            continue
        fi

        step "Downloading ${asset}..."
        if curl -fSL -o "$dest" "$url" 2>/dev/null; then
            ok "${asset}  ($(du -sh "$dest" | cut -f1))"
            downloaded=$((downloaded + 1))
        else
            fail "Failed to download ${asset} (not available for ${TAG}?)"
            rm -f "$dest"  # clean up partial
        fi
    done

    if [[ $DRY_RUN -eq 0 && $downloaded -eq 0 ]]; then
        ok "All mihomo binaries up to date (${TAG})"
    fi
    echo
fi

# ── Docker images ────────────────────────────────────────────────────────────
if [[ $DO_IMAGES -eq 1 ]]; then
    IMAGES=(
        "mihomo:metacubex/mihomo:latest"
        "metacubexd:ghcr.io/metacubex/metacubexd:latest"
        "zashboard:ghcr.io/zephyruso/zashboard:latest"
    )

    mkdir -p "$DOCKER_IMAGES"

    for entry in "${IMAGES[@]}"; do
        name="${entry%%:*}"
        full="${entry#*:}"

        step "${name} (${full})"

        if [[ $DRY_RUN -eq 1 ]]; then
            echo "  ${dim}[dry-run]${reset} Would pull ${full} for all platforms and save to docker-images/${name}.tar.gz"
            continue
        fi

        # Try pulling for each platform; skip silently if not available
        available_arches=()
        for platform_entry in "${DOCKER_PLATFORMS[@]}"; do
            arch="${platform_entry%%:*}"
            platform="${platform_entry#*:}"
            echo "  Pulling ${arch} (${platform})..."
            if docker pull --platform "${platform}" "$full" >/dev/null 2>&1; then
                docker tag "$full" "${name}:${arch}"
                available_arches+=("${arch}")
            else
                echo "    ${dim}${arch} not available for this image — skipping${reset}"
            fi
        done

        if [[ ${#available_arches[@]} -eq 0 ]]; then
            fail "No platforms available for ${full}"
            continue
        fi

        echo "  Saving multi-arch tarball (${available_arches[*]})..."
        # Build list of image tags for docker save
        save_tags=()
        for arch in "${available_arches[@]}"; do
            save_tags+=("${name}:${arch}")
        done
        docker save "${save_tags[@]}" | gzip > "$DOCKER_IMAGES/${name}.tar.gz"
        ok "${name}.tar.gz  ($(du -sh "$DOCKER_IMAGES/${name}.tar.gz" | cut -f1))  [${available_arches[*]}]"
        echo
    done
fi

# ── Geodata ──────────────────────────────────────────────────────────────────
# Shipped into the install's config dir so Mihomo never needs GitHub at
# runtime (geo-auto-update is off). These are Git LFS objects in the repo;
# a clone without `git lfs pull` holds 132-byte pointer files instead, and
# 3_build-releases.sh refuses to package those.
GEODATA_BASE="https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest"
GEODATA_FILES=(geoip.metadb geosite.dat country.mmdb)

if [[ $DO_GEODATA -eq 1 ]]; then
    step "Downloading geodata from MetaCubeX/meta-rules-dat..."
    mkdir -p "$DOCKER_IMAGES"
    for f in "${GEODATA_FILES[@]}"; do
        dest="$DOCKER_IMAGES/$f"
        if [[ $DRY_RUN -eq 1 ]]; then
            echo "  ${dim}[dry-run]${reset} Would download: $f"
            continue
        fi
        if curl -fSL -o "${dest}.part" "${GEODATA_BASE}/${f}" 2>/dev/null \
           && [[ $(stat -c %s "${dest}.part") -gt 100000 ]]; then
            mv -f "${dest}.part" "$dest"
            ok "$f  ($(du -sh "$dest" | cut -f1))"
        else
            rm -f "${dest}.part"
            fail "Failed to download $f"
        fi
    done
    echo
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo "${bold}────────────────────────────────────────${reset}"
echo "${bold}${green}Done.${reset}"

if [[ $DO_BINS -eq 1 ]]; then
    echo
    echo "  ${bold}Binaries:${reset}"
    ls -lh "$BIN_DIR"/mihomo-linux-*.gz 2>/dev/null | awk '{printf "    %-50s %s\n", $NF, $5}'
fi

if [[ $DO_GEODATA -eq 1 ]]; then
    echo
    echo "  ${bold}Geodata:${reset}"
    for f in "${GEODATA_FILES[@]}"; do
        [[ -f "$DOCKER_IMAGES/$f" ]] && \
            printf "    %-50s %s\n" "docker-images/$f" "$(du -sh "$DOCKER_IMAGES/$f" | cut -f1)"
    done
fi

if [[ $DO_IMAGES -eq 1 ]]; then
    echo
    echo "  ${bold}Docker images:${reset}"
    for f in mihomo metacubexd zashboard; do
        [[ -f "$DOCKER_IMAGES/${f}.tar.gz" ]] && \
            printf "    %-50s %s\n" "docker-images/${f}.tar.gz" "$(du -sh "$DOCKER_IMAGES/${f}.tar.gz" | cut -f1)"
    done
fi

echo
echo "  Next: ${dim}./scripts/2_rebuild.sh${reset} (build pimiproxy-api/ui), then ${dim}./scripts/3_build-releases.sh${reset}"
echo
