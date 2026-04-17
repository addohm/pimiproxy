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
- Supports a two-layer config: your provider's subscription (base) + your own groups and rules (always applied on top)
- Routes all device traffic through the proxy via TUN mode (always on)
- Sets up mDNS so the SBC is reachable as `pimiproxy.local` on the LAN, and as `pimiproxy.lan` on routers that use `.lan` as the local domain
- Shows a status banner on every SSH/terminal login — proxy engine state, proxy count, endpoints, and quick-command reference

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
- Internet access (for pulling Docker images if not using offline archives)
- Root / sudo access

### Platform compatibility

Pre-built release archives are produced for both the full (Docker) and
ultralite (native binary) variants. Availability depends on whether
upstream Docker images publish the target platform — ultralite has
broader arch coverage because it ships a single self-contained binary.

| Architecture | Full variant | Ultralite variant | Example hardware |
|---|---|---|---|
| `amd64` (x86_64) | ✅ Available | ✅ Available | Intel/AMD PCs, most VMs, x86 mini-PCs |
| `arm64` (aarch64) | ✅ Available | ✅ Available | Raspberry Pi 4/5, modern 64-bit SBCs |
| `armv7` | ❌ N/A (metacubexd missing) | ✅ Available | Raspberry Pi 3, older 32-bit ARM SBCs |
| `386` (x86 32-bit) | ❌ N/A (metacubexd missing) | ✅ Available | Legacy x86 PCs |

### Tested distros

| Distro | Full | Ultralite |
|---|---|---|
| Raspberry Pi OS (arm64 / Debian) | ✅ Working | ✅ Working |
| Xubuntu / Ubuntu 25.10 (x86_64) | ✅ Working | ✅ Working |
| Fedora 43 Xfce (x86_64) | ✅ Working | ✅ Working |
| Arch Linux (x86_64) | ✅ Working | ✅ Working |
| openSUSE | ⬜ Untested | ⬜ Untested |
| Alpine | ⬜ Untested | ⬜ Untested |

---

## Project files

| File / Directory | Purpose |
|-----------------|---------|
| `pimiproxy.sh` | One-command bootstrap — detects distro, installs all prerequisites, then launches `pimiproxy-core.py` |
| `pimiproxy-core.py` | Main installer and management tool — see subcommands below |
| `pimiproxy-ultralite.sh` | Bootstrap for the ultralite (Docker-free) variant |
| `pimiproxy-ultralite-core.py` | Ultralite installer — runs Mihomo as a native systemd service, no Docker/web UI |
| `pimiproxy-api/` | Django REST API container — manages config, auth, and Mihomo restarts |
| `pimiproxy-ui/` | React + Nginx frontend container — browser-based management UI |
| `docker-images/` | *(optional)* Pre-pulled `.tar.gz` image archives for offline install |
| `bin/` | *(optional)* Mihomo release binaries for offline/ultralite installs |

The `.tar.gz` archives are not included in the repository. See [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md) for how to prepare them.

---

## Quick start

**Option A — Bootstrap (recommended, handles all prerequisites):**

```bash
sudo ./pimiproxy.sh
```

This detects your distro, installs essential tools (curl, iproute2, procps), Python, cron, Docker, avahi-daemon, and raspi-config if needed, then launches the installer automatically.

**Option B — Python script directly (if prerequisites are already installed):**

```bash
sudo ./pimiproxy.sh install
```

Press Enter through all prompts to accept defaults. See [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md) for the full walkthrough.

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
| Management UI | `http://pimiproxy.lan:8585` |
| Dashboard (Metacubexd) | `http://pimiproxy.lan:8080` |
| Mihomo API | `http://pimiproxy.lan:9090` |

If your router doesn't use `.lan`, try mDNS: `http://pimiproxy.local:8585`

**Management UI login:** use your API secret (default: `pimiproxy`).

---

## Connecting a device to the proxy

On any device connected to the same network as the SBC, open **proxy settings** (not network/WiFi settings) and set:

| Field | Value |
|-------|-------|
| Proxy type | HTTP or SOCKS5 |
| Host | `10.0.0.240` *(your SBC's IP)* |
| Port | `7890` |
| Username / Password | *(leave blank)* |

The same port accepts both HTTP and SOCKS5 connections.

---

## Subcommands

All CLI management is done through `pimiproxy.sh`:

```bash
sudo ./pimiproxy.sh <subcommand>
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
| `update` | Pull latest Docker images and recreate containers |
| `hostname <name>` | Change the system hostname (max 24 chars, alphanumeric + hyphens), restart avahi-daemon, and optionally reboot |
| `debug [url]` | Fetch a provider URL with the Clash User-Agent and inspect the response |
| `export [dir]` | Export user config, cached provider YAML, and final config.yaml to a directory |
| `standard-repos` | Restore original system package repositories from backups |
| `china-repos` | Switch system package repositories (apt/dnf/pacman) to Aliyun mirrors |

---

## Status and connectivity test

```bash
sudo ./pimiproxy.sh status
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
sudo ./pimiproxy.sh refresh
```

---

## Day-to-day management

```bash
# Check status and test the proxy
sudo ./pimiproxy.sh status

# Test proxy groups for connectivity
sudo ./pimiproxy.sh test
sudo ./pimiproxy.sh test --url https://example.com

# View logs
sudo ./pimiproxy.sh logs                  # Mihomo logs
sudo ./pimiproxy.sh logs dashboard        # Dashboard logs
sudo ./pimiproxy.sh logs pimiproxy-api    # API logs
sudo ./pimiproxy.sh logs pimiproxy-ui     # UI logs

# Restart containers
sudo ./pimiproxy.sh restart               # All containers
sudo ./pimiproxy.sh restart mihomo        # Mihomo only

# Update to latest images
sudo ./pimiproxy.sh update

# Change hostname
sudo ./pimiproxy.sh hostname newname

# Debug a provider URL
sudo ./pimiproxy.sh debug
sudo ./pimiproxy.sh debug https://provider.example.com/api?token=abc123

# Export config for backup or debugging
sudo ./pimiproxy.sh export
sudo ./pimiproxy.sh export /tmp/pimiproxy-export

# Switch system repos to Aliyun mirrors (China) or restore originals
sudo ./pimiproxy.sh china-repos
sudo ./pimiproxy.sh standard-repos

# All device traffic is always tunneled through the proxy via TUN mode.
# No manual on/off toggle is needed.
```

---

## Uninstalling

```bash
sudo ./pimiproxy.sh uninstall
```

This stops containers, removes images, deletes all installed files, clears routing rules, and optionally resets the hostname and removes avahi-daemon.

---

## After install: next steps

If you provided a provider URL at install time, your proxy groups and rules are already configured — open the dashboard and select a server in the proxy group.

If you skipped the provider prompt, the default config passes all traffic through directly (`MATCH,DIRECT`). Add a provider later via the management UI at `http://pimiproxy.lan:8585`, or via CLI:

```bash
sudo ./pimiproxy.sh configure
```

Choose option 1 to set the URL, then option 5 to fetch it immediately.

See the [Mihomo documentation](https://wiki.metacubex.one) for config reference.

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## License

MIT
