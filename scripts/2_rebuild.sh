#!/usr/bin/env bash
# 2_rebuild.sh — build pimiproxy Docker images (dev machine) or deploy them (SBC)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCKER_IMAGES="$ROOT_DIR/docker-images"

# Version stamped into images as labels; VERSION at the repo root is the
# single source of truth (see docs/MAINTAINER.md).
PIMIPROXY_VERSION="$(cat "$ROOT_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')"
PIMIPROXY_VERSION="${PIMIPROXY_VERSION:-0.0.0-dev}"
PIMIPROXY_GIT_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# Defaults assume the user has SSH'd to their SBC at least once. Override
# either with the env vars or the --target flag. SBC_IMAGES is derived from
# SBC_USER so swapping the SSH user re-targets the rsync destination too.
SBC_USER="${PIMIPROXY_USER:-${USER}}"
SBC_HOST="${PIMIPROXY_TARGET:-${SBC_USER}@pimiproxy.lan}"
SBC_IMAGES="${PIMIPROXY_REMOTE_DIR:-/home/${SBC_USER}/pimiproxy}/docker-images"
COMPOSE_FILE="/opt/pimiproxy/docker-compose.yml"

MIHOMO_IMAGE="metacubex/mihomo:latest"
METACUBEXD_IMAGE="ghcr.io/metacubex/metacubexd:latest"
ZASHBOARD_IMAGE="ghcr.io/zephyruso/zashboard:latest"

# Docker build platforms — arch:linux-platform
# 386 is intentionally absent: node:20-alpine (used by pimiproxy-ui's
# build stage) has no 386 variant, so pimiproxy-ui can never build for
# 386. Since a full 386 release also requires metacubexd/zashboard
# which aren't published for 386 either, there's no reason to attempt
# 386 here.
BUILD_PLATFORMS=(
    "amd64:linux/amd64"
    "arm64:linux/arm64"
    "armv7:linux/arm/v7"
)

