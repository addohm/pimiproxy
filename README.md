# pimiproxy

A self-hosted proxy server for your home network, running on a Linux SBC or server. pimiproxy uses [Mihomo](https://github.com/MetaCubeX/mihomo) (the Clash.Meta engine) inside Docker, with a web dashboard for monitoring and a browser-based management UI for configuring proxy groups and rules.

Once installed, any device on your network can route its traffic through the SBC by pointing its proxy settings at `<pi-ip>:7890`. No VPN client, no app — just a standard HTTP/SOCKS5 proxy.

> **This installer is designed for fresh OS installs.** It modifies system settings including the hostname, DNS configuration, firewall rules, IP forwarding, and package repositories (when China mirrors are selected). Do not run it on a machine that is already configured for other purposes.

---

## What it does

- Runs Mihomo (Clash.Meta engine) in a Docker container on the SBC
- Exposes a mixed HTTP/SOCKS5 proxy port for other devices to use
- Provides a web dashboard (Metacubexd or Zashboard) for live traffic monitoring
- Provides a browser-based management UI for adding proxy groups, rules, and managing your provider URL
- Supports a two-layer config: your provider's subscription (base) + your own groups and rules (always applied on top). Group order is provider→user; rule order is user→provider→MATCH
- Routes all traffic through Mihomo via TUN — both the host's own traffic and forwarded traffic from devices using it as their DHCP gateway (e.g. Meta Quest)
- Includes an optional `clash-system on|off` toggle for advanced users to bypass Mihomo for host-originated traffic (gateway clients stay proxied)
- Sets up mDNS so the SBC is reachable as `pimiproxy.local` on the LAN, and as `pimiproxy.lan` on routers that use `.lan` as the local domain
- Shows a status banner on every SSH/terminal login — engine state, host-traffic state, proxy count, endpoints, and quick-command reference
- Validates every config change with Mihomo's own test mode before applying it, and reports real engine health (the controller answering, not just a TUN device existing)

## What it does not do

- It does not configure any upstream proxy or VPN out of the box — you bring your own provider and add it to the config
- It does not touch your router — other devices must be configured manually to use the proxy
- It does not intercept LAN traffic automatically — devices opt in via their proxy settings

---

## Requirements

- Any SBC or Linux host (Raspberry Pi, Orange Pi, Radxa, x86_64, etc.) with a 64-bit OS
- One of the supported distro families:
  - Debian / Raspberry Pi OS / Ubuntu / Mint / Pop
  - Fedora / RHEL / CentOS / Rocky / AlmaLinux
  - Arch / Manjaro / EndeavourOS
  - openSUSE
  - Alpine
- Internet access is optional: the full release archive bundles the container images **and** a
  static Docker Engine, so it can install on a fully-offline / air-gapped box (the ultralite variant
  needs no Docker at all)
- Root / sudo access

### Platform compatibility

Pre-built release archives are produced for both the full (Docker) and
ultralite (native binary) variants, and attached to each release on this
repository's **GitHub Releases** page. Availability depends on whether
upstream Docker images publish the target platform — ultralite has
broader arch coverage because it ships a single self-contained binary.

| Architecture | Full variant | Ultralite variant | Example hardware |
|---|---|---|---|
| `amd64` (x86_64) | ✅ Available | ✅ Available | Intel/AMD PCs, most VMs, x86 mini-PCs |
| `arm64` (aarch64) | ✅ Available | ✅ Available | Raspberry Pi 4/5, modern 64-bit SBCs |
| `armv7` | ✅ Available *(zashboard only)* | ✅ Available | Raspberry Pi 3, older 32-bit ARM SBCs |
| `386` (x86 32-bit) | ❌ Not available | ✅ Available | Legacy x86 PCs |

`armv7` full archive ships only the Zashboard dashboard because upstream
Metacubexd doesn't publish an armv7 image. `386` has no full archive
because `node:20-alpine` (used by `pimiproxy-ui`'s build) has no 386
variant — use the ultralite archive on 32-bit x86.

### Tested distros

| Distro | Full | Ultralite |
|---|---|---|
| Raspberry Pi OS (arm64 / Debian) | ✅ Working | ✅ Working |
| Xubuntu / Ubuntu 25.10 (x86_64) | ✅ Working | ✅ Working |
| Fedora 43 Xfce (x86_64) | ✅ Working | ✅ Working |
| Arch Linux (x86_64) | ✅ Working | ✅ Working |
| openSUSE | ⬜ Untested | ⬜ Untested |
| Alpine | ⬜ Untested | ⬜ Untested |

### Package options

Each variant ships in **two flavors** per architecture:

- **`-offline`** — fully self-contained, installs with **no network**: bundles the container images
  (or the mihomo binary, for ultralite), a static Docker Engine (full only), and geodata. Large.
- **(no suffix) = online** — small; ships source + geodata and fetches the heavy parts at install
  time (full: builds `pimiproxy-api`/`pimiproxy-ui` from source, pulls mihomo/dashboard, installs
  Docker from the network; ultralite: downloads the mihomo binary from GitHub).

| Base name | Offline archive | Online archive | Arches |
|---|---|---|---|
| Full (Docker) | `pimiproxy-<arch>-offline.tar.gz` | `pimiproxy-<arch>.tar.gz` | amd64, arm64, armv7 |
| Ultralite (no Docker) | `pimiproxy-ultralite-<arch>-offline.tar.gz` | `pimiproxy-ultralite-<arch>.tar.gz` | amd64, arm64, armv7, 386 |

Geodata is bundled in **both** flavors (small, and its GitHub source is blocked in China). Offline
is the right choice for air-gapped / China boxes; online is fine anywhere with normal internet.
---

## Project files

| File / Directory | Purpose |
|-----------------|---------|
| `pimiproxy` | One-command bootstrap — detects distro, installs all prerequisites, then launches `pimiproxy-core.py` |
| `pimiproxy-core.py` | Main installer and management tool — see subcommands below |
| `pimiproxy-ultralite` | Bootstrap for the ultralite (Docker-free) variant |
| `pimiproxy-ultralite-core.py` | Ultralite installer — runs Mihomo as a native systemd service, no Docker/web UI |
| `pimiproxy-api/` | Django REST API container — manages config, auth, and Mihomo restarts |
| `pimiproxy-ui/` | React + Nginx frontend container — browser-based management UI |
| `docker-images/` | *(optional)* Pre-pulled `.tar.gz` image archives for offline install |
| `bin/` | *(optional)* Mihomo release binaries for offline/ultralite installs |

The `.tar.gz` archives are not committed to the repository — download them from the GitHub Releases page. See [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md) for which one to pick and how to install it, and [docs/MAINTAINER.md](docs/MAINTAINER.md) to build them yourself.

---

## Quick start

**Bootstrap (recommended, handles all prerequisites):**

```bash
sudo ./pimiproxy install
```

This detects your distro, installs essential tools (curl, iproute2, procps), Python, cron, Docker, avahi-daemon, and raspi-config if needed, then launches the installer automatically.

Press Enter through all prompts to accept defaults. See [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md) for the full walkthrough.

After install you can run commands from anywhere using the symlink `/usr/local/bin/pimiproxy` (or `pimiproxy-ultralite`):

```bash
sudo pimiproxy status
sudo pimiproxy test --url https://www.google.com
```

**Installing from mainland China?** The installer will ask whether the device is in China. Answer yes to switch system package repositories (apt/dnf/pacman) and Docker CE to Aliyun mirrors. Use the `china-repos` and `standard-repos` subcommands to switch mirrors after install.

---

## Default settings

| Setting | Default |
|---------|---------|
| Install directory | `/opt/pimiproxy` |
| Hostname | `pimiproxy` (or current hostname if already customised) |
| Dashboard port | `8080` |
| Management UI port | `8585` |
| Mihomo API port | `9090` |
| Mixed proxy port | `7890` |
| API secret | `pimiproxy` |
| Dashboard | Metacubexd |

---

## Accessing the interfaces

After install:

| Interface | Default URL |
|-----------|-------------|
| Management UI | `http://[HOSTNAME].lan:8585` |
| Dashboard (Metacubexd) | `http://[HOSTNAME].lan:8080` |
| Mihomo API | `http://[HOSTNAME].lan:9090` |

If your router doesn't use `.lan`, try mDNS: `http://[HOSTNAME].local:8585`

Alternatively, you can use your devices IP: `http://[DEVICE_IP]:9090`

**Management UI login:** use your API secret (default: `pimiproxy`).

---

## Connecting a device to the proxy

On any device connected to the same network as the SBC, open **proxy settings** (not network/WiFi settings) and set:

| Field | Value |
|-------|-------|
| Proxy type | HTTP or SOCKS5 |
| Host | `192.168.1.100` *(your SBC's IP)* |
| Port | `7890` |
| Username / Password | *(leave blank)* |

The same port accepts both HTTP and SOCKS5 connections.

---

## Subcommands

All CLI management is done through `pimiproxy`:

```bash
sudo ./pimiproxy <subcommand>
```

| Subcommand / Flag | Description |
|---|---|
| `install` | Run the installer |
| `uninstall` | Stop containers, remove images, delete all files |
| `configure` | Interactive menu: provider URL, proxy groups, rules, ports, secret, dashboard |
| `refresh` | Re-fetch provider URL and restart Mihomo |
| `status` | Show container health, provider info, and run a live proxy connectivity test |
| `test [--url URL]` | Test proxy groups for connectivity; use `--url` to test a single URL |
| `logs [service]` | Tail live container logs (default: mihomo; also: dashboard, pimiproxy-api, pimiproxy-ui) |
| `restart [service]` | Restart one container or all |
| `update` | Version-aware update: refreshes mihomo/dashboard from archives or the registry; updates the pimiproxy api/ui pair from a newer release archive, local source, or a configured `update_url` host — never downgrades |
| `system [on\|off\|status]` | This device's own traffic: via proxy (`on`) or direct (`off`). Gateway clients stay proxied either way |
| `system boot [on\|off\|status]` | What host traffic does after every reboot — the install-time prompt, changeable any time |
| `version` | Print the installed version and exit — the only subcommand that does not need `sudo` (also `--version` / `-v`) |
| `doctor` | Run guided read-only diagnostics across the whole pipeline; prints numbered fix steps and exits non-zero when problems are found |
| `hostname <name>` | Change the system hostname (max 24 chars, alphanumeric + hyphens), restart avahi-daemon, and optionally reboot |
| `debug [url]` | Fetch a provider URL with the Clash User-Agent and inspect the response |
| `export [dir]` | Export user config, cached provider YAML, and final config.yaml to a directory |
| `standard-repos` | Restore original system package repositories from backups |
| `china-repos` | Switch system package repositories (apt/dnf/pacman) to Aliyun mirrors |

---

## Status and connectivity test

```bash
sudo ./pimiproxy status
```

Shows:
- Container up/down state
- TUN interface state
- Provider URL and number of proxies loaded
- Live HTTP and HTTPS connectivity test through the proxy, with latency
- Dashboard, UI, API, and proxy endpoint URLs

---

## Provider and config layers

pimiproxy uses a two-layer config model:

- **Provider layer** (base): fetched from your subscription URL at install time and refreshed daily. Contains proxies, proxy groups, and routing rules from your provider.
- **User layer** (always applied on top): your own proxy groups and rules, managed via the web UI or `configure` command. User rules are prepended first (highest priority); user groups are appended after provider groups.

If you provided a provider URL at install time, proxies and groups are available immediately. The subscription is refreshed automatically each night via a cron job. To refresh manually:

```bash
sudo ./pimiproxy refresh
```

---

## Day-to-day management

```bash
# Check status and test the proxy
sudo ./pimiproxy status

# Test proxy groups for connectivity
sudo ./pimiproxy test
sudo ./pimiproxy test --url https://example.com

# View logs
sudo ./pimiproxy logs                  # Mihomo logs
sudo ./pimiproxy logs dashboard        # Dashboard logs
sudo ./pimiproxy logs pimiproxy-api    # API logs
sudo ./pimiproxy logs pimiproxy-ui     # UI logs

# Restart containers
sudo ./pimiproxy restart               # All containers
sudo ./pimiproxy restart mihomo        # Mihomo only

# Update to newer images (shows installed vs available version; skips
# anything that isn't newer than what's running)
sudo ./pimiproxy update

# Change hostname
sudo ./pimiproxy hostname newname

# Debug a provider URL
sudo ./pimiproxy debug
sudo ./pimiproxy debug https://provider.example.com/api?token=abc123

# Export config for backup or debugging
sudo ./pimiproxy export
sudo ./pimiproxy export /tmp/pimiproxy-export

# Switch system repos to Aliyun mirrors (China) or restore originals
sudo ./pimiproxy china-repos
sudo ./pimiproxy standard-repos

# Host-traffic toggle (advanced) — gateway clients always stay proxied
sudo pimiproxy system off      # host's own traffic bypasses Mihomo (direct)
sudo pimiproxy system on       # host back through Mihomo (default)
sudo pimiproxy system status   # (or `sudo clash-system <action>` directly)
```

---

## Uninstalling

```bash
sudo ./pimiproxy uninstall
```

This stops containers, removes images, deletes all installed files, clears routing rules, and optionally resets the hostname and removes avahi-daemon.

---

## After install: next steps

If you provided a provider URL at install time, your proxy groups and rules are already configured — open the dashboard and select a server in the proxy group.

If you skipped the provider prompt, the default config passes all traffic through directly (`MATCH,DIRECT`). Add a provider later via the management UI at `http://pimiproxy.lan:8585`, or via CLI:

```bash
sudo ./pimiproxy configure
```

Choose option 1 to set the URL, then option 5 to fetch it immediately.

See the [Mihomo documentation](https://wiki.metacubex.one) for config reference.

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## License

MIT
