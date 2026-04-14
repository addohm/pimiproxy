# pimiproxy

A self-hosted proxy server for your home network, running on a Raspberry Pi. pimiproxy uses [Mihomo](https://github.com/MetaCubeX/mihomo) (the Clash.Meta engine) inside Docker, with a web dashboard for monitoring and control.

Once installed, any device on your network can route its traffic through the Pi by pointing its proxy settings at `<pi-ip>:7890`. No VPN client, no app — just a standard HTTP/SOCKS5 proxy.

---

## What it does

- Runs Mihomo (Clash.Meta engine) in a Docker container on the Pi
- Exposes a mixed HTTP/SOCKS5 proxy port for other devices to use
- Provides a web dashboard (Metacubexd or Zashboard) for live traffic monitoring and config control
- Optionally routes the Pi's own traffic through the proxy via `clash-system`
- Sets up mDNS so the Pi is reachable as `pimiproxy.local` on the LAN, and as `pimiproxy.lan` on routers that use `.lan` as the local domain
- Shows a status banner on every SSH/terminal login — proxy engine state, system proxy on/off, proxy count, endpoints, and quick-command reference

## What it does not do

- It does not configure any upstream proxy or VPN out of the box — you bring your own provider and add it to `config.yaml`
- It does not touch your router — other devices must be configured manually to use the proxy
- It does not intercept LAN traffic automatically — devices opt in via their proxy settings

---

## Requirements

- Raspberry Pi (any model with 64-bit OS support recommended), or any x86_64 Linux host
- One of the supported distro families:
  - Debian / Raspberry Pi OS / Ubuntu / Mint / Pop
  - Fedora / RHEL / CentOS / Rocky / AlmaLinux
  - Arch / Manjaro / EndeavourOS
  - openSUSE
  - Alpine
- Internet access (for pulling Docker images if not using offline archives)
- Root / sudo access

---

## Project files

| File | Purpose |
|------|---------|
| `pimiproxy.sh` | One-command bootstrap — detects distro, installs all prerequisites, then launches `pimiproxy-core.py` |
| `pimiproxy-core.py` | Main installer and management tool — see subcommands below |
| `mihomo.tar.gz` | *(optional)* Pre-pulled Mihomo image for offline install |
| `metacubexd.tar.gz` | *(optional)* Pre-pulled Metacubexd image for offline install |
| `zashboard.tar.gz` | *(optional)* Pre-pulled Zashboard image for offline install |

The `.tar.gz` archives are not included in the repository. See [INSTRUCTIONS.md](INSTRUCTIONS.md) for how to prepare them.

---

## Quick start

**Option A — Bootstrap (recommended, handles all prerequisites):**

```bash
sudo bash pimiproxy.sh
```

This detects your distro, installs Python, Docker, avahi-daemon, and raspi-config if needed, then launches the installer automatically.

**Option B — Python script directly (if prerequisites are already installed):**

```bash
sudo python3 pimiproxy-core.py install
```

Press Enter through all prompts to accept defaults. See [INSTRUCTIONS.md](INSTRUCTIONS.md) for the full walkthrough.

**Installing from mainland China?** Docker's package servers are often blocked or very slow. Pass `--china-mirrors` to route the Docker CE installation through Aliyun's mirror instead:

```bash
sudo python3 pimiproxy-core.py install --china-mirrors
```

---

## Default settings

| Setting | Default |
|---------|---------|
| Install directory | `/opt/pimiproxy` |
| Hostname | `pimiproxy` (or current hostname if already customised) |
| Dashboard port | `8080` |
| Mihomo API port | `9090` |
| Mixed proxy port | `7890` |
| API secret | `pimiproxy` |
| Dashboard | Metacubexd |

---

## Connecting a device to the proxy

On any device connected to the same network as the Pi, open **proxy settings** (not network/WiFi settings) and set:

| Field | Value |
|-------|-------|
| Proxy type | HTTP or SOCKS5 |
| Host | `10.0.0.240` *(your Pi's IP)* |
| Port | `7890` |
| Username / Password | *(leave blank)* |

The same port accepts both HTTP and SOCKS5 connections.

---

## Accessing the dashboard

After install, open a browser and go to:

```
http://pimiproxy.lan:8080
```

If your router doesn't use `.lan`, try mDNS (most devices):

```
http://pimiproxy.local:8080
```

Or by IP directly:

```
http://10.0.0.240:8080
```

The API secret (default: `pimiproxy`) may be required on first load depending on the dashboard.

---

## Subcommands

All management is done through `pimiproxy-core.py`:

```bash
sudo python3 pimiproxy-core.py <subcommand>
```

| Subcommand / Flag | Description |
|---|---|
| `install` | Run the installer |
| `uninstall` | Stop containers, remove images, delete all files |
| `configure` | Interactive menu: subscription, proxy groups, rules, ports, secret, dashboard |
| `refresh` | Re-fetch subscription URL and restart Mihomo |
| `status` | Show container health, subscription info, and run a live proxy connectivity test |
| `logs [mihomo\|dashboard]` | Tail live container logs (default: mihomo) |
| `restart [mihomo\|dashboard]` | Restart one container or all |
| `update` | Pull latest Docker images and recreate containers |
| `hostname <name>` | Change the system hostname, restart avahi-daemon, and optionally reboot |
| `debug [url]` | Fetch a subscription URL with the Clash User-Agent and inspect the response |
| `install --china-mirrors` | Install using Aliyun mirrors for Docker CE (use in mainland China) |

---

## Status and connectivity test

```bash
sudo python3 pimiproxy-core.py status
```

Shows:
- Container up/down state
- `clash-system` routing state
- Subscription URL and number of proxies loaded
- Live HTTP and HTTPS connectivity test through the proxy, with latency
- Dashboard, API, and proxy endpoint URLs

---

## Subscription management

If you provided a Clash/Mihomo-compatible subscription URL at install time, pimiproxy fetches it during install and extracts the proxy list, proxy groups, and routing rules directly from the provider's config. The result is written into `config.yaml` — no separate provider fetch happens at Mihomo startup.

The subscription is refreshed automatically each night via a cron job. To refresh manually:

```bash
sudo python3 pimiproxy-core.py refresh
```

This re-fetches the URL, rebuilds `config.yaml`, and restarts Mihomo.

---

## Configuring proxy groups and rules

Run the interactive configurator:

```bash
sudo python3 pimiproxy-core.py configure
```

| Option | Description |
|---|---|
| 1) Manage subscription URL | Add or change your Clash/Mihomo API URL |
| 2) Manage proxy groups | Add or remove proxy groups |
| 3) Manage rules | Add, remove, or reorder routing rules |
| 4) Show current config | Print the live config.yaml to screen |
| 5) Refresh subscription now | Re-fetch server list and restart Mihomo |
| 6) Change API secret | Update the dashboard API password |
| 7) Change ports | Change dashboard, API, or proxy port |
| 8) Switch dashboard | Swap between Metacubexd and Zashboard |
| 9) Save and apply | Write changes and optionally restart containers |
| 10) Exit without saving | Discard all changes |