# ── Colours ───────────────────────────────────────────────────────────────────
bold=$(tput bold 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
cyan=$(tput setaf 6 2>/dev/null || true)
dim=$(tput dim 2>/dev/null || true)

step()  { echo "${bold}${cyan}→ $*${reset}"; }
ok()    { echo "${bold}${green}✓ $*${reset}"; }

usage() {
    cat <<EOF
${bold}Usage:${reset} $0 [--target user@host] [--api] [--ui] [--third-party] [--sync] [--sync-only] [--deploy] [--deploy-api] [--deploy-ui]

${bold}Options:${reset}
  --target user@host   Override the sync destination (default: ${SBC_HOST})
                       Only used with --sync / --sync-only / --deploy.
                       Can also be set via PIMIPROXY_TARGET env var.

${bold}Dev machine (build only — no sync by default):${reset}
  (no flags)       Build both pimiproxy images locally, save tarballs
  --api            Build only pimiproxy-api, save tarball
  --ui             Build only pimiproxy-ui, save tarball
  --third-party    Pull mihomo, metacubexd, zashboard (all available arches)
  --sync           Also rsync the source tree to the target after building
  --sync-only      Rsync source without rebuilding anything

${bold}Remote — run these on the target machine:${reset}
  --deploy         Load both images, force-recreate both containers
  --deploy-api     Load pimiproxy-api image, recreate pimiproxy-api container
  --deploy-ui      Load pimiproxy-ui image, recreate pimiproxy-ui container

${bold}Archive format:${reset}
  Each .tar.gz contains all successfully-built variants, tagged as
  <image>:<arch>. 3_build-releases.sh splits them back into per-arch
  release archives.
EOF
    exit 1
}

# ── Build helpers ─────────────────────────────────────────────────────────────
_build_image() {
    local name="$1"      # pimiproxy-api or pimiproxy-ui
    local context="$ROOT_DIR/${name}"

    # pimiproxy-api needs the shared config generator. The running container
    # prefers the copy mounted from the install dir; this baked copy is the
    # fallback for install dirs that predate pimiproxy_common.py. Gitignored.
    if [[ "$name" == "pimiproxy-api" ]]; then
        cp "$ROOT_DIR/pimiproxy_common.py" "$context/api/_bundled_common.py"
    fi

    local built_arches=()
    for entry in "${BUILD_PLATFORMS[@]}"; do
        local arch="${entry%%:*}"
        local platform="${entry#*:}"
        step "Building ${name} (${arch}, ${platform})…"
        local _blog; _blog="$(mktemp)"
        if docker buildx build --platform "${platform}" -t "${name}:${arch}" \
                --label "io.pimiproxy.version=${PIMIPROXY_VERSION}" \
                --label "io.pimiproxy.git=${PIMIPROXY_GIT_SHA}" \
                "$context" --load >"$_blog" 2>&1; then
            built_arches+=("${arch}")
            rm -f "$_blog"
        else
            echo "    ${dim}${arch} build failed — last lines:${reset}" >&2
            tail -15 "$_blog" >&2
            rm -f "$_blog"
        fi
    done

    if [[ ${#built_arches[@]} -eq 0 ]]; then
        echo "  No arches built successfully for ${name}."
        return 1
    fi

    step "Saving ${name}.tar.gz…"
    local save_tags=()
    for arch in "${built_arches[@]}"; do
        save_tags+=("${name}:${arch}")
    done
    mkdir -p "$DOCKER_IMAGES"
    docker save "${save_tags[@]}" | gzip > "$DOCKER_IMAGES/${name}.tar.gz"
    ok "${name} saved (${built_arches[*]})"
}

build_api() { _build_image pimiproxy-api; }
build_ui()  { _build_image pimiproxy-ui; }

pull_third_party() {
    mkdir -p "$DOCKER_IMAGES"
    for entry in \
        "mihomo:${MIHOMO_IMAGE}" \
        "metacubexd:${METACUBEXD_IMAGE}" \
        "zashboard:${ZASHBOARD_IMAGE}"
    do
        name="${entry%%:*}"
        full="${entry#*:}"

        available_arches=()
        for platform_entry in "${BUILD_PLATFORMS[@]}"; do
            arch="${platform_entry%%:*}"
            platform="${platform_entry#*:}"
            step "Pulling ${name} (${arch}, ${platform})…"
            if docker pull --platform "${platform}" "$full" >/dev/null 2>&1; then
                docker tag "$full" "${name}:${arch}"
                available_arches+=("${arch}")
            else
                echo "    ${dim}${arch} not available — skipping${reset}"
            fi
        done

        if [[ ${#available_arches[@]} -eq 0 ]]; then
            echo "  No platforms available for ${full}"
            continue
        fi

        step "Saving ${name}.tar.gz…"
        save_tags=()
        for arch in "${available_arches[@]}"; do
            save_tags+=("${name}:${arch}")
        done
        docker save "${save_tags[@]}" | gzip > "$DOCKER_IMAGES/${name}.tar.gz"
        ok "${name} saved (${available_arches[*]})"
    done
}

do_sync() {
    step "Syncing to ${SBC_HOST}…"
    rsync -avzc \
        --exclude '.git' \
        --exclude '.gitignore' \
        --exclude '__pycache__' \
        --exclude 'node_modules' \
        --exclude 'pimiproxy.tar.gz' \
        --del \
        "$ROOT_DIR" "${SBC_HOST}:/home/${SBC_USER}"
    ok "Sync complete"
}

# ── Deploy helpers (run on SBC) ───────────────────────────────────────────────
deploy_api() {
    step "Loading pimiproxy-api image…"
    sudo docker load -i "${SBC_IMAGES}/pimiproxy-api.tar.gz"
    step "Recreating pimiproxy-api container…"
    sudo docker compose -f "$COMPOSE_FILE" up -d --force-recreate pimiproxy-api
    ok "pimiproxy-api deployed"
}

deploy_ui() {
    step "Loading pimiproxy-ui image…"
    sudo docker load -i "${SBC_IMAGES}/pimiproxy-ui.tar.gz"
    step "Recreating pimiproxy-ui container…"
    sudo docker compose -f "$COMPOSE_FILE" up -d --force-recreate pimiproxy-ui
    ok "pimiproxy-ui deployed"
}

# ── Argument parsing ──────────────────────────────────────────────────────────
if [[ $# -eq 0 ]]; then
    # Default: build both local images, no sync. Run both even if one fails
    # (set -e would otherwise abort before build_ui if build_api returned 1).
    _rc=0
    build_api || _rc=1
    build_ui  || _rc=1
    exit $_rc
fi

DO_API=0 DO_UI=0 DO_SYNC=0 DO_THIRD_PARTY=0
DO_DEPLOY_API=0 DO_DEPLOY_UI=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)        shift; SBC_HOST="$1" ;;
        --api)           DO_API=1 ;;
        --ui)            DO_UI=1 ;;
        --third-party)   DO_THIRD_PARTY=1 ;;
        --sync)          DO_SYNC=1 ;;
        --sync-only)     DO_SYNC=1 ;;
        --deploy)        DO_DEPLOY_API=1; DO_DEPLOY_UI=1 ;;
        --deploy-api)    DO_DEPLOY_API=1 ;;
        --deploy-ui)     DO_DEPLOY_UI=1 ;;
        -h|--help)       usage ;;
        *)               echo "Unknown flag: $1"; usage ;;
    esac
    shift
done

# --sync-only: just sync, skip building
if [[ $DO_SYNC -eq 1 && $DO_API -eq 0 && $DO_UI -eq 0 && $DO_THIRD_PARTY -eq 0 ]]; then
    do_sync
    exit 0
fi

[[ $DO_THIRD_PARTY  -eq 1 ]] && pull_third_party
[[ $DO_API          -eq 1 ]] && build_api
[[ $DO_UI           -eq 1 ]] && build_ui

# Only sync if the user explicitly asked for it (--sync or --sync-only)
[[ $DO_SYNC         -eq 1 ]] && do_sync

[[ $DO_DEPLOY_API   -eq 1 ]] && deploy_api
[[ $DO_DEPLOY_UI    -eq 1 ]] && deploy_ui
