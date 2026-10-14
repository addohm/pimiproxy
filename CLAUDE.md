# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Environment: dev machine vs target devices

`/srv/aidev/pimiproxy/` is the **development source directory only**. Every `pimiproxy` host is a
separate physical device or VM. Never run deploy, copy, docker, or python commands targeting a
device unless explicitly instructed.

Known targets (`PROMPT.md`): `pimiproxy.lan` (the SBC) plus test VMs at `172.16.9.91` /
`.92` / `.93`. Scripts default to `$USER@pimiproxy.lan`; override with `--target user@host` or the
`PIMIPROXY_TARGET` / `PIMIPROXY_USER` env vars.

**Remote access is limited to:**
1. `rsync` — sync source to a device
2. `ssh $USER@<host> "sudo docker logs <container>"` — read container logs for debugging
3. `ssh $USER@<host> "cat <file>"` — read files on the device (e.g. `config.yaml`)

SSH and rsync use the dev user (`$USER`). Root SSH is not permitted.

**Targets cannot build Docker images.** They have no access to docker.io, ghcr.io, or any registry
(the primary user base is in mainland China). All images are built here on the dev machine, saved
as `.tar.gz`, rsynced, and loaded there by the user.

---

## Two variants — keep them in sync

| | Full (`pimiproxy`) | Ultralite (`pimiproxy-ultralite`) |
|---|---|---|
| Engine | mihomo in Docker | mihomo binary as a systemd unit |
| Extras | metacubexd/zashboard dashboard, `pimiproxy-api`, `pimiproxy-ui` | none |
| Install dir | `/opt/pimiproxy` | `/opt/pimiproxy-lite` |
| Requires Docker | yes | no |
| Arches shipped | amd64, arm64, armv7 | amd64, arm64, armv7, **386** |

