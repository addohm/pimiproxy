# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Environment: dev machine vs SBC

`/srv/aidev/pimiproxy/` is the **development source directory only**. The SBC (`pimiproxy.lan`) is a separate physical device. Never run deploy, copy, docker, or python commands targeting the SBC unless explicitly instructed.

**Remote access is limited to two operations:**
1. `rsync` — sync source to the SBC
2. `ssh addohm@pimiproxy.lan "docker logs ..."` — read container logs for debugging

SSH and rsync use the `addohm` user. Root SSH is not permitted.

**The SBC cannot build Docker images.** It has no access to docker.io or any registry. All images must be built here on the dev machine, saved as `.tar.gz`, rsynced to the SBC, and loaded there by the user.

---

## Deploy workflow

### 1. Build a Docker image (on dev machine)

Images must be built for `linux/arm64` (the SBC is `aarch64`):

```bash
# API container
docker buildx build --platform linux/arm64 -t pimiproxy-api:latest ./pimiproxy-api/ --load
docker save pimiproxy-api:latest | gzip > docker-images/pimiproxy-api.tar.gz

# UI container
docker buildx build --platform linux/arm64 -t pimiproxy-ui:latest ./pimiproxy-ui/ --load
docker save pimiproxy-ui:latest | gzip > docker-images/pimiproxy-ui.tar.gz
```

### 2. Sync to the SBC

```bash
rsync -avzc --exclude .git/ --exclude .gitignore --exclude '__pycache__' --exclude 'node_modules' --del /srv/aidev/pimiproxy addohm@pimiproxy.lan:/home/addohm
```

Note: source has **no trailing slash**; destination is `/home/addohm` (not `/home/addohm/pimiproxy`). rsync creates `/home/addohm/pimiproxy/` on the SBC.

### 3. User loads the image and recreates the container on the SBC

```bash
sudo docker load -i /home/addohm/pimiproxy/docker-images/pimiproxy-api.tar.gz
sudo docker compose -f /opt/pimiproxy/docker-compose.yml up -d --force-recreate pimiproxy-api
```

`docker restart` does **not** apply a newly built image. Always `--force-recreate` after a new build.

---

## Runtime paths (on SBC)

| Path | Purpose |
|------|---------|
| `/opt/pimiproxy/` | Installer runtime directory — root-owned, never edit manually |
| `/opt/pimiproxy/.pimiproxy-config.json` | Live config — read/written by the API container |
| `/opt/pimiproxy/.pimiproxy-meta.json` | Install metadata (ports, install_dir, dashboard type) |
| `/opt/pimiproxy/config/config.yaml` | Generated Mihomo config — rebuilt by config_builder on every write |
| `/home/addohm/pimiproxy/` | Synced source — used as build context for `docker build` |
| `/opt/pimiproxy/docker-compose.yml` | Runtime compose file — uses pre-built images, no `build:` directive |

The API container mounts `/opt/pimiproxy` → `/pimiproxy` (read/write) and `/var/run/docker.sock` to restart Mihomo.

---

## Architecture

### `pimiproxy-core.py`
Single-file Python installer and management tool. Run as `sudo python3 pimiproxy-core.py <subcommand>`. Key functions:

- `build_config(cfg)` — generates `config.yaml` from the config dict. **Must be kept in sync with `pimiproxy-api/api/config_builder.py`.**
- `do_install()` — interactive installer; writes `.pimiproxy-config.json`, `.pimiproxy-meta.json`, `docker-compose.yml`, cron job, and loads/starts all containers
- `do_refresh()` — fetches provider URL, extracts proxies/groups/rules YAML blocks, writes to config, rebuilds and restarts Mihomo
- `do_refresh_cron()` — called by cron; checks `auto_refresh_enabled` + `refresh_interval_days` + `.last_refresh` stamp before calling `do_refresh()`
- `COMMANDS` dict maps subcommand strings to handler functions

### Two-layer config model
Provider subscription (base) + user config (always on top):
- **Groups:** subscription groups verbatim → user `proxy_groups` appended after
- **Rules:** user `rules` first (highest priority) → subscription rules → MATCH fallback

The `🧭 Meta Quest` group is a hardcoded system group injected by the config builder after subscription groups. Its proxy list is built dynamically: `DIRECT` + first subscription group name + all individual proxy names.

### `pimiproxy-api/` (Django REST API container)
- All logic in `api/views.py` — no models, no ORM beyond sessions
- Auth: Django session middleware; `api/middleware.py` enforces auth on all `/api/*` routes
- Every write operation calls `_rebuild_and_restart(cfg)` which writes `config.yaml` via `config_builder.build_config()` then runs `docker restart mihomo`
- `config_builder.py` is a **self-contained copy** of the config-building logic from `pimiproxy-core.py`. Keep them in sync.
- `yaml_quote(s)` must be applied to **all** proxy names written as YAML list items — names can contain `[`, `:`, and other YAML-special characters

### `pimiproxy-ui/` (React + Nginx frontend container)
- Vite + React SPA; `nginx.conf` proxies `/api/*` to `pimiproxy-api:8000` and serves the SPA with `try_files` fallback
- `src/api.js` — all fetch calls; base URL is relative (`/api/`) so nginx handles routing
- Auth state lives in `App.jsx`; `onUnauthorized` prop is passed to every page component and calls `setAuthed(false)` on 401
- Design tokens in `src/styles/variables.css`; fonts are npm-bundled (`@fontsource/space-grotesk`, `@fontsource/syne`) — no CDN calls

### `docker-images/`
Optional `.tar.gz` archives for offline installation. Gitignored. Built on the dev machine and rsynced to the SBC.

---

## Config JSON keys (`.pimiproxy-config.json`)

| Key | Description |
|-----|-------------|
| `api_port` | Mihomo external controller port (default 9090) |
| `api_secret` | Auth secret for both Mihomo API and the pimiproxy UI login |
| `mixed_port` | Mihomo mixed HTTP/SOCKS5 port (default 7890) |
| `subscription_url` | Provider subscription URL |
| `subscription_proxies_yaml` | Raw YAML block cached from last refresh |
| `subscription_groups_yaml` | Raw YAML block cached from last refresh |
| `subscription_rules_yaml` | Raw YAML block cached from last refresh |
| `proxy_groups` | User-defined proxy groups (list of dicts) |
| `rules` | User-defined rules (list of strings: `"TYPE,value,target"`) |
| `auto_refresh_enabled` | Whether the cron job should refresh (bool, default true) |
| `refresh_interval_days` | How many days between auto-refreshes (int, default 1) |
