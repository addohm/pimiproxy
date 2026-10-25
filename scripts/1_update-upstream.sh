#!/usr/bin/env bash
# 1_update-upstream.sh — download latest mihomo binaries and pull latest Docker images
# Run on the dev machine before building releases.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN_DIR="$ROOT_DIR/bin"
VERSIONS_DOC="$ROOT_DIR/docs/IMAGE_VERSIONS.md"

# Latest release tag for a GitHub repo, read from the /releases/latest
# redirect. Deliberately not the REST API: it rate-limits per IP (unauth'd,
# 60/hour) and has blocked this machine before, which would silently break
# version checks. Prints nothing on failure.
latest_tag() {   # latest_tag <owner/repo>
    curl -fsSL -o /dev/null -w '%{url_effective}' \
        "https://github.com/$1/releases/latest" 2>/dev/null \
        | sed -n 's|.*/releases/tag/||p'
}

# Version baked into a pulled image's OCI label. Authoritative for mihomo;
# useless for the dashboards (metacubexd sets no labels, and zashboard's
# label reports its Caddy base image, not zashboard) — so callers only use
# this where it is known to be meaningful.
image_label_version() {   # image_label_version <image:tag>
    docker image inspect "$1" \
        --format '{{index .Config.Labels "org.opencontainers.image.version"}}' \
        2>/dev/null | grep -v '^<no value>$'
}
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
  --docker         Download the static Docker Engine (dockerd/containerd/runc)
                   + compose plugin per arch into docker-engine/ (offline install)
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
DO_DOCKER=0
DRY_RUN=0

if [[ $# -eq 0 ]]; then
    DO_BINS=1; DO_IMAGES=1; DO_GEODATA=1; DO_DOCKER=1
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bins)    DO_BINS=1 ;;
        --images)  DO_IMAGES=1 ;;
        --geodata) DO_GEODATA=1 ;;
        --docker)  DO_DOCKER=1 ;;
        --all)     DO_BINS=1; DO_IMAGES=1; DO_GEODATA=1; DO_DOCKER=1 ;;
        --dry-run) DRY_RUN=1; DO_BINS=1; DO_IMAGES=1; DO_GEODATA=1; DO_DOCKER=1 ;;
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

    TAG="$(latest_tag MetaCubeX/mihomo)"
    if [[ -z "$TAG" ]]; then
        fail "Could not determine the latest mihomo release. Check your internet connection."
        exit 1
    fi

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
# runtime (geo-auto-update is off). Not committed (docker-images/ is
# gitignored), so a fresh clone must fetch them here; 3_build-releases.sh
# refuses to package missing or truncated files.
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

# ── Static Docker Engine (offline bootstrap for the full variant) ────────────
# The release images load via `docker load`, which needs a running daemon. On a
# fully offline box the wrapper can install the engine from this bundle instead
# of get.docker.com / Aliyun. Static tarball = dockerd + containerd + runc +
# docker-init + docker-proxy + the client, no dependencies.
DOCKER_ENGINE_DIR="$ROOT_DIR/docker-engine"
DOCKER_STATIC_VER="${DOCKER_STATIC_VER:-29.7.2}"
DOCKER_COMPOSE_VER="${DOCKER_COMPOSE_VER:-v5.5.0}"
# pimiarch : docker-static-arch : compose-arch
DOCKER_ENGINE_ARCHES=(
    "amd64:x86_64:x86_64"
    "arm64:aarch64:aarch64"
    "armv7:armhf:armv7"
)

if [[ $DO_DOCKER -eq 1 ]]; then
    step "Downloading static Docker Engine ${DOCKER_STATIC_VER} + compose ${DOCKER_COMPOSE_VER}..."
    mkdir -p "$DOCKER_ENGINE_DIR"
    for entry in "${DOCKER_ENGINE_ARCHES[@]}"; do
        arch="${entry%%:*}"; rest="${entry#*:}"
        darch="${rest%%:*}"; carch="${rest##*:}"
        tgz_url="https://download.docker.com/linux/static/stable/${darch}/docker-${DOCKER_STATIC_VER}.tgz"
        comp_url="https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VER}/docker-compose-linux-${carch}"
        tgz_dest="$DOCKER_ENGINE_DIR/docker-${arch}.tgz"
        comp_dest="$DOCKER_ENGINE_DIR/docker-compose-${arch}"
        if [[ $DRY_RUN -eq 1 ]]; then
            echo "  ${dim}[dry-run]${reset} Would download: docker-${arch}.tgz, docker-compose-${arch}"
            continue
        fi
        if curl -fSL -o "${tgz_dest}.part" "$tgz_url" 2>/dev/null \
           && [[ $(stat -c %s "${tgz_dest}.part") -gt 10000000 ]]; then
            mv -f "${tgz_dest}.part" "$tgz_dest"
            ok "docker-${arch}.tgz  ($(du -sh "$tgz_dest" | cut -f1))"
        else
            rm -f "${tgz_dest}.part"; fail "Failed: docker-${arch}.tgz (${darch})"
        fi
        if curl -fSL -o "${comp_dest}.part" "$comp_url" 2>/dev/null \
           && [[ $(stat -c %s "${comp_dest}.part") -gt 1000000 ]]; then
            mv -f "${comp_dest}.part" "$comp_dest"; chmod +x "$comp_dest"
            ok "docker-compose-${arch}  ($(du -sh "$comp_dest" | cut -f1))"
        else
            rm -f "${comp_dest}.part"; fail "Failed: docker-compose-${arch} (${carch})"
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

