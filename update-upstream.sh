#!/usr/bin/env bash
# update-upstream.sh — download latest mihomo binaries and pull latest Docker images
# Run on the dev machine before building releases.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"
DOCKER_IMAGES="$SCRIPT_DIR/docker-images"

bold=$(tput bold 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
cyan=$(tput setaf 6 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)
dim=$(tput dim 2>/dev/null || true)

step()  { echo "${bold}${cyan}→ $*${reset}"; }
ok()    { echo "${bold}${green}✓ $*${reset}"; }
fail()  { echo "${bold}${red}✗ $*${reset}" >&2; }

usage() {
    cat <<EOF

${bold}${cyan}update-upstream${reset} — fetch latest mihomo binaries and Docker images

${bold}Usage:${reset} $0 [options]

${bold}Options:${reset}
  --bins           Download latest mihomo release binaries (arm64, amd64, armv7)
  --images         Pull latest Docker images and save as multi-arch tarballs
                   (mihomo, metacubexd, zashboard — arm64 + amd64)
  --all            Both of the above (default if no flags given)
  --dry-run        Show what would be downloaded without doing it
  -h, --help       Show this help

${bold}Downloads:${reset}
  bin/mihomo-linux-arm64-<version>.gz
  bin/mihomo-linux-amd64-<version>.gz
  bin/mihomo-linux-armv7-<version>.gz
  docker-images/mihomo.tar.gz        ${dim}(arm64 + amd64)${reset}
  docker-images/metacubexd.tar.gz    ${dim}(arm64 + amd64)${reset}
  docker-images/zashboard.tar.gz     ${dim}(arm64 + amd64)${reset}

${bold}Note:${reset} pimiproxy-api and pimiproxy-ui images are built locally
via rebuild.sh — this script does not touch them.

EOF
    exit 0
}

# ��─ Argument parsing ─────────────────────────────────────────────────────────
DO_BINS=0
DO_IMAGES=0
DRY_RUN=0

if [[ $# -eq 0 ]]; then
    DO_BINS=1; DO_IMAGES=1
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bins)    DO_BINS=1 ;;
        --images)  DO_IMAGES=1 ;;
        --all)     DO_BINS=1; DO_IMAGES=1 ;;
        --dry-run) DRY_RUN=1; DO_BINS=1; DO_IMAGES=1 ;;
        -h|--help) usage ;;
        *)         echo "Unknown flag: $1"; usage ;;
    esac
    shift
done

echo
echo "${bold}${cyan}update-upstream${reset}"
echo "${bold}────────────────────────────────────────${reset}"
echo

# ── Mihomo binaries ──────────────────────────────────────────────────────────
if [[ $DO_BINS -eq 1 ]]; then
    step "Fetching latest mihomo release info from GitHub..."

    # Get latest release tag from GitHub API
    RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest" 2>/dev/null) || {
        fail "Could not reach GitHub API. Check your internet connection."
        exit 1
    }
    TAG=$(echo "$RELEASE_JSON" | grep -oP '"tag_name"\s*:\s*"\K[^"]+')
    VERSION="${TAG#v}"  # strip leading 'v' if present

    echo "  Latest release: ${bold}${TAG}${reset}"
    echo

    # Check current version
    CURRENT=$(ls "$BIN_DIR"/mihomo-linux-amd64-*.gz 2>/dev/null | grep -oP 'v[\d.]+' | head -1 || true)
    if [[ -n "$CURRENT" && "$CURRENT" == "$TAG" ]]; then
        ok "Already up to date (${TAG})"
    else
        if [[ -n "$CURRENT" ]]; then
            echo "  Current: ${dim}${CURRENT}${reset} → ${bold}${TAG}${reset}"
        fi

        # Download URLs — mihomo uses alpha/Meta naming
        ASSETS=(
            "mihomo-linux-amd64-${TAG}.gz"
            "mihomo-linux-arm64-${TAG}.gz"
            "mihomo-linux-armv7-${TAG}.gz"
        )
        BASE_URL="https://github.com/MetaCubeX/mihomo/releases/download/${TAG}"

        mkdir -p "$BIN_DIR"

        for asset in "${ASSETS[@]}"; do
            url="${BASE_URL}/${asset}"
            dest="$BIN_DIR/${asset}"

            if [[ $DRY_RUN -eq 1 ]]; then
                echo "  ${dim}[dry-run]${reset} Would download: ${asset}"
                continue
            fi

            step "Downloading ${asset}..."
            if curl -fSL -o "$dest" "$url" 2>/dev/null; then
                ok "${asset}  ($(du -sh "$dest" | cut -f1))"
            else
                fail "Failed to download ${asset}"
                fail "URL: ${url}"
            fi
        done

        # Remove old versions
        if [[ $DRY_RUN -eq 0 ]]; then
            for old in "$BIN_DIR"/mihomo-linux-*.gz; do
                [[ -f "$old" ]] || continue
                case "$(basename "$old")" in
                    *"${TAG}"*) ;;  # keep current version
                    *) rm -f "$old"; echo "  ${dim}Removed old: $(basename "$old")${reset}" ;;
                esac
            done
        fi
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
            echo "  ${dim}[dry-run]${reset} Would pull ${full} for arm64 + amd64 and save to docker-images/${name}.tar.gz"
            continue
        fi

        for arch in arm64 amd64; do
            echo "  Pulling ${arch}..."
            docker pull --platform "linux/${arch}" "$full" >/dev/null 2>&1 || {
                fail "Failed to pull ${full} for ${arch}"
                continue
            }
            docker tag "$full" "${name}:${arch}"
        done

        echo "  Saving multi-arch tarball..."
        docker save "${name}:arm64" "${name}:amd64" \
            | gzip > "$DOCKER_IMAGES/${name}.tar.gz"
        ok "${name}.tar.gz  ($(du -sh "$DOCKER_IMAGES/${name}.tar.gz" | cut -f1))"
        echo
    done
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo "${bold}────────────────────────────────────────${reset}"
echo "${bold}${green}Done.${reset}"

if [[ $DO_BINS -eq 1 ]]; then
    echo
    echo "  ${bold}Binaries:${reset}"
    ls -lh "$BIN_DIR"/mihomo-linux-*.gz 2>/dev/null | awk '{printf "    %-50s %s\n", $NF, $5}'
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
echo "  Next: ${dim}./build-releases.sh${reset} to generate release archives."
echo
