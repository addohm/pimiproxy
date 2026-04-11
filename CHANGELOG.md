## [2.2.0] — 2026-04-11

### Fixed
- **Metadata sourcing crash (root cause):** The real issue was not just quoting — `COMPOSE="docker compose"` is fundamentally unsafe to source because bash interprets the space as a command separator. Replaced entirely with two separate variables: `COMPOSE_BIN` (the executable) and `COMPOSE_SUB` (the subcommand). These are stored with `printf %q` so they survive source correctly even if they ever contain special characters. The `compose_run()` function assembles and executes them safely.
- **YAML tab contamination:** All YAML and config file writing has been moved off heredocs entirely. Every line is written with an explicit `printf '%s\n'` call via a local `cl()` helper function. This makes it structurally impossible for editor tab settings, shell indentation, or heredoc handling differences across bash versions to introduce tab characters into YAML files.
- **clash-system written without heredoc:** The `/usr/local/bin/clash-system` script is now also written line-by-line with `cl()` — no heredoc, no tab risk.
- **docker-compose.yml written without heredoc:** Same treatment — explicit `printf` per line.

### Changed
- Entire script rewritten to eliminate all heredocs from file-generation paths. Heredocs remain only for the outer script structure (none), not for any generated file content.
- `compose_run()` function replaces all direct `$COMPOSE` variable invocations — compose is never stored as a space-separated string in a variable or file again.

---

# Changelog

All notable changes to pimiproxy are documented here.

Format: `[version] — date` followed by categorised changes.
Categories: `Added`, `Fixed`, `Changed`, `Removed`.

---

## [2.1.1] — 2026-04-11

### Fixed
- Uninstall crash: `.pimiproxy-meta` was storing `COMPOSE=docker compose` unquoted; when bash sourced the file it executed `compose` as a standalone command and hit a fatal error on line 6. Fixed by writing the value as `COMPOSE='docker compose'` with embedded single quotes so it sources correctly regardless of spaces in the command.

---

## [2.1.0] — 2026-04-11

### Added
- Subscription URL prompt during install — users can paste a Clash/Mihomo-compatible API URL at install time
- When a URL is provided, the installer generates a complete working config: `proxy-providers` block with daily refresh and health checks, a `select` group for manual server choice, a `url-test` group for automatic fastest-server selection, and LAN/private IP bypass rules above the catch-all
- `./config/providers/` directory created automatically when a subscription is configured, giving Mihomo a local path to cache the fetched provider file
- When no URL is provided, the generated `config.yaml` includes a comment pointing to the Mihomo proxy-providers documentation

### Changed
- `config.yaml` generation split into a base block (always written) and a conditional append (subscription vs. stub), making each section independently maintainable

---

## [2.0.0] — 2026-04-11

Complete rewrite of the installation script. All prior versions were development iterations and are not documented individually.

### Added
- Single-script install/uninstall — one file, two subcommands: `install` and `uninstall`
- `set -Eeuo pipefail` with `ERR` trap for reliable failure detection and line-number reporting
- `detect_compose()` helper — automatically selects Docker Compose v2 plugin or v1 standalone, whichever is available
- `load_image()` helper — loads from local `.tar.gz` archive if present, pulls from registry as fallback; returns only the clean image tag to stdout
- All logging functions (`info`, `step`, `warn`, `die`) write exclusively to stderr, preventing output from polluting variable captures
- Install metadata file written to `$INSTALL_DIR/.pimiproxy-meta` — stores install-time settings so uninstall can reference them without guesswork
- `clash-system` utility installed to `/usr/local/bin/clash-system` with `on`, `off`, `status`, and `restart` subcommands
- `clash-system` auto-escalates to sudo if not run as root
- `clash-system status` reports both routing rule state and TUN interface presence
- Uninstaller stops containers, removes Docker images, clears routing rules, removes all files, and optionally resets hostname and removes avahi-daemon
- mDNS support via `avahi-daemon` and `libnss-mdns` — Pi becomes reachable as `<hostname>.local` on the LAN
- cloud-init hostname persistence: sets `preserve_hostname: true` if `/etc/cloud/cloud.cfg` is present
- `docker-compose.yml` written with `printf` line-by-line to eliminate heredoc control-character issues
- `fake-ip-filter` entries for `*.lan` and `*.local` to prevent mDNS/LAN hostnames from being intercepted by Mihomo's fake-ip DNS

### Fixed
- ANSI escape codes from log functions no longer leak into captured variable values (was causing YAML parse errors in generated `docker-compose.yml`)
- `docker-compose.yml` no longer fails with "control characters are not allowed" when image tag variables contain colour codes
- `docker-compose-v3` package name (invalid) replaced with correct `docker-compose-plugin` with fallback to `docker-compose`
- `/etc/hosts` update now correctly handles both the case where a `127.0.1.1` line exists and where it does not
- Image tag parsing from `docker load` output now uses `awk` correctly; falls back to pull if tag cannot be parsed

### Changed
- `restart: always` changed to `restart: unless-stopped` for both containers — prevents auto-restart after a deliberate `docker compose stop`
- Compose file no longer uses top-level `version:` key (deprecated in Compose v2)
- Summary output shows both `.local` hostname and raw IP address for all service endpoints