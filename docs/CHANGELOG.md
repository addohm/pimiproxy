# Changelog

All notable changes to pimiproxy are documented here.
For a narrative per-version overview, see [VERSION_HISTORY.md](VERSION_HISTORY.md).

Format: `[version] — date` followed by categorised changes.
Categories: `Added`, `Fixed`, `Changed`, `Removed`.

---

## [Unreleased]

### Added — provider-managed `fake-ip-filter` and `nameserver-policy`
- The subscription's `dns` block is no longer discarded wholesale: refresh now
  lifts `fake-ip-filter` entries and `nameserver-policy` mappings out of it
  (hand-parsed — no PyYAML; quoted keys like `'geosite:cn'`, inline flow
  lists, and block lists all handled) and caches them in
  `.pimiproxy-config.json` (`subscription_fake_ip_filter`,
  `subscription_nameserver_policy`). Everything else in the provider's dns
  block stays ignored — the generated dns section (nameserver ordering,
  tunneled DoH fallback, hijack) is load-bearing and remains local.
- `build_config` merges them without duplicates: fake-ip-filter emits the
  local fleet-proven baseline first, then user extras, then provider entries —
  all deduplicated, so a provider carrying the same baseline adds nothing
  twice; nameserver-policy emits the local captive-portal defaults with
  provider entries overriding on pattern collision and appended otherwise.
  No provider DNS data → output identical to before.
