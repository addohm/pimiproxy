#!/usr/bin/env bash
# 3_build-releases.sh — generate release archives for pimiproxy and pimiproxy-ultralite
# Requires: Docker images built via 2_rebuild.sh and mihomo binaries in bin/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RELEASES="$ROOT_DIR/releases"
DOCKER_IMAGES="$ROOT_DIR/docker-images"
BIN_DIR="$ROOT_DIR/bin"
UPDATES="$RELEASES/updates"

PIMIPROXY_VERSION="$(cat "$ROOT_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')"
PIMIPROXY_VERSION="${PIMIPROXY_VERSION:-0.0.0-dev}"

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

${bold}Output — each variant is built in two flavors:${reset}
  releases/pimiproxy-<arch>-offline.tar.gz             (full, self-contained: images + Docker engine + geodata)
  releases/pimiproxy-<arch>.tar.gz                     (full, online: source + geodata; builds/pulls at install)
  releases/pimiproxy-ultralite-<arch>-offline.tar.gz   (ultralite, self-contained: mihomo binary + geodata)
  releases/pimiproxy-ultralite-<arch>.tar.gz           (ultralite, online: downloads the mihomo binary at install)

  Offline archives install with no network; online archives are small and fetch
  the heavy parts (images / Docker engine / mihomo binary) at install time.
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
        local path="$DOCKER_IMAGES/$f"
        [[ -f "$path" ]] || { fail "Missing: docker-images/$f"; exit 1; }
        # A Git LFS pointer is ~130 bytes and starts with a version line.
        # Shipping one as geodata gives Mihomo an unreadable database at
        # runtime with no way to fetch a real one (geo-auto-update is off).
        if [[ $(stat -c %s "$path") -lt 100000 ]] || head -c 40 "$path" | grep -q "git-lfs"; then
            fail "docker-images/$f is a Git LFS pointer, not real geodata."
            echo "    Run:  git lfs pull --include='docker-images/*'   or   ./scripts/1_update-upstream.sh --geodata" >&2
            exit 1
        fi
    done
}

mkdir -p "$RELEASES"

# ── Check if a Docker image archive contains the requested arch ─────────────
_has_arch_tag() {
    local archive="$1" arch="$2" img="$3"
    # A Git-LFS pointer (or a truncated archive) is ~130 bytes and starts with
    # a version line. Loading it as a docker image gives an incomplete release
    # that silently omits the arch. Fail loudly instead of skipping.
    if [[ $(stat -c %s "$archive" 2>/dev/null || echo 0) -lt 100000 ]] || head -c 40 "$archive" 2>/dev/null | grep -q "git-lfs"; then
        fail "docker-images/$(basename "$archive") is a Git LFS pointer or truncated, not a real image archive."
        echo "    Run:  git lfs pull --include='docker-images/*'   or   ./scripts/1_update-upstream.sh" >&2
        exit 1
    fi
    docker load -i "$archive" >/dev/null 2>&1 || return 1
    docker image inspect "${img}:${arch}" >/dev/null 2>&1
}

