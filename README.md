# pimiproxy

A self-hosted proxy server for your home network, running on a Linux SBC or server. pimiproxy uses [Mihomo](https://github.com/MetaCubeX/mihomo) (the Clash.Meta engine) inside Docker, with a web dashboard for monitoring and a browser-based management UI for configuring proxy groups and rules.

Once installed, any device on your network can route its traffic through the SBC by pointing its proxy settings at `<pi-ip>:7890`. No VPN client, no app — just a standard HTTP/SOCKS5 proxy.

---

## What it does

- Runs Mihomo (Clash.Meta engine) in a Docker container on the SBC
- Exposes a mixed HTTP/SOCKS5 proxy port for other devices to use
- Provides a web dashboard (Metacubexd or Zashboard) for live traffic monitoring
- Provides a browser-based management UI for adding proxy groups, rules, and managing your provider URL
- Supports a two-layer config: your provider's subscription (base) + your own groups and rules (always applied on top)
- Optionally routes the SBC's own traffic through the proxy via `clash-system`
- Sets up mDNS so the SBC is reachable as `pimiproxy.local` on the LAN, and as `pimiproxy.lan` on routers that use `.lan` as the local domain
- Shows a status banner on every SSH/terminal login — proxy engine state, system proxy on/off, proxy count, endpoints, and quick-command reference

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

### Tested platforms

| Platform | Status |
|----------|--------|
| Raspberry Pi OS (64-bit) | ✅ Working |
| Ubuntu (x86_64) | 🔄 In progress |
| Fedora | 🔄 In progress |
| Arch Linux | ⬜ Untested |

---

## Project files

| File / Directory | Purpose |
|-----------------|---------|
| `pimiproxy.sh` | One-command bootstrap — detects distro, installs all prerequisites, then launches `pimiproxy-core.py` |
| `pimiproxy-core.py` | Main installer and management tool — see subcommands below |
| `pimiproxy-api/` | Django REST API container — manages config, auth, and Mihomo restarts |
| `pimiproxy-ui/` | React + Nginx frontend container — browser-based management UI |
| `docker-images/` | *(optional)* Pre-pulled `.tar.gz` image archives for offline install |

The `.tar.gz` archives are not included in the repository. See [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md) for how to prepare them.

---

## Quick start

**Option A — Bootstrap (recommended, handles all prerequisites):**

```bash
sudo ./pimiproxy.sh
```

This detects your distro, installs Python, Docker, avahi-daemon, and raspi-config if needed, then launches the installer automatically.

**Option B — Python script directly (if prerequisites are already installed):**

```bash
sudo python3 pimiproxy-core.py install
```

Press Enter through all prompts to accept defaults. See [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md) for the full walkthrough.

**Installing from mainland China?** The installer will ask whether the device is in China. Answer yes to route the Docker CE installation through Aliyun's mirror instead of the default upstream servers.

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

All CLI management is done through `pimiproxy-core.py`:

```bash
sudo python3 pimiproxy-core.py <subcommand>
```

| Subcommand / Flag | Description |
|---|---|
| `install` | Run the installer |
| `uninstall` | Stop containers, remove images, delete all files |
| `configure` | Interactive menu: provider URL, proxy groups, rules, ports, secret, dashboard |
| `refresh` | Re-fetch provider URL and restart Mihomo |
| `status` | Show container health, provider info, and run a live proxy connectivity test |
| `logs [service]` | Tail live container logs (default: mihomo; also: dashboard, pimiproxy-api, pimiproxy-ui) |
| `restart [service]` | Restart one container or all |
| `update` | Pull latest Docker images and recreate containers |
| `hostname <name>` | Change the system hostname, restart avahi-daemon, and optionally reboot |
| `debug [url]` | Fetch a provider URL with the Clash User-Agent and inspect the response |

---

## Status and connectivity test

```bash
sudo python3 pimiproxy-core.py status
```

Shows:
- Container up/down state
- `clash-system` routing state
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
sudo python3 pimiproxy-core.py refresh
```

---

## Day-to-day management

```bash
# Check status and test the proxy
sudo python3 pimiproxy-core.py status

# View logs
sudo python3 pimiproxy-core.py logs                  # Mihomo logs
sudo python3 pimiproxy-core.py logs dashboard        # Dashboard logs
sudo python3 pimiproxy-core.py logs pimiproxy-api    # API logs
sudo python3 pimiproxy-core.py logs pimiproxy-ui     # UI logs

# Restart containers
sudo python3 pimiproxy-core.py restart               # All containers
sudo python3 pimiproxy-core.py restart mihomo        # Mihomo only

# Update to latest images
sudo python3 pimiproxy-core.py update

# Change hostname
sudo python3 pimiproxy-core.py hostname newname

# Debug a provider URL
sudo python3 pimiproxy-core.py debug
sudo python3 pimiproxy-core.py debug https://provider.example.com/api?token=abc123

# SBC system proxy
sudo clash-system on       # Route SBC's own traffic through the proxy
sudo clash-system off      # Revert to direct
sudo clash-system status
```

---

## Uninstalling

```bash
sudo python3 pimiproxy-core.py uninstall
```

This stops containers, removes images, deletes all installed files, clears routing rules, and optionally resets the hostname and removes avahi-daemon.

---

## After install: next steps

If you provided a provider URL at install time, your proxy groups and rules are already configured — open the dashboard and select a server in the proxy group.

If you skipped the provider prompt, the default config passes all traffic through directly (`MATCH,DIRECT`). Add a provider later via the management UI at `http://pimiproxy.lan:8585`, or via CLI:

```bash
sudo python3 pimiproxy-core.py configure
```

Choose option 1 to set the URL, then option 5 to fetch it immediately.

See the [Mihomo documentation](https://wiki.metacubex.one) for config reference.

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## License

MIT