- The CLI/cron and API refresh paths now share one `subscription_updates()`
  helper (config key writes can't drift); the API falls back gracefully when
  running against an older mounted `pimiproxy_common.py`. Verified against
  the live addohm.net-served config (108 provider fake-ip entries) with
  `mihomo -t` passing on both merged and no-provider outputs.

### Added — versioned releases and a real update path for the api/ui images
- pimiproxy now has a version identity: the `VERSION` file at the repo root is
  the single source of truth. `2_rebuild.sh` stamps it into the `pimiproxy-api`
  and `pimiproxy-ui` images as the `io.pimiproxy.version` label (plus the git
  short SHA), release archives ship a `VERSION` copy next to the core script,
  and installs record it in `.pimiproxy-meta.json`. `status` and the login
  banner display it on both variants.
- `sudo pimiproxy update` is now version-aware for the api/ui pair — they are
  our own packages with no registry behind them, so previously `update` could
  only blind-load whatever archive happened to sit on disk (and silently did
  nothing when none did). Now it reports the installed version, skips archives
  that aren't newer than what's running, and — fixing a real bug — retags the
  arch-specific tag from a loaded archive to the `:latest` tag that
  `docker-compose.yml` actually runs (previously a loaded archive never reached
  the containers).
- New optional `update_url` config key (unset by default): when set, and no
  local archive or source is available, `update` fetches
  `<update_url>/manifest.json` and, if it advertises a newer version, downloads
  the per-arch api/ui bundles, verifies their sha256, and loads both
  atomically — any failure leaves the running images untouched. Requests go
  through the local Mihomo mixed port first (so the update host is reached via
  the tunnel, even from China or with host bypass on) and fall back to direct.
  No default host is baked in — the distribution channel is deliberately left
  to deployment.
- `3_build-releases.sh` now emits `releases/updates/` — per-arch
  `pimiproxy-{api,ui}-<arch>.tar.gz` bundles (~60–77 MB each vs the ~350 MB
  full archives) plus a `manifest.json` with version and sha256 per file:
  exactly the layout `update_url` expects, ready to copy onto any static file
  host. The directory is gitignored (regenerated per release).
- Fixed the armv7 arch-tag mapping at install time: `linux/arm/v7` previously
  mapped to a nonexistent `:v7` tag suffix, so armv7 archive installs fell back
  to whatever tag loaded first.

### Added — `pimiproxy doctor`: guided diagnostics
- New `doctor` subcommand: seven read-only checks over the whole pipeline (engine, device-style
  connectivity check, delay test of every group's selected server, DNS honesty, Quest store
  endpoint, UDP/NTP relay, active IPv6-leak probe), each failure printing plain-language numbered
  fix steps. Exits non-zero when problems are found (cron-able). DNS honesty is verified by
  TLS-certificate validation of the locally-returned IP — comparing against a reference resolver
  false-alarms on CDN geo-variance. The IPv6 probe actively solicits Router Advertisements rather
  than trusting the routing table, which holds stale RA routes long after a router stops
  advertising.

### Fixed — DNS: GFW poisoning of fake-ip-filter exempted domains
- Domains exempted from fake-ip (Meta/Oculus, captive checks, NTP, STUN) resolved via the mainland
  UDP resolvers and got GFW-poisoned answers (`graph.oculus.com` → a dead third-party IP,
  `edge-mqtt.facebook.com` → a Twitter IP, `connectivitycheck.gstatic.com` → an ISP hijack box that
  is unreachable once traffic leaves via the proxy — devices then report "limited connection" and
  gate app downloads). `build_config` now emits: a DoH-by-IP fallback tunneled through the group of
  the final `MATCH` rule (auto-detected per deployment) with `fallback-filter: geoip: true`;
  `proxy-server-nameserver` (mainland DNS for provider hostnames — without it the tunneled DoH
  deadlocks: DoH needs the proxy, the proxy's hostname needs DNS); and a `nameserver-policy`
  pinning captive-check domains to the tunneled DoH (the geoip filter cannot catch that hijack
  because the forged answers are CN IPs).
- New fake-ip-filter built-ins: `+.oculusvr.com`, `+.fbsbx.com`. User-managed additions supported
  via `fake_ip_filter_extra` (configure → "Manage fake-IP DNS exceptions").

### Fixed — configure menu silently losing a just-edited provider URL
- "Refresh provider now" fetched from the URL typed in "Manage provider URL", but
  `save_subscription` persists only the subscription block keys, and the post-refresh reload then
  destroyed the new URL from the session too — so nothing the user did afterwards (including "Save
  and apply") could persist it, and the daily cron refresh reverted the whole subscription to the
  old URL. The refresh handler now persists a session-edited URL under the config lock once a
  fetch from it has succeeded and validated.
- "Manage provider URL": pressing Enter now keeps the current URL (it used to clear it); clearing
  requires explicitly typing `clear`.

### Changed — configure menu order
- "Save and apply" and "Exit without saving" moved to the bottom of the menu (now 11/12); web UI
  build is 9, fake-IP DNS exceptions is 10.

### Fixed — API container segfaults on 16 KB-page arm64 kernels (Pi 5)
- `pimiproxy-api` and `pimiproxy-ui` moved off Alpine (musl libc) onto glibc base images
  (`python:3.12-slim`, `nginx:1.27`, `node:20-slim`). musl segfaults on 16 KB-page arm64 kernels —
  e.g. the Raspberry Pi 5's `rpt-rpi-2712` kernel (`PAGE_SIZE=16384`) — so the API container
  crash-looped with exit 139 (SIGSEGV) and every UI login returned nginx **502**, which the UI
  showed as "incorrect password". glibc runs on both 4 KB (Pi Zero/2/3/4) and 16 KB (Pi 5) page
  kernels, so the single `arm64` package now works across the whole Pi range. The API image installs
  just the static Docker CLI (pinned to the bundled engine version).


### Changed — two release flavors per variant (offline / online)
- `3_build-releases.sh` now builds **two flavors** of each variant per arch, clearly labeled:
  `pimiproxy-<arch>-offline.tar.gz` (self-contained: images + static Docker engine + geodata) and
  `pimiproxy-<arch>.tar.gz` (online: source + geodata; the installer builds `pimiproxy-api`/`-ui`
  from source, pulls mihomo/dashboard, and installs Docker from the network). Same split for
  ultralite (`-offline` bundles the mihomo binary; online downloads it from GitHub at install).
  Geodata is bundled in both flavors. Full: amd64/arm64/armv7; ultralite: +386.
- Ultralite installer gained a GitHub mihomo-binary download fallback (pinned to the bundled
  version) so the online ultralite flavor works when no binary is bundled.


### Phase 7 — offline Docker Engine bundle
- The full variant bundled container images offline but not the Docker Engine daemon, so a
  fully-offline box (China / air-gap) couldn't `docker load` them — install died at the Docker step.
  `1_update-upstream.sh --docker` now fetches the static `dockerd`/`containerd`/`runc`/`docker-init`
  bundle + the compose plugin per arch (amd64/arm64/armv7) into `docker-engine/`;
  `3_build-releases.sh` bundles them into full archives; and the `pimiproxy` wrapper installs the
  engine from the bundle (binaries to `/usr/local/bin`, shipped `containerd`/`docker` systemd units)
  BEFORE any network path, falling back to get.docker.com / Aliyun only if the bundle is absent
  (fixes H8).
- `timed_step` now returns the real exit code and reports FAILED instead of always printing "done",
  and `curl | sh` runs under `pipefail`, so a blocked get.docker.com is a real, visible failure
  instead of a false success (fixes M3).

### Phase 9 — robustness cleanup
- The UI parsed every response as JSON before checking `res.ok`, so an nginx 502/504 HTML page (API
  down) surfaced as a cryptic `SyntaxError` instead of "Backend unavailable" (fixes M8).
- The provider URL endpoint validates the scheme (http/https) instead of storing any string (L2).
- `do_test` imports `urllib.error` explicitly instead of relying on an import side effect (L10).

### Phase 8 — security hardening
- The login brute-force throttle keyed on the client-controlled `X-Forwarded-For` header (which
  nginx only appends to), so an attacker could dodge the per-IP lockout by rotating it. It now keys
  on nginx's trusted `REMOTE_ADDR` (fixes H4).
- The API secret is compared with `secrets.compare_digest` (constant-time) instead of `==` (L3).
- `SESSION_COOKIE_SECURE` is enabled when `PIMIPROXY_TLS=1` (opt-in; default off for plain-HTTP LAN)
  (L4). `ALLOWED_HOSTS` remains permissive-by-default with a documented `PIMIPROXY_ALLOWED_HOSTS`
  override, since the real CSRF defense is the Origin check (M7).

### Phase 6 — release packaging integrity
- Release archives could ship 132-byte Git-LFS **pointer** files (geodata and image `.tar.gz`) when
  the build machine hadn't run `git lfs pull` — yielding unreadable geodata (GEOSITE/GEOIP rules
  silently fail with geo-auto-update off) or a release silently missing an arch. `3_build-releases.sh`
  now rejects pointer/truncated geodata AND image archives loudly with the fix command (H5, M4), and
  `1_update-upstream.sh --geodata` downloads real geodata.
- `2_rebuild.sh` now surfaces cross-arch build errors (last 15 lines) instead of discarding them, and
  no longer lets a failed `build_api` abort before `build_ui` under `set -e` (M4).
- The installer refreshes bundled geodata on reinstall when the shipped file differs, instead of only
  copying when absent (L6).

### Phase 5 — install preflight (prevents the dual-mihomo collision)
- The installer now checks for another `mihomo` systemd unit, a `mihomo` binary outside our own
  path, an existing `mihomo` TUN device, and anything already listening on the mixed/API ports, and
  refuses to continue without explicit confirmation (or `--force`). A second Mihomo silently wins the
  TUN device and port 7890, so pimiproxy's own engine never starts while the install looks fine —
  the exact condition found on the live box. Reinstalling over pimiproxy's own install is still
  allowed. Ultralite no longer silently overwrites a foreign `/etc/systemd/system/mihomo.service` or
  `/usr/local/bin/mihomo` (fixes H1).

### Phase 4 — real engine health (fixes silent TUN stop / false "running")
- `status`, the login banner, and the UI Overview previously reported the engine as "running"
  whenever the `mihomo` TUN device existed — which stays true when the process is dead, or when a
  DIFFERENT mihomo owns that device. Health now means the container/service is running AND the
  engine's own controller answers on its port (fixes H2, the reported "TUN stopped, no notice").
- `status` reports who holds the mixed port and flags a foreign mihomo serving in pimiproxy's place;
  it now exits non-zero when unhealthy so it can be scripted/monitored (fixes M2). The banner no
  longer claims "via proxy" when the engine is down (L9). The UI shows the reason under the status
  dot. Fixes a doubled `v` in the version display.

### Phase 3 — validate before restart (fixes silent crash-loop / 502)
- Every write path now validates the new config with Mihomo's own test mode (`mihomo -t`) BEFORE
  persisting anything and restarting. If Mihomo rejects it, the API returns **422** with Mihomo's
  exact reason and nothing on disk changes; the CLI prints it and keeps the previous config live
  (fixes C1 — the reported "nothing tunnels / 502 while everything looks fine" class).
- After a restart the engine's controller is polled until it answers; the API returns **502** with
  the reason if it doesn't, instead of reporting success for a crash-looping engine (fixes M1).
- Imported proxy-groups/rules are shape-validated (non-empty name+type / non-empty string) so a
  malformed import is rejected with a clean 4xx instead of poisoning the config and breaking every
  later rebuild including cron (fixes H6). Group add/delete tolerate legacy nameless groups (M6).
  Refresh counts proxies from the stored extraction and aborts on an empty fetch (M5).

### Phase 2 — safe config I/O (fixes the reverting subscription URL)
- `.pimiproxy-config.json` has three independent writers (host CLI, daily cron, API container via
  its bind mount), and every one previously did an unlocked whole-file read-modify-write. A refresh
  that snapshotted the config before its up-to-30 s fetch wrote the stale copy back afterward,
  reverting any change made in between (fixes C2, C3 — the reported "subscription URL reverted" bug).
- New shared helpers in `pimiproxy_common.py`: `config_lock()` (flock, honored across the bind
  mount), `write_config()` (temp-file + fsync + atomic rename, mode-preserving — readers never see a
  torn file), and `update_config()` (lock → re-read → mutate only the keys you own → atomic write).
  All CLI, cron, and API writers now go through them; refresh writes only the three `subscription_*`
  keys and re-reads under the lock after the fetch.

### Removed — `🧭 Meta Quest` group + rules injection
- `build_config` no longer injects the hardcoded `🧭 Meta Quest` proxy group or its 19
  `DOMAIN-SUFFIX,…,🧭 Meta Quest` routing rules. These are being moved into the provider/subscription
  config instead. Group merge is now `provider groups → user groups`; rule order is `user rules →
  provider rules → MATCH`. The `fake-ip-filter` DNS allow-list (Meta/Oculus/Facebook/connectivity)
  is retained — it governs DNS resolution, not routing, and is still needed for the Quest to report
  online. A config with no provider and no user groups now omits `proxy-groups:` entirely instead of
  emitting an empty group list.
- Removed the leftover install-time "select a proxy for the 🧭 Meta Quest group" next-step message,
  the API's `system_group_names` (so the UI no longer shows a phantom System group), and the stale
  docs that described the group as auto-injected (README, INSTRUCTIONS §11, MAINTAINER, CLAUDE, PROMPT).

### Phase 1 — one config generator
- **`build_config()` unified into `pimiproxy_common.py`.** It previously existed as three
  hand-synced copies (core, ultralite, `config_builder.py`); drift between them had already caused a
  shipped regression. The API container now loads the copy mounted from the install dir (bundled
  fallback baked into the image at build time), so the UI and host CLI always generate identical
  `config.yaml`.
- The API refresh endpoint now uses the shared `fetch_subscription`/`extract_yaml_blocks` instead of
  its own regex parser — so a UI refresh strips GEOIP rules (which need a GitHub MMDB download,
  fatal on blocked networks) and handles zero-indent provider blocks the same as the CLI (fixes H3).
- Appended proxy-groups (`🧭 Meta Quest` + user groups) now match the provider's indentation instead
  of assuming two spaces, so a 4-space provider no longer produces mixed-indent YAML (fixes H7).

## [7.0.0] — 2026-04-20

### Added
- **`pimiproxy system [on|off|status]` subcommand** — new main-CLI entry point for the host-traffic toggle. Dispatches to `/usr/local/bin/clash-system`, which remains installed and callable directly. Both variants expose the same subcommand.
- **Shared `pimiproxy_common.py` module** — colour helpers, subprocess wrappers, Spinner, hostname/DHCP, YAML subscription parser, distro/pkg abstraction. Both `pimiproxy-core.py` and `pimiproxy-ultralite-core.py` import from it, eliminating ~400 lines of copy-paste and the drift class that caused the Quest-group regression.
- **Login rate-limiting on `/api/login`** — 5 failed attempts per IP per 60-second window triggers a 5-minute lockout (429 with `Retry-After`). Successful login clears the counter. Per-Gunicorn-worker state; with the default 2 workers, real-world brute-force is capped well below anything practical.
- **`OriginCheckMiddleware`** — CSRF defence for every mutating `/api/*` request. When the browser sends `Origin` or `Referer`, its host must match the request's own `Host` header; mismatches get 403. Curl/CLI scripts that omit both headers still pass through.
- **`SESSION_COOKIE_SAMESITE = "Strict"`** — session cookies never attach to cross-site requests, closing the small window `OriginCheckMiddleware` can't cover.
- **Auto-regenerating Django `SECRET_KEY`** — if `/pimiproxy/ui-secret.txt` goes missing (corrupted volume, manual delete), the container creates a fresh `secrets.token_urlsafe(50)` at startup with `0600` perms instead of refusing to boot or accepting a hardcoded default.
- **`clash-system` utility restored** at `/usr/local/bin/clash-system` with `on|off|status` subcommands. Toggles host-originated traffic between "routes through Mihomo" (on, default) and "bypasses Mihomo / direct internet" (off). Gateway-client traffic (devices using this host as their DHCP gateway, e.g. Meta Quest) is *always* proxied regardless of the toggle because only the `OUTPUT` chain is mangled, not `FORWARD`. Implementation uses fwmark `888` (deliberately different from Mihomo's internal mark 666), ip rule at priority 5000, and a POSTROUTING MASQUERADE so packets exit with the correct source IP.
- **Optional `pimiproxy-bypass.service`** — only installed when the ADVANCED install prompt *"Bypass proxy for this device's traffic on boot?"* is answered yes. Runs `clash-system off` after Mihomo's TUN comes up, persisting the bypass across reboots. Express install never asks and leaves the boot state as "on".
- **systemd-resolved DNS scope fix** — when Mihomo's TUN comes up, `systemd-resolved` auto-uses it as a DNS scope, causing `getaddrinfo()` to return fake-IPs even when bypass is active. Installer now strips the mihomo link from resolved's DNS config (`resolvectl dns mihomo "" && default-route mihomo false && flush-caches`), and the mihomo systemd unit re-applies it via `ExecStartPost` after every start. No-op on systems without `systemd-resolved` (e.g. Pi).
- **FORWARD iptables rules** (`-i mihomo` / `-o mihomo` → ACCEPT) installed so gateway clients can reach TUN on systems where the default `FORWARD` policy is DROP (e.g. when Docker is installed).
- **Login banner — "Host traffic" line** — shows `● via proxy` (clash-system on) or `○ direct (clash-system off)` based on live `ip rule show` output.
- **`/usr/local/bin/pimiproxy` and `/usr/local/bin/pimiproxy-ultralite` symlinks** — wrapper scripts are copied to the install directory and symlinked for global access, so post-install users can run `sudo pimiproxy <cmd>` from anywhere.
- **Raspbian apt source handling** — separate `mirrors.aliyun.com/raspbian/raspbian` path and `raspbian-archive-keyring.gpg` instead of the Debian defaults, which don't exist on Raspbian.
- **Arch Linux 32 (archlinux32) mirror support** — the Yandex Russian mirror (reachable from China) with fallbacks to mirror.archlinux32.org, because Aliyun doesn't mirror archlinux32.
- **armv7 full variant** — archive is now produced for armv7 using zashboard (since upstream metacubexd doesn't publish an armv7 Docker image). Installer auto-selects the available dashboard.

### Fixed
- **Banner double-display on Debian/Raspbian/Ubuntu** — SSH login reads both `/etc/profile` (sourcing `profile.d/pimiproxy-banner.sh`) AND `~/.bashrc` (which sources `/etc/bash.bashrc`, where the banner is also sourced so desktop terminals see it). Added a one-shot guard (`_PIMIPROXY_BANNER_SHOWN`) so it prints only once per shell session.
- **Wrapper script not installed** — previously only `pimiproxy-core.py` was copied to the install dir, so `sudo /opt/pimiproxy/pimiproxy <cmd>` returned "command not found". The wrapper is now copied alongside the core script.
- **Direct invocation of core script warns** — running `pimiproxy-core.py` directly (bypassing the wrapper) prints a warning pointing users at `sudo pimiproxy <cmd>`. Driven by `PIMIPROXY_VIA_WRAPPER=1` env var set by the wrapper.
- **Quest group referenced a non-existent proxy group** — when installing without a subscription URL AND without user-defined proxy groups, the `🧭 Meta Quest` group fallback always appended `"Proxy"` / `"Provider"` to its proxies list, causing Mihomo to refuse the config with `'Proxy' not found`. Quest now references `DIRECT` plus every user group that actually exists.
- **Hardcoded fallbacks removed for `api_secret` and `SECRET_KEY`** — the API used to fall back to the literal password `"pimiproxy"` when the config was missing, and to `"insecure-dev-key-replace-at-install"` when the secret file was missing. Both fallbacks now return deny-first (503 / auto-regen respectively).
- **`ALLOWED_HOSTS` is env-var driven** (`PIMIPROXY_ALLOWED_HOSTS`) — still defaults to `*` because LAN IP access breaks any stricter default, but the real CSRF layer is now the Origin-check middleware.
- **nginx passes `$http_host`** instead of `$host` to the API container — so `request.get_host()` includes the port and the Origin-check comparison matches browser-submitted requests.
- **China-mirrors prompt skipped on reinstalls** — the ultralite wrapper asked the mirror question on every install; now it asks only when Python3 or avahi aren't already present. Matches the Full-variant behaviour (which already gated the prompt on missing Docker).

### Changed
- **Wrapper scripts renamed** — `pimiproxy.sh` → `pimiproxy` and `pimiproxy-ultralite.sh` → `pimiproxy-ultralite`. Post-install users already invoked them via the bare-name symlink (`/usr/local/bin/pimiproxy`); the in-tree file now matches. All docs, release archives, and installer copy steps updated.
- **Default UX unchanged** — fresh installs still behave identically to 6.1.0: host traffic routes through Mihomo via TUN. The bypass is opt-in at install time (advanced mode) or at runtime (`sudo pimiproxy system off` / `sudo clash-system off`).

---

## [6.1.0] — 2026-04-16

### Removed
- **`clash-system` utility** — the `/usr/local/bin/clash-system` on/off/status/restart tool has been removed. TUN mode is now always on — all device traffic (including the SBC's own traffic) is always tunneled through the proxy. There is no longer a toggle to route the SBC's traffic direct vs. through the proxy.
- **`pimiproxy-bypass.service`** — the systemd unit that ran `clash-system off` on boot is no longer needed and has been removed.
- **`/run/pimiproxy-proxy-on` marker file** — no longer written or checked; system proxy state is no longer tracked because TUN is always active.

### Changed
- **Login banner** — "System proxy" line replaced with "TUN mode: always on". The `clash-system` quick-command reference has been removed from the banner.
- **`status` subcommand** — no longer reports clash-system routing state (it was always on).
- **Uninstaller** — no longer removes `/usr/local/bin/clash-system` or clears iptables routing rules (neither exists anymore).

---

## [6.0.0] — 2026-04-15

### Added
- **pimiproxy-ultralite** — Docker-free variant. A standalone Python script (`pimiproxy-ultralite.py`) and bash bootstrap wrapper (`pimiproxy-ultralite`) that install mihomo as a native systemd service. No Docker, no web UI, no dashboard. Target use case: devices where Docker is undesirable or unavailable.
- **Native systemd service** — mihomo runs as `/etc/systemd/system/mihomo.service` with `CAP_NET_ADMIN`, `CAP_NET_RAW`, and `CAP_NET_BIND_SERVICE` ambient capabilities. No container overhead.
- **Arch-detected binary install** — mihomo release binaries placed in `bin/` are automatically selected by host architecture (`aarch64`, `armv7l`, `x86_64`) and copied to `/usr/local/bin/mihomo`.
- **Non-interactive rule subcommands** — `rule-list`, `rule-add`, `rule-remove` allow scripted rule management without entering the configure menu.
- **Install path `/opt/pimiproxy-lite/`** — coexists safely alongside a full pimiproxy install at `/opt/pimiproxy/`.
- **`bin/` directory** — holds mihomo release binaries for offline/ultralite installs. Binaries are gitignored; directory tracked via `.gitkeep`.

### Changed
- **`pimiproxy-ultralite`** — mirrors `pimiproxy` structure but drops the Docker install section entirely. Warns if no matching mihomo binary is found for the detected arch rather than exiting.
- **Shell wrapper passthrough** — both `pimiproxy` and `pimiproxy-ultralite` now skip the full bootstrap for any command other than `install`, passing directly to their Python script. Users can run all management commands through the shell wrapper (e.g. `sudo ./pimiproxy logs`) without invoking the Python script directly. Extra arguments (e.g. for `rule-add`, `hostname`, `debug`) are forwarded correctly. Both wrappers are executable — `sudo bash` is not required.

---

## [5.1.0] — 2026-04-14

### Fixed
- **Proxy names with special YAML characters cause Mihomo crash** — proxy names containing `[`, `:`, `#`, or other YAML-reserved characters (e.g. `[REALITY] [CHINA] Daily Usage 4`) were written unquoted into `proxy-groups` lists. YAML parsed them as flow sequences and Mihomo refused to start. All proxy names in group `proxies:` lists are now quoted with `yaml_quote()` in both `config_builder.py` and `pimiproxy-core.py`.
- **Meta Quest group referenced non-existent "Provider" group** — the hardcoded `- "Provider"` entry in the injected `🧭 Meta Quest` group caused a Mihomo crash loop (`proxy group: 🧭 Meta Quest: 'Provider' not found`) on subscriptions where the top-level group has a different name. The group now uses the first group name extracted from the subscription YAML, matching the actual group that exists.
- **GeoIP MMDB download failure on networks without GitHub access** — Mihomo attempts to download `geoip.metadb`, `geosite.dat`, and `country.mmdb` from GitHub on first start. On networks where GitHub is unreachable, Mihomo deletes any partially downloaded file and enters a crash loop. Geo data files are now bundled in `docker-images/` and copied to `/opt/pimiproxy/config/` at install time. `geo-auto-update: false` is now set in all generated configs so Mihomo never attempts to download or update these files at runtime.
- **(XD) dashboard link pointed to the wrong port** — the link in the management UI sidebar was defined as `href={dashUrl}`, but `dashUrl` was only populated by the initial auth check, which runs before login and fails with 401. After logging in, `dashUrl` remained empty and the link fell back to `#`, keeping the user on the UI port. A second `useEffect` with an `[authed]` dependency now fetches `/api/status` immediately after login and sets `dashUrl` correctly.

### Added
- **Check URL tool** — new tab in the management UI. Enter a URL to test whether it is reachable through the proxy. Shows HTTP status, latency, and — when available — the live Mihomo routing decision: which rule matched, rule type and value, and the full proxy chain the connection used. Routing data is retrieved by polling Mihomo's `/connections` API while the probe is in-flight.
- **Geodata files bundled with installer** — `geoip.metadb`, `geosite.dat`, and `country.mmdb` placed in `docker-images/` are copied to the Mihomo config directory at install time. If files are already present they are not overwritten.

---

## [5.0.0] — 2026-04-14

### Added
- **Web management UI** — two new Docker containers: `pimiproxy-api` (Django REST API) and `pimiproxy-ui` (React + Nginx). Accessible on port 8585 by default. Login uses the same API secret as the Mihomo dashboard.
- **Two-layer config model** — user-defined proxy groups and rules are now always applied on top of the provider subscription. User rules are prepended first (highest priority); user groups are appended after provider groups. Previously, user groups and rules were silently ignored when a subscription was present.
- **Provider groups and rules visible in UI** — the management UI displays provider subscription groups (read-only) alongside user groups (CRUD). Provider rules are shown as a read-only preview below user rules.
- **Drag-to-reorder rules** — user rules can be reordered in the UI by dragging. No external dependencies; uses native HTML5 drag.
- **Light/dark mode toggle** — UI defaults to dark. Toggle persists in `localStorage`.
- **Offline font bundling** — Space Grotesk and Syne fonts are bundled at build time via `@fontsource` npm packages. No CDN calls required at runtime; works in China and air-gapped environments.
- **`ui_port` in configure** — change the management UI port via configure option 7 (alongside dashboard, API, and proxy ports).
- **`ui-secret.txt`** — Django `SECRET_KEY` generated at install time and written to `/opt/pimiproxy/ui-secret.txt`. Removed by the uninstaller.
- **Image build at install** — installer builds `pimiproxy-api` and `pimiproxy-ui` images from source, or loads them from `docker-images/pimiproxy-api.tar.gz` / `pimiproxy-ui.tar.gz` if present.
- **`docker-images/` directory** — offline `.tar.gz` archives for all five containers (mihomo, metacubexd, zashboard, pimiproxy-api, pimiproxy-ui) go here. Archives are gitignored; directory is tracked via `.gitkeep`.

### Changed
- **"Wannaflix" → "Provider"** — all user-facing references to "Wannaflix" in `build_config()`, the configure menu, and the login banner updated to the generic term "Provider". The internal JSON field `subscription_url` is unchanged for backwards compatibility.
- **Subscription prompt renamed** — "Clash API subscription URL" → "Provider URL" throughout the install wizard and configure menu.
- **`build_banner()`** — UI URL line (`http://<hostname>.lan:<ui_port>`) added to the login banner.
- **`do_status()`** — UI port shown in endpoint list; provider terminology updated.
- **`do_logs()`** — now accepts `pimiproxy-api` and `pimiproxy-ui` as service names.
- **Repo structure** — moved docs to `docs/`, moved offline archives to `docker-images/`, added `pimiproxy-api/` and `pimiproxy-ui/` directories.

---

## [4.7.0] — 2026-04-12

### Added
- **"Quest" proxy group** — always injected into `config.yaml` regardless of subscription. Type `select`, default `DIRECT`. Covers Meta/Oculus/Quest OS domains and streaming fitness platforms (Les Mills, Brightcove). Routing these direct bypasses the upstream proxy server so geo-licensed content (Les Mills Bodycombat VR, etc.) sees the Pi's real home IP instead of the proxy's exit IP. Switchable to any proxy via the Mihomo dashboard or `configure`. Quest domain rules are prepended before the subscription catch-all so they are evaluated first.

---

## [4.6.0] — 2026-04-12

### Fixed
- **Gateway mode (Quest 3 / forwarded clients)** — devices that use the Pi as their network gateway now get their traffic routed correctly. Three changes: `auto-route: true` so Mihomo registers kernel routing rules for forwarded packets; `auto-detect-interface: true` so Mihomo picks the correct WAN interface automatically; kernel IP forwarding enabled at install time (`net.ipv4.ip_forward=1`, persisted via `/etc/sysctl.d/99-pimiproxy.conf`). Without forwarding enabled the Pi was silently dropping packets from gateway clients.
- **`clash-system` rewritten for `auto-route: true`** — the old script used manual ip rules and routing table 101 (designed for `auto-route: false`). This conflicted with Mihomo's own auto-route rules. New design: `off` adds an `iptables mangle OUTPUT` rule that marks Pi-originated packets with Mihomo's routing mark (666), which Mihomo's ip rule exempts from the TUN. Forwarded packets (Quest 3 etc.) travel through the FORWARD chain and are never marked, so they always reach Mihomo. `on` removes the bypass mark — Pi traffic also routes through the proxy.

### Added
- **IP forwarding** — installer writes `/etc/sysctl.d/99-pimiproxy.conf` and applies `net.ipv4.ip_forward=1` immediately. Uninstaller removes the file and resets the value.
- **`pimiproxy-bypass.service`** — systemd unit installed and enabled at install time. Runs `clash-system off` after network is up on every boot, ensuring Pi traffic goes direct by default. Removed by the uninstaller (which also clears any leftover iptables rules).

### Changed
- **TUN auto-route enabled by default** — `auto-route: true` and `auto-detect-interface: true` in generated `config.yaml`. Previously both were false — worked for clients using port 7890 but broke gateway-mode clients.
- **DNS nameservers changed** — primary `8.8.8.8` / `1.1.1.1`, fallback `8.8.4.4` / `1.0.0.1`. Previous defaults (`223.5.5.5` / `119.29.29.29`) were Chinese DNS services and gave incorrect geo-routing for most users.
- **Fake-ip filter expanded** — added connectivity check domains for Android/Apple/Windows, NTP servers (`time.android.com`, `pool.ntp.org`), STUN (`stun.l.google.com` for WebRTC-based Quest apps), Instagram/CDN domains, `+.googlevideo.com`, and Les Mills/Brightcove domains (`+.lesmills.com`, `+.lesmillsplus.com`, `+.lesmillsondemand.com`, `+.brightcove.com`, `+.brightcove.net`, `+.bcovlive.io`, `+.boltdns.net`) for Widevine DRM and geo-routed video delivery.

---

## [4.5.0] — 2026-04-12

### Added
- **Login banner** — `/etc/profile.d/pimiproxy-banner.sh` installed at install time. Displays on every interactive shell login: proxy engine state (mihomo container running/stopped), system proxy state (clash-system ON/OFF), proxy count loaded from config, AES hardware acceleration state, dashboard/API/proxy endpoint URLs, and a quick-command reference. Removed by the uninstaller. Rebuilt automatically when ports change via `configure` option 9.
- **AES hardware acceleration check** — `has_aes_hardware()` reads `/proc/cpuinfo` flags/Features for the `aes` capability flag. Works on ARM and x86_64. Result shown at end of install, in `status` output, and baked into the login banner. Warns the user when AES is unavailable that encryption runs in software and latency will be higher on heavy traffic.

---

## [4.4.0] — 2026-04-12

### Added
- **`debug [url]` subcommand** — fetches a subscription URL with the Clash-Verge User-Agent and reports status code, content type, response size, and parsed proxy/group/rule counts. Saves the full response to `raw_debug_output.yaml`. Replaces the standalone `getapiconfig.py` utility. URL can be passed as an argument for non-interactive use.

### Changed
- **`getapiconfig.py` superseded** — its functionality is now built into `pimiproxy-core.py debug`. The standalone script is no longer needed.

---

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
- Bash script (`pimiproxy`) retired
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