Ultralite ships 386 because it only needs the mihomo binary. The full variant cannot: `node:20-alpine`
(used by `pimiproxy-ui`'s build stage) and metacubexd/zashboard have no 386 images, and metacubexd
has no armv7 image either — so the armv7 full archive ships zashboard only.

**Any change to `pimiproxy-core.py` or `pimiproxy` must be mirrored in
`pimiproxy-ultralite-core.py` / `pimiproxy-ultralite`** unless it is genuinely Docker-specific.

---

## Deploy workflow

### Build scripts (`./scripts/`, run in order)

```bash
./scripts/1_update-upstream.sh [--bins|--images|--all|--dry-run]   # fetch mihomo bins + upstream images
./scripts/2_rebuild.sh [flags]                                    # build pimiproxy-api + pimiproxy-ui
./scripts/3_build-releases.sh [--full|--ultralite|--all]          # package per-arch release archives
```

`1_update-upstream.sh` writes `bin/mihomo-linux-<arch>-<version>.gz` (amd64, 386, arm64, armv7) and
`docker-images/{mihomo,metacubexd,zashboard}.tar.gz`.

`3_build-releases.sh` writes `releases/pimiproxy-<arch>.tar.gz` and
`releases/pimiproxy-ultralite-<arch>.tar.gz`. An arch with a missing image or binary is skipped
silently. Release archives must include `pimiproxy_common.py` alongside the wrapper + core script.

**Versioning:** the root `VERSION` file is the single source of truth. `2_rebuild.sh` stamps it
into the api/ui images (`io.pimiproxy.version` label); `3_build-releases.sh` ships it in every
archive and also emits `releases/updates/` (gitignored): per-arch `pimiproxy-{api,ui}-<arch>.tar.gz`
bundles + `manifest.json` — the layout the device-side `update_url` config key expects on a static
host. `.pimiproxy-meta.json` records `version` at install; `update` compares against it and won't
downgrade. Bump `VERSION` before rebuilding images for a release.

### `2_rebuild.sh` — **builds only by default; it does not sync**

```bash
./scripts/2_rebuild.sh                       # build both pimiproxy images, save tarballs. No sync.
./scripts/2_rebuild.sh --api                 # build only pimiproxy-api
./scripts/2_rebuild.sh --ui                  # build only pimiproxy-ui
./scripts/2_rebuild.sh --third-party         # pull mihomo/metacubexd/zashboard for all arches
./scripts/2_rebuild.sh --sync                # build, then rsync the source tree to the target
./scripts/2_rebuild.sh --sync-only           # rsync only, no build
./scripts/2_rebuild.sh --target user@host …  # override the destination
```

Run these **on the target device**, not here:

```bash
./scripts/2_rebuild.sh --deploy         # load both + force-recreate both containers
./scripts/2_rebuild.sh --deploy-api     # load + recreate pimiproxy-api only
./scripts/2_rebuild.sh --deploy-ui      # load + recreate pimiproxy-ui only
```

If `2_rebuild.sh` prints `arm64 build failed — skipping`, the QEMU binfmt handlers are not
registered on this host (they do not survive some kernel upgrades). The script hides the real
error; re-register and re-run:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64,arm
```

### Multi-arch tarball format

Images are built for `linux/amd64`, `linux/arm64`, and `linux/arm/v7` and tagged `<image>:<arch>`.
**One `.tar.gz` holds every successfully-built arch**; `3_build-releases.sh` splits them back into
per-arch release archives, and the installer picks the tag matching the host. Manual equivalent:

```bash
docker buildx build --platform linux/arm64 -t pimiproxy-api:arm64 ./pimiproxy-api/ --load
docker save pimiproxy-api:arm64 pimiproxy-api:amd64 | gzip > docker-images/pimiproxy-api.tar.gz
```

### Sync to a device

```bash
rsync -avzc --exclude .git/ --exclude .gitignore --exclude '__pycache__' --exclude 'node_modules' --del /srv/aidev/pimiproxy $USER@pimiproxy.lan:/home/$USER
```

Source has **no trailing slash**; destination is `/home/$USER` (not `/home/$USER/pimiproxy`). rsync
creates `/home/$USER/pimiproxy/` on the device.

### Load and recreate on the device

```bash
sudo docker load -i /home/$USER/pimiproxy/docker-images/pimiproxy-api.tar.gz
sudo docker compose -f /opt/pimiproxy/docker-compose.yml up -d --force-recreate pimiproxy-api
```

`docker restart` does **not** apply a newly built image. Always `--force-recreate` after a new build.

---

## Runtime paths

### Full variant

| Path | Purpose |
|------|---------|
| `/opt/pimiproxy/` | Installer runtime directory — root-owned, never edit manually |
| `/opt/pimiproxy/.pimiproxy-config.json` | Live config — read/written by the API container |
| `/opt/pimiproxy/.pimiproxy-meta.json` | Install metadata (install_dir, hostname, ports, compose bin) |
| `/opt/pimiproxy/config/config.yaml` | Generated Mihomo config — rebuilt by config_builder on every write |
| `/opt/pimiproxy/docker-compose.yml` | Runtime compose file — pre-built images, no `build:` directive |
| `/opt/pimiproxy/ui-secret.txt` | Django `SECRET_KEY` (mounted as `/pimiproxy/ui-secret.txt`) |
| `/usr/local/bin/pimiproxy` | Symlink → `/opt/pimiproxy/pimiproxy` |
| `/usr/local/bin/clash-system` | Host-traffic bypass toggle |
| `/home/$USER/pimiproxy/` | Synced source — build context for `docker build` |

The API container mounts `/opt/pimiproxy` → `/pimiproxy` (read/write) and `/var/run/docker.sock` to
restart Mihomo.

### Ultralite variant

| Path | Purpose |
|------|---------|
| `/opt/pimiproxy-lite/` | Install directory |
| `/opt/pimiproxy-lite/.pimiproxy-meta.json` | Install metadata |
| `/opt/pimiproxy-lite/config/config.yaml` | Generated Mihomo config |
| `/opt/pimiproxy-lite/bin/mihomo-<arch>` | Extracted mihomo binary |
| `/etc/systemd/system/mihomo.service` | Engine unit (built by `build_mihomo_service()`) |
| `/etc/systemd/system/pimiproxy-bypass.service` | Optional — applies `clash-system off` after boot |
| `/usr/local/bin/pimiproxy-ultralite` | Symlink → `/opt/pimiproxy-lite/pimiproxy-ultralite` |

---

## Architecture

### Entry points: `pimiproxy` / `pimiproxy-ultralite` (bash wrappers)

**These are the supported entry points — always reference them in user-facing text and docs, never
`python3 pimiproxy-core.py`.** The wrappers detect the distro, install prerequisites (Python 3,
cron, Docker, avahi, …), resolve symlinked install paths, then exec the core script with
`PIMIPROXY_VIA_WRAPPER=1`. Both cores print a warning when that env var is absent.

```bash
sudo ./pimiproxy <subcommand>          # or, post-install, `sudo pimiproxy <subcommand>`
sudo ./pimiproxy-ultralite <subcommand>
```

### `pimiproxy_common.py` — shared helpers

Variant-agnostic code imported by both cores: colour class `C`, `info`/`step`/`warn`/`die`,
`ask`/`ask_secret`/`confirm`/`choose`, `Spinner`, `run`/`run_ok`, `yaml_quote`,
`current_hostname`/`set_hostname`/`dhcp_renew`, `extract_yaml_blocks`, `fetch_subscription`,
`save_subscription`, `detect_distro`, `pkg_update`/`pkg_install`/`pkg_remove`, `has_aes_hardware`.

Deliberately **not** shared (stays per-variant): `build_config`, `build_banner`, `do_install`,
`do_uninstall`, and all Docker-vs-systemd logic. Must be importable from the same directory as each
core script, so installers and `3_build-releases.sh` copy it alongside the wrapper + core.

### The three-way sync rule

`build_config()` exists in **three** places and all three must change together:

- `pimiproxy-core.py:300`
- `pimiproxy-ultralite-core.py:70`
- `pimiproxy-api/api/config_builder.py:16` (a self-contained copy — the API container does not
  import the core scripts)

`yaml_quote()` must be applied to **every** proxy name written as a YAML list item — names can
contain `[`, `:`, and other YAML-special characters.

### Subcommands (`COMMANDS` dict)

Shared: `install`, `uninstall`, `configure`, `refresh`, `refresh-cron`, `status`, `logs`, `restart`,
`hostname`, `test`, `debug`, `export`, `standard-repos`, `china-repos`, `system`
(`system [on|off|status]` for the runtime host-traffic toggle, `system boot [on|off|status]`
for the persistent one — it reads `sys.argv[3]`).
`version` (`--version`/`-v`) is handled by the **wrapper** before the root check — it is not in
`COMMANDS` and is the one subcommand that works without `sudo`.

Full only: `update`. Ultralite only: `rule-list`, `rule-add`, `rule-remove`.

`_TAKES_ARG` lists the subcommands that accept a second positional argument — add to it when a new
subcommand takes one. Key handlers:

- `do_install()` — interactive installer; writes `.pimiproxy-config.json`, `.pimiproxy-meta.json`,
  `docker-compose.yml` (or the systemd unit), the cron job, the login banner, `clash-system`, and
  loads/starts everything
- `do_refresh()` — fetches the provider URL, extracts proxies/groups/rules YAML blocks, writes to
  config, rebuilds and restarts Mihomo
- `do_refresh_cron()` — checks `auto_refresh_enabled` + `refresh_interval_days` + `.last_refresh`
  before calling `do_refresh()`

### Two-layer config model

Provider subscription (base) + user config (always on top):
- **Groups:** subscription groups verbatim → user `proxy_groups` appended after
- **Rules:** user `rules` first (highest priority) → subscription rules → `MATCH` fallback

**No `🧭 Meta Quest` group or rules are injected** — that hardcoded injection was removed (moved to
the provider/subscription config). The `fake-ip-filter` DNS allow-list for Meta/Oculus/Facebook/
connectivity domains **stays** (it governs DNS resolution so the Quest reports online, not routing).
A config with no provider and no user groups omits `proxy-groups:` entirely.

**Provider DNS lifting:** refresh also parses the subscription's `dns` block (nothing from it is
emitted verbatim) and extracts `fake-ip-filter` + `nameserver-policy` into
`subscription_fake_ip_filter` / `subscription_nameserver_policy`. `build_config` merges them:
fake-ip-filter = local baseline → user extras → provider entries (deduplicated); nameserver-policy
= local captive-portal defaults, provider overriding on collision. The rest of the generated `dns`
section (nameserver ordering, tunneled DoH fallback, dns-hijack) is load-bearing and always local.

### Remediation-branch reliability layer (`remediation`)
`build_config` is now the single copy in `pimiproxy_common.py` (core, ultralite, and the API all
use it; the API loads the copy mounted at `/pimiproxy/pimiproxy_common.py`). Other invariants:
- **Config I/O is locked + atomic** — every writer (CLI, cron, API bind-mount) goes through
  `config_lock()` / `write_config()` / `update_config()`. Never do a bare `write_text(json.dumps())`.
- **Validate before restart** — every write runs `mihomo -t` (`install_config()`) before persisting;
  the API returns 422 (rejected) / 502 (engine didn't come up) with Mihomo's reason.
- **Health = controller, not TUN** — `engine_health()` checks the process AND the controller answers;
  `status` exits non-zero when unhealthy and flags a foreign mihomo on the port.
- **Install preflight** — `preflight_conflicts()` refuses to install over an existing mihomo
  unit/binary/TUN/port.
- **Offline Docker** — the wrapper installs Docker from a bundled static engine
  (`docker-engine/docker-<arch>.tgz`) before any network path; `1_update-upstream.sh --docker`
  fetches it.

Appended groups use an indent **detected from the provider's own YAML** rather than a hardcoded two
spaces — provider blocks are emitted verbatim, so a provider using four-space indentation would
otherwise nest the appended groups inside the previous group's `proxies:` list.

### `pimiproxy-api/` (Django REST API container)

- All logic in `api/views.py` — no models, no ORM beyond sessions. Routes in `api/urls.py`.
- Every write calls `_rebuild_and_restart(cfg)`: writes `config.yaml` via
  `config_builder.build_config()`, then `docker restart mihomo`.
- Auth/CSRF stack (`pimiproxy_api/settings.py`), middleware order matters:
  `SecurityMiddleware` → `SessionMiddleware` → `CommonMiddleware` →
  `api.middleware.OriginCheckMiddleware` → `api.middleware.AuthRequiredMiddleware`
  - `AuthRequiredMiddleware` — 401s any `/api/*` request without `session["authenticated"]`;
    `/api/login` is the only public path
  - `OriginCheckMiddleware` — for mutating `/api/*` requests, `Origin`/`Referer` host must equal
    `request.get_host()`. Requests with **neither** header pass (CLI tools).
  - `SESSION_COOKIE_SAMESITE = "Strict"`, `SESSION_COOKIE_HTTPONLY = True`
  - Login throttling in `views.py` — 5 failures per IP per 60s → 5-minute lockout (429). Per-worker
    state; the container runs 2 gunicorn workers.
  - `SECRET_KEY` is read from `/pimiproxy/ui-secret.txt`, auto-generated at `0600` if missing.
    **Never add a hardcoded fallback** — for either this or `api_secret`.
  - `ALLOWED_HOSTS` defaults to `*` (LAN service reached by IP, `.lan`, and container name);
    tighten via the `PIMIPROXY_ALLOWED_HOSTS` env var.

### `pimiproxy-ui/` (React + Nginx frontend container)

- Vite + React SPA. Dev: `cd pimiproxy-ui && npm install && npm run dev`; production build is
  `npm run build` inside the `node:20-alpine` stage of `Dockerfile`, served by `nginx:alpine`.
- `nginx.conf` proxies `/api/*` to `pimiproxy-api:8000` and serves the SPA via `try_files`. It must
  pass **`Host $http_host`** (host *and* port), not `$host` — otherwise `request.get_host()` drops
  the port and `OriginCheckMiddleware` 403s every write.
- `src/api.js` — all fetch calls; base URL is relative (`/api/`) so nginx handles routing.
- Auth state lives in `App.jsx`; `onUnauthorized` is passed to every page component and calls
  `setAuthed(false)` on 401.
- Design tokens in `src/styles/variables.css`; fonts are npm-bundled
  (`@fontsource/space-grotesk`, `@fontsource/syne`) — no CDN calls.

### `clash-system` — host-traffic toggle

All traffic normally routes through Mihomo's TUN. `sudo clash-system off` (or
`sudo pimiproxy system off`) makes the **host's own** traffic go direct via fwmark `888` + an ip rule
at priority 5000 + POSTROUTING MASQUERADE. Only the `OUTPUT` chain is mangled, never `FORWARD`, so
**gateway clients stay proxied regardless of the toggle**. `pimiproxy-bypass.service` persists the
`off` state across reboots. The advanced-install prompt sets its initial value, but it is a live
setting afterwards: `pimiproxy system boot [on|off|status]` or `configure` → "Host traffic on boot"
install/remove the unit via `apply_boot_bypass()` in `pimiproxy_common.py`, with each core supplying
its own unit text from `build_bypass_service()` (full orders on `docker.service`, ultralite on
`mihomo.service`). State is read back from systemd (`boot_bypass_enabled()`), never stored in config,
so a manual `systemctl disable` can't leave a stale value. Both toggles apply immediately as well as
at boot, so runtime and boot state never disagree.

### Binary assets

`docker-images/`, `bin/`, and `releases/` are **tracked via Git LFS** (see `.gitattributes`), not
gitignored. `CLAUDE.md`, `.claude/`, `*.yaml`, `pimiproxy-ui/node_modules|dist`, and the
`docs/pmp-*-plan.md` working docs are gitignored.

---

## Config JSON keys (`.pimiproxy-config.json`)

| Key | Description | Ultralite |
|-----|-------------|:---:|
| `install_dir` | Install root (`/opt/pimiproxy`) | via meta |
| `api_port` | Mihomo external controller port (default 9090) | ✅ |
| `api_secret` | Auth secret for both Mihomo API and the pimiproxy UI login | ✅ |
| `mixed_port` | Mihomo mixed HTTP/SOCKS5 port (default 7890) | ✅ |
| `ui_port` | Management UI port (default 8585) | — |
| `dashboard_port` | Dashboard port (default 8080) | — |
| `dash_name` | Dashboard container name | — |
| `engine_image` / `dashboard_image` | Docker image tags written into `docker-compose.yml` | — |
| `subscription_url` | Provider subscription URL | ✅ |
| `subscription_proxies_yaml` | Raw YAML block cached from last refresh | ✅ |
| `subscription_groups_yaml` | Raw YAML block cached from last refresh | ✅ |
| `subscription_rules_yaml` | Raw YAML block cached from last refresh | ✅ |
| `subscription_fake_ip_filter` | fake-ip-filter patterns lifted from the provider's `dns` block at refresh (list); merged after the local baseline, deduplicated | ✅ |
| `subscription_nameserver_policy` | nameserver-policy entries lifted from the provider's `dns` block (list of `[pattern, [servers]]`); provider wins over local defaults on pattern collision | ✅ |
| `proxy_groups` | User-defined proxy groups (list of dicts) | ✅ |
| `proxy_providers` | User-defined proxy providers (dict) | — |
| `rules` | User-defined rules (list of `"TYPE,value,target"` strings) | ✅ |
| `auto_refresh_enabled` | Whether the cron job should refresh (bool, default true) | ✅ |
| `refresh_interval_days` | Days between auto-refreshes (int, default 1) | ✅ |
| `update_url` | Optional base URL of a static host serving `manifest.json` + per-arch api/ui update bundles (from `releases/updates/`). Unset by default — no channel is baked in. | — |

`.pimiproxy-meta.json` is separate and holds install-time facts: `install_dir`, `hostname`,
`dashboard_port`, `api_port`, `mixed_port`, `compose_bin`, `compose_sub`.

---

## Testing & verification

**There is no automated test suite.** Verification is manual:

```bash
sudo ./pimiproxy status              # container health, TUN state, live proxy connectivity test
sudo ./pimiproxy test                # test every proxy group
sudo ./pimiproxy test --url https://www.google.com
sudo ./pimiproxy logs [mihomo|dashboard|pimiproxy-api|pimiproxy-ui]
sudo ./pimiproxy export [dir]        # dump user config + provider YAML + final config.yaml
```

From the dev machine, read logs remotely: `ssh $USER@pimiproxy.lan "sudo docker logs pimiproxy-api"`.

Before sharing a release, extract and install the built tarball on a test VM. Full coverage means
Ubuntu, Fedora, and Arch VMs plus the SBC, using the advanced install path and exercising both Y/N
for the bypass-on-boot prompt.

---

## Conventions

- **Commit after each completed task**, with a detailed message — do not batch unrelated changes.
- **Bump `VERSION` and add a `docs/CHANGELOG.md` entry for every change — always, no exceptions.**
  Patch for fixes, minor for new features, major for breaking changes. Changes must never sit
  unversioned: the version is what a user reports (`pimiproxy version`) and what `update` compares,
  so an unbumped change is indistinguishable from the release before it. Add the narrative line to
  `docs/VERSION_HISTORY.md` when cutting a minor or major.
- **Finish a release by bringing `dev` up to `main`:** `git fetch . main:dev` (no checkout — the
  repo holds multi-GB LFS artifacts, and switching branches rewrites them twice). `dev` is the
  user's local marker for what is current on this machine; it is **local only** and never pushed.
  The command refuses a non-fast-forward, so if `dev` has its own commits, stop and ask.
- **Then reclaim disk: `git lfs prune --verify-remote`.** Each release orphans the previous
  release's LFS objects (~1.5 GB/cycle; `.git` hit 11 GB before the first prune).
  `--verify-remote` checks every object exists on the remote before deleting it locally, so
  pruned objects remain re-fetchable. Release ritual in order: bump `VERSION` → changelog →
  rebuild → commit → push → `git fetch . main:dev` → `git lfs prune --verify-remote`.
- Give shell commands on a **single line** — multi-line commands break when pasted.
- The config is written as **hand-built YAML strings, not PyYAML** — the core scripts stay
  dependency-free so they run on a fresh OS before pip is available.

---

## Reference docs

| Doc | Contents |
|-----|----------|
| `docs/MAINTAINER.md` | Deep maintainer reference — repo layout, variant differences, release matrix, adding a subcommand, per-distro gotchas, China mirrors |
| `docs/DEV_INSTRUCTIONS.md` | Full install walkthrough and prompt-by-prompt explanation |
| `docs/TROUBLESHOOTING.md` | Symptom → fix reference |
| `docs/CHANGELOG.md` | Versioned change history |
| `docs/VERSION_HISTORY.md` | Narrative per-version overview (what each release was about) |
| `docs/IMAGE_VERSIONS.md` | Pinned upstream image/binary versions |
| `README.md` | User-facing overview, arch support matrix, subcommand table |
