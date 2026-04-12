## [4.3.0] — 2026-04-12

### Added
- **`status` subcommand** — shows container health, clash-system state, subscription info (URL + proxy count), and runs a live HTTP and HTTPS connectivity test through the proxy. Reports pass/fail with latency for both tests and prints the dashboard/API/proxy endpoint URLs at the end.
- **`logs [mihomo|dashboard]` subcommand** — tails the live log stream for the specified container (default: `mihomo`). Equivalent to `cd /opt/pimiproxy && docker compose logs -f`.
- **`restart [mihomo|dashboard]` subcommand** — restarts one container or all if no argument given. Equivalent to `docker compose restart`.
- **`update` subcommand** — pulls the latest Docker images for all containers and force-recreates them. Equivalent to `docker compose pull && docker compose up -d --force-recreate`.
- **Configure option 6: Change API secret** — change the Mihomo API secret post-install without reinstalling.
- **Configure option 7: Change ports** — change dashboard, API, or mixed proxy ports post-install. Dashboard port changes rebuild `docker-compose.yml`; API/proxy port changes rebuild `config.yaml`.
- **Configure option 8: Switch dashboard** — switch between Metacubexd and Zashboard without reinstalling. Pulls/loads the new image and rebuilds `docker-compose.yml` on save.

### Changed
- **Configure save (now option 9)** — Save and apply now rebuilds `docker-compose.yml` when port or dashboard changes are pending, and updates meta so `status`/`logs`/`restart` read the correct ports after a change.
- **Image names extracted to module-level constants** — `MIHOMO_IMAGE`, `METACUBEXD_IMAGE`, `ZASHBOARD_IMAGE` — used by install, update, and configure dashboard switch.
- **`arch` stored in install metadata** — used by configure dashboard switch to pull the correct platform image.

---

## [4.2.0] — 2026-04-12

### Added
- **`hostname` subcommand** — `sudo python3 pimiproxy-core.py hostname <new-hostname>` changes the system hostname without requiring a full reinstall. Updates `/etc/hostname`, `/etc/hosts`, cloud-init if present, and reminds you to restart avahi-daemon for mDNS to pick up the change.

### Changed
- **Hostname prompt at install is now optional** — if the device already has a custom hostname (anything not in the generic defaults: `raspberrypi`, `localhost`, `debian`, `ubuntu`, `alarm`), the installer pre-fills the prompt with the current hostname rather than defaulting to `pimiproxy`. Pressing Enter keeps the existing name unchanged. The hostname step is skipped entirely if the entered value matches the current hostname.

---

## [4.1.0] — 2026-04-11

### Fixed
- **`run()` missing `cwd` support** — `compose_run()` passed `cwd=` to `run()` but `run()` silently dropped it, causing a `TypeError` crash at the "Starting services" step on every install.
- **Mihomo crash loop on startup** — `fallback-filter: geoip: false` added to generated DNS config, preventing Mihomo from attempting to download the MaxMind MMDB database on first run. Previously Mihomo would fatal-exit if it couldn't reach GitHub to download the file.
- **Provider `GEOIP,*` rules crash** — Rules injected from the provider's ruleset are now filtered to remove `GEOIP,*` entries before writing to `config.yaml`. These rules require the MMDB database at parse time; the provider's existing `IP-CIDR` entries cover the same traffic without it.

### Added
- **`refresh` subcommand** — `sudo python3 pimiproxy-core.py refresh` re-fetches the subscription URL, rebuilds `config.yaml` with fresh proxy/group/rule data, and restarts Mihomo. Safe to run at any time.
- **"Refresh subscription now" in configure menu** — option 5 in the interactive configurator triggers the same fetch-rebuild-restart cycle without leaving the menu.
- **Daily cron refresh** — `/etc/cron.daily/pimiproxy-refresh` installed at install time; re-fetches the subscription and rebuilds config each night automatically. Removed by the uninstaller.
- **Script copied to install directory** — `pimiproxy-core.py` is copied to `/opt/pimiproxy/` at install time so the cron job has a stable path to call it from.