if [[ $DO_DOCKER -eq 1 ]]; then
    echo
    echo "  ${bold}Docker Engine bundle:${reset}"
    ls -lh "$DOCKER_ENGINE_DIR"/docker-*.tgz "$DOCKER_ENGINE_DIR"/docker-compose-* 2>/dev/null | awk '{printf "    %-50s %s\n", $NF, $5}'
fi

# ── Record what is actually bundled ──────────────────────────────────────────
# docs/IMAGE_VERSIONS.md used to be hand-typed, so it recorded whatever someone
# remembered rather than what shipped. Generate it from the artifacts instead:
# mihomo's version comes from its OCI label (authoritative), the dashboards'
# from the upstream release tag at pull time (their images carry no usable
# version — see image_label_version).
if [[ $DRY_RUN -eq 0 && ( $DO_IMAGES -eq 1 || $DO_BINS -eq 1 ) ]]; then
    step "Recording bundled versions"

    _mihomo_img_ver="$(image_label_version mihomo:amd64)"
    [[ -z "$_mihomo_img_ver" ]] && _mihomo_img_ver="$(image_label_version mihomo:arm64)"
    [[ -z "$_mihomo_img_ver" ]] && _mihomo_img_ver="unknown"
    _mcd_ver="$(latest_tag MetaCubeX/metacubexd)";  [[ -z "$_mcd_ver"  ]] && _mcd_ver="unknown"
    _zash_ver="$(latest_tag Zephyruso/zashboard)";  [[ -z "$_zash_ver" ]] && _zash_ver="unknown"
    _bin_ver="$(ls "$BIN_DIR"/mihomo-linux-amd64-*.gz 2>/dev/null | head -1 \
                | sed -n 's|.*mihomo-linux-amd64-\(.*\)\.gz|\1|p')"
    [[ -z "$_bin_ver" ]] && _bin_ver="unknown"

    mkdir -p "$(dirname "$VERSIONS_DOC")"
    {
        echo "<!-- Generated by scripts/1_update-upstream.sh — do not edit by hand."
        echo "     Regenerate by re-running that script. -->"
        echo "# Bundled upstream versions"
        echo
        echo "Last fetched: $(date -u +%Y-%m-%d)"
        echo
        echo "## mihomo ${_mihomo_img_ver}"
        echo "docker.io/metacubex/mihomo:latest"
        echo "https://github.com/MetaCubeX/mihomo/releases/tag/${_mihomo_img_ver}"
        echo
        echo "## mihomo-binary ${_bin_ver}"
        echo "bin/mihomo-linux-<arch>-${_bin_ver}.gz  (ultralite variant)"
        echo "https://github.com/MetaCubeX/mihomo/releases/tag/${_bin_ver}"
        echo
        echo "## metacubexd ${_mcd_ver}"
        echo "ghcr.io/metacubex/metacubexd:latest"
        echo "https://github.com/MetaCubeX/metacubexd/releases/tag/${_mcd_ver}"
        echo
        echo "## zashboard ${_zash_ver}"
        echo "ghcr.io/zephyruso/zashboard:latest"
        echo "https://github.com/Zephyruso/zashboard/releases/tag/${_zash_ver}"
        echo
        echo "## pimiproxy-api / pimiproxy-ui"
        echo "Built locally by scripts/2_rebuild.sh from this repo; versioned by the"
        echo "root VERSION file and stamped as the io.pimiproxy.version image label."
    } > "$VERSIONS_DOC"
    ok "docs/IMAGE_VERSIONS.md updated (mihomo ${_mihomo_img_ver}, binary ${_bin_ver}, metacubexd ${_mcd_ver}, zashboard ${_zash_ver})"
    echo
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
