#!/usr/bin/env bash
# 4_publish-release.sh — publish releases/*.tar.gz as a GitHub Release
#
# Release archives are not committed to git: their Git LFS history outgrew
# GitHub's free 10 GiB quota. They ship as assets on a GitHub Release tagged
# v<VERSION> instead — no total-size or bandwidth limit, each file under 2 GiB.
# Requires the GitHub CLI, authenticated (gh auth login). Run after
# 3_build-releases.sh, once the release commit is pushed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RELEASES="$ROOT_DIR/releases"

PIMIPROXY_VERSION="$(cat "$ROOT_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')"
TAG="v${PIMIPROXY_VERSION}"

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
${bold}Usage:${reset} $0 [--draft]

Creates GitHub Release ${TAG} (from VERSION) at the current commit, with every
releases/*.tar.gz attached and the matching docs/CHANGELOG.md section as notes.

  --draft     Create it as a draft, to review on GitHub before publishing

The repo is taken from the git remote; set GH_REPO=owner/name to override.
EOF
    exit 1
}

DRAFT=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --draft)   DRAFT=(--draft) ;;
        -h|--help) usage ;;
        *)         echo "Unknown flag: $1"; usage ;;
    esac
    shift
done

[[ -n "$PIMIPROXY_VERSION" ]] || { fail "VERSION is empty"; exit 1; }
command -v gh >/dev/null 2>&1 || { fail "GitHub CLI (gh) not found — install it, then run: gh auth login"; exit 1; }

shopt -s nullglob
archives=("$RELEASES"/*.tar.gz)
if [[ ${#archives[@]} -eq 0 ]]; then
    fail "No archives in releases/ — run ./scripts/3_build-releases.sh first"
    exit 1
fi

# Never publish a stale build under a new tag: every archive must carry VERSION.
step "Checking ${#archives[@]} archives are ${PIMIPROXY_VERSION}"
for a in "${archives[@]}"; do
    case "$(basename "$a")" in
        pimiproxy-ultralite-*) top=pimiproxy-ultralite ;;
        *)                     top=pimiproxy ;;
    esac
    v="$(tar -xzOf "$a" --occurrence=1 "${top}/VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$v" != "$PIMIPROXY_VERSION" ]]; then
        fail "$(basename "$a") is version '${v:-unknown}', expected ${PIMIPROXY_VERSION}"
        echo "    Rebuild: ./scripts/3_build-releases.sh" >&2
        exit 1
    fi
done
ok "All archives are ${PIMIPROXY_VERSION}"

# Release notes = this version's CHANGELOG section, trimmed of the blank lines
# and `---` rule around it.
notes="$(awk -v hdr="## [${PIMIPROXY_VERSION}]" '
    index($0, hdr) == 1 { found = 1; next }
    found && /^## \[/   { exit }
    found               { line[++n] = $0 }
    END {
        while (n > 0 && line[n] ~ /^(---)?[[:space:]]*$/) n--
        for (s = 1; s <= n && line[s] ~ /^[[:space:]]*$/; s++) ;
        for (i = s; i <= n; i++) print line[i]
    }
' "$ROOT_DIR/docs/CHANGELOG.md")"
if [[ -z "$notes" ]]; then
    fail "No '## [${PIMIPROXY_VERSION}]' section in docs/CHANGELOG.md"
    exit 1
fi

commit="$(git -C "$ROOT_DIR" rev-parse HEAD)"
step "Creating GitHub Release ${TAG} at ${commit:0:7}${DRAFT:+ (draft)}"
cd "$ROOT_DIR"
gh release create "$TAG" "${archives[@]}" \
    --title "pimiproxy ${PIMIPROXY_VERSION}" \
    --notes "$notes" \
    --target "$commit" \
    "${DRAFT[@]}"
ok "Published ${TAG} with ${#archives[@]} archives"