### Changed
- **Subscription handling rewritten** — `proxy-providers` with `type: http` removed. Instead, the installer now fetches the subscription URL at install time and injects the `proxies:`, `proxy-groups:`, and `rules:` blocks verbatim into `config.yaml`. This works regardless of whether the provider returns a full Clash config or a proxies-only list, and eliminates the startup delay waiting for Mihomo to fetch its own providers.
- **Provider groups and rules used directly** — when the subscription response includes `proxy-groups:` and `rules:` sections, they are used as-is rather than replaced with pimiproxy-generated defaults. pimiproxy only contributes the infrastructure layer: ports, API controller, TUN, DNS.

---

## [4.0.0] — 2026-04-11

### Added (bootstrap)
- `pimiproxy-bootstrap.sh` — single-command setup for any supported Linux distro
- Distro detection via `/etc/os-release` with ID_LIKE fallback, covers: Debian/Raspbian/Ubuntu/Mint/Pop, Fedora/RHEL/CentOS/Rocky/Alma, Arch/Manjaro/EndeavourOS, openSUSE, Alpine
- Package manager auto-selection (apt, dnf, pacman, zypper, apk)
- Docker install via official `get.docker.com` script when not already present — always installs the current engine + Compose plugin in one step, works across all supported distros
- curl/wget fallback for fetching the Docker install script (installs curl first if neither is available)
- raspi-config installed automatically on Raspberry Pi when using an apt-based distro
- Bootstrap hands off to Python via `exec python3 pimiproxy.py` — no subprocess, no shell wrapper overhead
- Bootstrap validates Python >= 3.7 before proceeding

### Added (Python)
- `Platform` class — detects OS, package manager, Raspberry Pi model, systemd availability, and Docker arch string (aarch64/arm64/armv7l/x86_64 all mapped correctly) at startup
- `PLAT.docker_arch` — returns correct `linux/arm64`, `linux/arm/v7`, `linux/amd64` etc. automatically, no hardcoded arch
- `PLAT.pkg_install()` — installs packages using the detected package manager, used by `ensure_avahi()` and uninstall
- `set_hostname()` — platform-aware: uses `hostnamectl` if available, falls back to direct `/etc/hostname` write; calls `raspi-config nonint do_hostname` additionally on Raspberry Pi; updates `/etc/hosts` and cloud-init in all cases
- `reset_hostname()` — calls `set_hostname()` for consistent cleanup
- `ensure_avahi()` — installs avahi with correct package names per distro (avahi-daemon/libnss-mdns on apt, avahi/nss-mdns on dnf/pacman, etc.)
- Uninstall removes avahi with correct package names per distro

### Changed
- `PLAT.docker_arch` replaces hardcoded `linux/arm64` — script now works correctly on x86_64 hosts too
- Docker availability check in install now gives a clear message directing user to run the bootstrap rather than attempting its own install

---

## [3.0.0] — 2026-04-11

Complete rewrite in Python. The bash script is retired.

### Added
- `configure` subcommand — interactive menu to manage subscription URLs, proxy groups, and rules without editing YAML by hand
- Proxy group builder — choose type (select/url-test/fallback/load-balance), source (provider or named proxies), and all relevant settings interactively
- Rule editor — add, remove, reorder rules interactively; insert at any position; apply common presets (LAN bypass, China GEOIP, catch-all)
- Rule presets: LAN bypass, local hostname bypass, China mainland DIRECT, catch-all to Proxy, catch-all to DIRECT
- Extended config stored as `.pimiproxy-config.json` alongside the metadata, giving `configure` mode a structured representation to work with rather than parsing YAML
- Metadata stored as JSON (`pimiproxy-meta.json`) — no shell sourcing, no quoting issues possible by design
- `compose_run()` / `find_compose()` functions replace all string-based compose invocation — compose command is never stored or sourced as a string

### Changed
- All file generation uses Python string building (`L()` helper, list of lines joined at end) — structurally impossible to introduce tabs or control characters into generated YAML
- No heredocs anywhere in the codebase
- No shell variable quoting edge cases — Python handles all string interpolation
- `clash-system` utility is generated by Python using the same line-by-line approach

### Removed
- Bash script (`pimiproxy.sh`) retired
- All heredoc-based file generation
- `COMPOSE` as a space-separated shell variable

---

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