---

## Day-to-day management

```bash
# Check status and test the proxy
sudo python3 pimiproxy-core.py status

# View logs
sudo python3 pimiproxy-core.py logs              # Mihomo logs
sudo python3 pimiproxy-core.py logs dashboard    # Dashboard logs

# Restart containers
sudo python3 pimiproxy-core.py restart           # All containers
sudo python3 pimiproxy-core.py restart mihomo    # Mihomo only

# Update to latest images
sudo python3 pimiproxy-core.py update

# Change hostname
sudo python3 pimiproxy-core.py hostname newname

# Debug a subscription URL
sudo python3 pimiproxy-core.py debug
sudo python3 pimiproxy-core.py debug https://provider.example.com/api?token=abc123

# Pi system proxy
sudo clash-system on       # Route Pi's own traffic through the proxy
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

If you provided a subscription URL at install time, your proxy groups and rules are already configured — open the dashboard and select a server in the proxy group.

If you skipped the subscription prompt, the default config passes all traffic through directly (`MATCH,DIRECT`). Add a subscription later:

```bash
sudo python3 pimiproxy-core.py configure
```

Choose option 1 to set the URL, then option 5 to fetch it immediately.

See the [Mihomo documentation](https://wiki.metacubex.one) for config reference.

---

## License

MIT