# ── Build a pimiproxy full archive for one arch ──────────────────────────────
_build_full() {
    local arch="$1" mode="$2"   # mode: offline | online
    local suffix=""; [[ "$mode" == "offline" ]] && suffix="-offline"
    local archive="$RELEASES/pimiproxy-${arch}${suffix}.tar.gz"

    local required_imgs=(mihomo pimiproxy-api pimiproxy-ui)
    local dashboard_imgs=(metacubexd zashboard)
    local available_dashboards=()

    if [[ "$mode" == "offline" ]]; then
        # Offline needs the built images present for this arch.
        local missing_imgs=()
        for img in "${required_imgs[@]}"; do
            local src="$DOCKER_IMAGES/${img}.tar.gz"
            [[ -f "$src" ]] || { missing_imgs+=("${img} (archive missing)"); continue; }
            _has_arch_tag "$src" "$arch" "$img" || missing_imgs+=("${img}:${arch}")
        done
        for img in "${dashboard_imgs[@]}"; do
            local src="$DOCKER_IMAGES/${img}.tar.gz"
            [[ -f "$src" ]] || continue
            _has_arch_tag "$src" "$arch" "$img" && available_dashboards+=("${img}")
        done
        [[ ${#available_dashboards[@]} -eq 0 ]] && missing_imgs+=("no dashboard (need metacubexd or zashboard)")
        if [[ ${#missing_imgs[@]} -gt 0 ]]; then
            skip "pimiproxy-${arch}${suffix}: ${missing_imgs[*]}"; return 0
        fi
        step "pimiproxy-${arch}-offline  ${dim}(dashboards: ${available_dashboards[*]})${reset}"
    else
        step "pimiproxy-${arch}  ${dim}(online — builds api/ui + pulls images + installs Docker at install time)${reset}"
    fi

    # Stage source (both flavors) — the online installer builds api/ui from
    # this source and pulls mihomo/dashboard from the registry.
    local staging; staging=$(mktemp -d)
    mkdir -p "$staging/pimiproxy/docker-images" \
             "$staging/pimiproxy/pimiproxy-api" \
             "$staging/pimiproxy/pimiproxy-ui"
    cp "$ROOT_DIR/pimiproxy" "$ROOT_DIR/pimiproxy-core.py" \
       "$ROOT_DIR/pimiproxy_common.py" "$ROOT_DIR/README.md" \
       "$ROOT_DIR/VERSION" "$staging/pimiproxy/"
    rsync -a --exclude='pmp-*-plan.md' --exclude='DEV_INSTRUCTIONS.md' --exclude='MAINTAINER.md' \
        "$ROOT_DIR/docs/" "$staging/pimiproxy/docs/"
    # Geodata is bundled in BOTH flavors — small, and its source (GitHub) is
    # blocked in China, so we never rely on fetching it at runtime.
    cp "$DOCKER_IMAGES/geoip.metadb" "$DOCKER_IMAGES/geosite.dat" "$DOCKER_IMAGES/country.mmdb" \
       "$staging/pimiproxy/docker-images/"
    rsync -a --exclude='__pycache__' "$ROOT_DIR/pimiproxy-api/" "$staging/pimiproxy/pimiproxy-api/"
    rsync -a --exclude='__pycache__' --exclude='node_modules' "$ROOT_DIR/pimiproxy-ui/" "$staging/pimiproxy/pimiproxy-ui/"

    if [[ "$mode" == "offline" ]]; then
        # Bundle the built images + the static Docker engine (self-contained).
        local img_tmp; img_tmp=$(mktemp -d)
        for img in "${required_imgs[@]}" "${available_dashboards[@]}"; do
            docker save "${img}:${arch}" | gzip > "$img_tmp/${img}.tar.gz"
        done
        cp "$img_tmp"/*.tar.gz "$staging/pimiproxy/docker-images/"
        rm -rf "$img_tmp"
        local de_tgz="$ROOT_DIR/docker-engine/docker-${arch}.tgz"
        local de_comp="$ROOT_DIR/docker-engine/docker-compose-${arch}"
        if [[ -f "$de_tgz" ]]; then
            if [[ $(stat -c %s "$de_tgz" 2>/dev/null || echo 0) -lt 10000000 ]] || head -c 40 "$de_tgz" | grep -q "git-lfs"; then
                fail "docker-engine/docker-${arch}.tgz is a Git LFS pointer, not the real bundle."
                echo "    Run:  git lfs pull --include='docker-engine/*'   or   ./scripts/1_update-upstream.sh --docker" >&2
                exit 1
            fi
            mkdir -p "$staging/pimiproxy/docker-engine"
            cp "$de_tgz" "$staging/pimiproxy/docker-engine/"
            [[ -f "$de_comp" ]] && cp "$de_comp" "$staging/pimiproxy/docker-engine/"
        else
            skip "docker-engine/docker-${arch}.tgz absent — offline archive will need a network Docker install"
        fi
    fi

    tar czf "$archive" -C "$staging" pimiproxy
    ok "$(basename "$archive")  ($(du -sh "$archive" | cut -f1))"
    rm -rf "$staging"
}

# ── Build a pimiproxy-ultralite archive for one arch ─────────────────────────
_build_ultralite() {
    local arch="$1" mode="$2"   # mode: offline | online
    local suffix=""; [[ "$mode" == "offline" ]] && suffix="-offline"
    local archive="$RELEASES/pimiproxy-ultralite-${arch}${suffix}.tar.gz"

    local bin_match=""
    if [[ "$mode" == "offline" ]]; then
        bin_match=$(ls "$BIN_DIR"/mihomo-linux-${arch}-*.gz 2>/dev/null | head -1)
        if [[ -z "$bin_match" ]]; then
            skip "pimiproxy-ultralite-${arch}${suffix}: no mihomo binary in bin/"; return 0
        fi
        step "pimiproxy-ultralite-${arch}-offline"
    else
        step "pimiproxy-ultralite-${arch}  ${dim}(online — downloads the mihomo binary at install time)${reset}"
    fi

    local staging; staging=$(mktemp -d)
    mkdir -p "$staging/pimiproxy-ultralite/bin" \
             "$staging/pimiproxy-ultralite/docker-images"
    cp "$ROOT_DIR/pimiproxy-ultralite" "$ROOT_DIR/pimiproxy-ultralite-core.py" \
       "$ROOT_DIR/pimiproxy_common.py" "$ROOT_DIR/README.md" \
       "$ROOT_DIR/VERSION" "$staging/pimiproxy-ultralite/"
    rsync -a --exclude='pmp-*-plan.md' --exclude='DEV_INSTRUCTIONS.md' --exclude='MAINTAINER.md' \
        "$ROOT_DIR/docs/" "$staging/pimiproxy-ultralite/docs/"
    cp "$DOCKER_IMAGES/geoip.metadb" "$DOCKER_IMAGES/geosite.dat" "$DOCKER_IMAGES/country.mmdb" \
       "$staging/pimiproxy-ultralite/docker-images/"
    [[ "$mode" == "offline" ]] && cp "$bin_match" "$staging/pimiproxy-ultralite/bin/"

    tar czf "$archive" -C "$staging" pimiproxy-ultralite
    ok "$(basename "$archive")  ($(du -sh "$archive" | cut -f1))"
    rm -rf "$staging"
}

# ── Update bundles ───────────────────────────────────────────────────────────
# Small per-arch api+ui image bundles + a manifest, for `pimiproxy update` to
# fetch from a configured update_url (any static file host). Not committed to
# git (releases/updates/ is gitignored) — the hosting channel is decided at
# publish time.
_build_update_bundles() {
    step "Update bundles (releases/updates/, v${PIMIPROXY_VERSION})"
    rm -rf "$UPDATES"
    mkdir -p "$UPDATES"
    local manifest_files=()
    for arch in "${FULL_ARCHES[@]}"; do
        for img in pimiproxy-api pimiproxy-ui; do
            if ! docker image inspect "${img}:${arch}" >/dev/null 2>&1; then
                skip "${img}:${arch} not in Docker — no update bundle for ${arch}"
                continue 2
            fi
        done
        for img in pimiproxy-api pimiproxy-ui; do
            local out="$UPDATES/${img}-${arch}.tar.gz"
            docker save "${img}:${arch}" | gzip > "$out"
            manifest_files+=("$(basename "$out")")
        done
        ok "update bundles for ${arch}"
    done
    if [[ ${#manifest_files[@]} -eq 0 ]]; then
        skip "no update bundles built — manifest not written"
        return 0
    fi
    {
        echo "{"
        echo "  \"version\": \"${PIMIPROXY_VERSION}\","
        echo "  \"files\": {"
        local i last=$(( ${#manifest_files[@]} - 1 ))
        for i in "${!manifest_files[@]}"; do
            local f="${manifest_files[$i]}" comma=","
            [[ $i -eq $last ]] && comma=""
            local sum size
            sum=$(sha256sum "$UPDATES/$f" | cut -d' ' -f1)
            size=$(stat -c %s "$UPDATES/$f")
            echo "    \"$f\": {\"sha256\": \"$sum\", \"size\": $size}$comma"
        done
        echo "  }"
        echo "}"
    } > "$UPDATES/manifest.json"
    ok "manifest.json written (v${PIMIPROXY_VERSION})"
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
        rm -f "$RELEASES"/pimiproxy-${a}.tar.gz "$RELEASES"/pimiproxy-${a}-offline.tar.gz 2>/dev/null
    done
fi
if [[ $DO_ULTRALITE -eq 1 ]]; then
    rm -f "$RELEASES"/pimiproxy-ultralite-*.tar.gz 2>/dev/null
fi

if [[ $DO_FULL -eq 1 ]]; then
    for arch in "${FULL_ARCHES[@]}"; do
        _build_full "$arch" offline
        _build_full "$arch" online
    done
    _build_update_bundles
fi

if [[ $DO_ULTRALITE -eq 1 ]]; then
    for arch in "${ULTRALITE_ARCHES[@]}"; do
        _build_ultralite "$arch" offline
        _build_ultralite "$arch" online
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
