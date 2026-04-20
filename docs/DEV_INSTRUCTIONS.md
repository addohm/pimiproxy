# pimiproxy — Installation & Usage Instructions

---

## Table of contents

1. [Before you begin](#1-before-you-begin)
2. [Preparing offline image archives (optional)](#2-preparing-offline-image-archives-optional)
3. [Running the installer](#3-running-the-installer)
4. [Installer prompts explained](#4-installer-prompts-explained)
5. [Connecting devices to the proxy](#5-connecting-devices-to-the-proxy)
6. [Accessing the dashboard](#6-accessing-the-dashboard)
7. [Web management UI](#7-web-management-ui)
8. [Configuring proxy groups and rules](#8-configuring-proxy-groups-and-rules)
9. [Day-to-day management](#9-day-to-day-management)
10. [Uninstalling](#10-uninstalling)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Before you begin

**On the SBC:**
- Supported OS: Raspberry Pi OS, Debian, Ubuntu, Fedora, Arch, openSUSE, Alpine, or any compatible derivative
- SBC is connected to your network via ethernet (recommended) or WiFi
- You know the SBC's IP address — check your router's device list, or run `hostname -I` on the SBC
- You have SSH access or a keyboard/monitor connected

**On your network:**
- The SBC's IP should ideally be static, or reserved in your router's DHCP settings so it doesn't change. pimiproxy does not configure a static IP for you.

---

## 2. Preparing offline image archives (optional)

If your SBC has limited or no internet access, you can pre-build or pre-pull the Docker images on another machine and transfer them to the SBC.

### Third-party images (pulled from registries)

Run the following on your other machine (must have Docker installed and QEMU binfmt set up for arm64 cross-builds):

```bash
# Pull for arm64
docker pull --platform linux/arm64 ghcr.io/metacubex/mihomo:latest
docker pull --platform linux/arm64 ghcr.io/metacubex/metacubexd:latest
docker pull --platform linux/arm64 ghcr.io/zephyruso/zashboard:latest

# Save to compressed archives in the docker-images/ directory
docker save ghcr.io/metacubex/mihomo:latest     | gzip > docker-images/mihomo.tar.gz
docker save ghcr.io/metacubex/metacubexd:latest  | gzip > docker-images/metacubexd.tar.gz
docker save ghcr.io/zephyruso/zashboard:latest   | gzip > docker-images/zashboard.tar.gz
```

### pimiproxy-api and pimiproxy-ui (built from source)

These two images are built locally from the `pimiproxy-api/` and `pimiproxy-ui/` source directories. If the SBC cannot run `docker build` (e.g. no internet access for base image pulls), build them on your other machine and save the result:

```bash
# Enable arm64 cross-build support (one-time setup)
docker run --privileged --rm tonistiigi/binfmt --install arm64

# Build for arm64
docker build --platform linux/arm64 -t pimiproxy-api:latest ./pimiproxy-api/
docker build --platform linux/arm64 -t pimiproxy-ui:latest  ./pimiproxy-ui/

# Save to compressed archives
docker save pimiproxy-api:latest | gzip > docker-images/pimiproxy-api.tar.gz
docker save pimiproxy-ui:latest  | gzip > docker-images/pimiproxy-ui.tar.gz
```

### Transfer to the SBC

```bash
rsync -avz docker-images/ user@pimiproxy.lan:~/pimiproxy/docker-images/
```

Place all `.tar.gz` archives in the `docker-images/` subdirectory alongside `pimiproxy-core.py`. If archives are present when the installer runs, they will be loaded from disk instead of pulled or built from source.

### Geodata files (bundled with pimiproxy)

`geoip.metadb`, `geosite.dat`, and `country.mmdb` should also be placed in `docker-images/`. The installer copies them to `/opt/pimiproxy/config/` so Mihomo has them available without needing network access to GitHub. These files are required for GEOSITE and GEOIP rule matching. `geo-auto-update: false` is set in all generated configs — Mihomo will never attempt to download or update them at runtime.

---

## 3. Running the installer

SSH into your SBC (or open a terminal on it directly) and navigate to the directory containing the pimiproxy files.

### Option A — Bootstrap (recommended)

The bootstrap script detects your distro, installs essential tools (curl, iproute2, procps), Python 3, cron, Docker (via the official `get.docker.com` installer), avahi-daemon, and raspi-config automatically, then launches the Python installer. This is the easiest path and works on Debian, Ubuntu, Fedora, Arch, openSUSE, Alpine, and their derivatives.

```bash
sudo ./pimiproxy
```

Pass a subcommand to control what happens after prerequisites are confirmed:

```bash
sudo ./pimiproxy install     # default
sudo ./pimiproxy uninstall
sudo ./pimiproxy configure
```

### Option B — Python script directly

If Python 3, Docker + Compose, cron, and avahi-daemon are already installed:

```bash
sudo ./pimiproxy install
```

The script will walk you through a series of prompts. Press **Enter** at each one to accept the default value shown in brackets.

To uninstall later:

```bash
sudo ./pimiproxy uninstall
```

### Installing from mainland China

Docker's package servers (`download.docker.com`) and standard distro repositories are frequently blocked or unreachable from mainland China. If `pimiproxy` needs to install Docker CE, it will ask:

```
  Is this device located in China? (use Aliyun mirrors for Docker CE) [y/N]:
```

Answer **y** to switch system package repositories (apt/dnf/pacman) and Docker CE to Aliyun mirrors. On Debian/Ubuntu, apt sources are replaced with `mirrors.aliyun.com` (with a separate `/debian-security` mirror path for Debian security updates). On Fedora, core repos are switched to Aliyun base URLs and third-party repos (cisco-openh264 etc.) are disabled. On Arch, the mirrorlist is replaced with Aliyun. This question only appears during install. Everything else — image pulls, subscription fetches, runtime traffic — is unaffected and routes through whatever proxy you configure.

After install, you can switch mirrors at any time:

```bash
sudo ./pimiproxy china-repos      # Switch to Aliyun mirrors
sudo ./pimiproxy standard-repos    # Restore original repos from backups
```

### Supported platforms

| Distro family | Package manager | Docker package |
|---|---|---|
| Debian / Raspberry Pi OS / Ubuntu / Mint | apt | `docker.io` (distro repo) or Docker CE via official repo |
| Fedora / RHEL / CentOS / Rocky / Alma | dnf | `docker-ce` via Docker CE repo |
| Arch / Manjaro / EndeavourOS | pacman | `docker` (community repo) or Docker CE via `get.docker.com` |
| openSUSE | zypper | `docker-ce` via Docker CE repo |
| Alpine | apk | `docker-ce` via Docker CE repo |

The installer detects your distro automatically from `/etc/os-release` and selects the correct package manager and package names. No manual configuration is required.

---

## 4. Installer prompts explained

### Install directory
**Default: `/opt/pimiproxy`**

Where all pimiproxy files live — the Docker Compose file, Mihomo config, and install metadata. Leave this as the default unless you have a specific reason to change it.

### Hostname
**Default: `pimiproxy` (or the current hostname if the device has already been renamed)**

Sets the SBC's system hostname. Limited to 24 characters, alphanumeric and hyphens only. After install the SBC will be reachable as `<hostname>.lan` on routers that assign `.lan` hostnames via DHCP/DNS, and as `<hostname>.local` via mDNS on devices that support it.

If the device's hostname is already something other than a generic default (`raspberrypi`, `localhost`, `debian`, `ubuntu`, `alarm`), the installer will pre-fill the prompt with the existing name — press Enter to keep it unchanged.

If the hostname is changed during install, you will be asked at the end whether you want to reboot. A reboot is the easiest way to ensure the router registers the new hostname via DHCP. The `.local` mDNS name is available immediately without a reboot.

You can also change the hostname at any time after install:

```bash
sudo ./pimiproxy hostname <new-hostname>
```

This updates the system hostname, automatically restarts avahi-daemon so `.local` takes effect immediately, and prompts you to reboot for the `.lan` name to propagate.

### Dashboard port
**Default: `8080`**

The port the web dashboard is served on. Open `http://<sbc-ip>:8080` in a browser to access it.

### Mihomo API port
**Default: `9090`**

The port the Mihomo API listens on. The dashboard communicates with Mihomo through this port. You generally do not need to change this unless something else on the SBC is already using 9090.

### Mixed proxy port
**Default: `7890`**

The port other devices use to connect to the proxy. This is the port you enter in proxy settings on phones, computers, etc. It accepts both HTTP and SOCKS5 connections on the same port.

### API secret
**Default: `pimiproxy`**

A password used by the dashboard to authenticate with the Mihomo API. The secret is shown in the install summary at the end of the install process. You will be prompted to enter it in the dashboard on first load. Change it to something stronger if the SBC is accessible outside your home network.

### Provider URL
**Default: none (optional)**

If you have a proxy provider service, paste your Clash-compatible API URL here. This must be a **Clash/Mihomo API URL** — the kind your provider gives you specifically for use with Clash, Mihomo, or compatible clients. It is not a regular VPN config file, an app download link, or a WireGuard/OpenVPN config.

Examples of what this looks like:
```
https://provider.example.com/api/v1/client/subscribe?token=abc123
https://another-provider.example.com/clash?token=abc123
```

If you provide a URL, the installer will fetch it immediately and validate that it contains proxies before proceeding. The installer will generate a complete working config with:
- A `select` group — pick a server manually from the dashboard
- An `Auto - Fastest` group — automatically uses the lowest-latency server
- LAN and private IP bypass rules so local traffic never goes through the proxy
- Daily automatic refresh of the server list from your provider

If you leave it blank, the proxy will still run but will pass all traffic through directly (no upstream provider). You can add a subscription later by running `sudo ./pimiproxy configure`.

### Dashboard choice
**Default: `1` (Metacubexd)**

Two dashboard options are available:

| Option | Description |
|--------|-------------|
| **Metacubexd** | Clean, standard dashboard. Good for most users. |
| **Zashboard** | More detailed, power-user focused. More controls and stats. |

Both connect to the same Mihomo instance. You can switch between them at any time without reinstalling — use option 8 in the `configure` menu.

---

## 5. Connecting devices to the proxy

The proxy is available immediately after install. Devices connect by setting their **proxy settings** — not their WiFi/network settings. The device stays connected to the network normally; you are only telling it to route application traffic through the SBC.

**The proxy address for all devices (default settings):**

| Field | Value |
|-------|-------|
| Host / Server | Your SBC's IP address, e.g. `10.0.0.240` |
| Port | `7890` |
| Type | HTTP or SOCKS5 (both work on the same port) |
| Username | *(leave blank)* |
| Password | *(leave blank)* |

### Windows

Settings → Network & Internet → Proxy → Manual proxy setup

Turn on "Use a proxy server", enter the host and port, save.

### macOS

System Settings → Network → select your connection → Details → Proxies

Enable "Web Proxy (HTTP)" and/or "SOCKS Proxy", enter host and port.

### iPhone / iPad

Settings → Wi-Fi → tap the `ⓘ` next to your network → Configure Proxy → Manual

Enter the server and port.

### Android

Settings → Wi-Fi → long press your network → Modify network → Advanced options → Proxy → Manual

Enter the hostname and port.

### Linux (system-wide)

Varies by desktop environment. In GNOME: Settings → Network → Network Proxy → Manual.

Or set environment variables for terminal sessions:

```bash
export http_proxy="http://10.0.0.240:7890"
export https_proxy="http://10.0.0.240:7890"
export all_proxy="socks5://10.0.0.240:7890"
```

---

## 6. Accessing the dashboard

Open a browser and navigate to:

```
http://pimiproxy.lan:8080
```

If `.lan` doesn't resolve, try mDNS (most non-Android devices):

```
http://pimiproxy.local:8080
```

Or by IP directly:

```
http://10.0.0.240:8080
```

On first load the dashboard will ask for the API backend address and secret:

- **API URL:** `http://pimiproxy.lan:9090` (or `http://10.0.0.240:9090`)
- **Secret:** `pimiproxy` (or whatever you set during install)

The dashboard shows live connections, traffic speed, active rules, and lets you switch proxy modes and groups without restarting Mihomo.

---

## 7. Web management UI

pimiproxy includes a browser-based management UI for managing proxy groups, rules, and the provider URL — no SSH required.

**Default URL:** `http://pimiproxy.lan:8585`

Log in with your API secret (default: `pimiproxy`).

The UI has five tabs:

| Tab | Description |
|-----|-------------|
| **Overview** | Container status, proxy count, and provider info |
| **Groups** | View provider groups (read-only) and manage your own groups |
| **Rules** | Manage your rules (drag to reorder); provider rules shown below as read-only |
| **Check URL** | Test whether a URL is reachable through the proxy. Shows HTTP status, latency, and the live Mihomo routing decision (matched rule, proxy chain) |
| **Settings** | Provider URL, manual refresh, port info |

The UI is served by two Docker containers (`pimiproxy-api` and `pimiproxy-ui`) that are built and started as part of the install. If they are not running, use option 11 in the `configure` menu to build or rebuild them:

```bash
sudo ./pimiproxy configure
# choose option 11
```

---

## 8. Configuring proxy groups and rules

Use the interactive configurator to manage your subscription URL, proxy groups, rules, ports, API secret, and dashboard choice:

```bash
sudo ./pimiproxy configure
```

### Configure mode menu

| Option | Description |
|--------|-------------|
| 1) Manage provider URL | Add or change your Clash/Mihomo API URL |
| 2) Manage proxy groups | Add or remove proxy groups |
| 3) Manage rules | Add, remove, or reorder routing rules |
| 4) Show current config | Print the live config.yaml to screen |
| 5) Refresh provider now | Re-fetch server list and restart Mihomo |
| 6) Change API secret | Update the dashboard API password |
| 7) Change ports | Change dashboard, API, proxy, or UI ports |
| 8) Switch dashboard | Swap between Metacubexd and Zashboard |
| 9) Save and apply | Write changes and optionally restart containers |
| 10) Exit without saving | Discard all changes |
| 11) Enable / rebuild web management UI | Build or reload the pimiproxy-api and pimiproxy-ui containers |

**Proxy groups** — add groups that control how traffic is routed:

| Type | Behaviour |
|------|-----------|
| `select` | You pick a server manually in the dashboard |
| `url-test` | Automatically uses the fastest server |
| `fallback` | Uses the first available server |
| `load-balance` | Spreads connections across servers |

**Rules** — control which traffic goes where. Rules are evaluated top to bottom:

| Example | Meaning |
|---------|---------|
| `DOMAIN-SUFFIX,google.com,Proxy` | Send Google traffic via Proxy group |
| `IP-CIDR,192.168.0.0/16,DIRECT` | Keep LAN traffic direct |
| `MATCH,Proxy` | Send everything else via Proxy (catch-all) |

**Presets** — common rule sets available in one step: LAN bypass, local hostname bypass, and catch-all options.

**Port changes** — changing the dashboard port rebuilds `docker-compose.yml` and recreates the dashboard container on save. Changing the API or proxy port rebuilds `config.yaml` and restarts Mihomo.

**Dashboard switch** — pulls or loads the new dashboard image and rebuilds `docker-compose.yml`. The container is recreated on save.

After making changes, option 9 will ask whether to restart the affected containers immediately.

To restart Mihomo manually after editing `/opt/pimiproxy/config/config.yaml` directly:

```bash
sudo ./pimiproxy restart mihomo
```

---

## 9. Day-to-day management

### Login banner

A status banner is displayed automatically on every interactive SSH or terminal login:

```
  pimiproxy
  ──────────────────────────────────────────
  Proxy engine    ●  running
  TUN mode        ●  always on
  Proxies loaded     47
  AES hardware    ●  available
  ──────────────────────────────────────────
  Dashboard  →  http://pimiproxy.lan:8080
  UI         →  http://pimiproxy.lan:8585
  API        →  http://pimiproxy.lan:9090
  Proxy      →  pimiproxy.lan:7890  (HTTP/SOCKS5)
  ──────────────────────────────────────────
  pimiproxy-core.py [status|logs|configure|debug]
  ──────────────────────────────────────────
```

The AES line shows green `●  available` when the CPU has hardware AES acceleration, or yellow `○  software fallback — higher latency` when it does not. This is checked once at install time and baked into the banner — it won't change unless you reinstall.

The banner is installed to `/etc/profile.d/pimiproxy-banner.sh` (for login shells via SSH) and sourced from `/etc/bash.bashrc` (for desktop terminals / non-login shells). Both are removed by the uninstaller. If you change ports via `configure`, the banner is rebuilt automatically.

### Check status

```bash
sudo ./pimiproxy status
```

Shows container health, subscription info (URL and number of proxies loaded), and runs a live HTTP and HTTPS connectivity test through the proxy with latency results.

### View logs

```bash
sudo ./pimiproxy logs                   # Mihomo logs (default)
sudo ./pimiproxy logs dashboard         # Dashboard logs
sudo ./pimiproxy logs pimiproxy-api     # API container logs
sudo ./pimiproxy logs pimiproxy-ui      # UI container logs
```

Press Ctrl-C to stop the log stream.

### Restart containers

```bash
sudo ./pimiproxy restart                # Restart all containers
sudo ./pimiproxy restart mihomo         # Restart Mihomo only
sudo ./pimiproxy restart dashboard      # Restart dashboard only
sudo ./pimiproxy restart pimiproxy-api  # Restart API only
sudo ./pimiproxy restart pimiproxy-ui   # Restart UI only
```

### Update to latest images

```bash
sudo ./pimiproxy update
```

Pulls the latest Docker images for all containers and recreates them. Safe to run at any time.

### Test proxy connectivity

```bash
sudo ./pimiproxy test                    # Test all proxy groups
sudo ./pimiproxy test --url https://example.com  # Test a single URL
```

### Export config

```bash
sudo ./pimiproxy export                  # Export to current directory
sudo ./pimiproxy export /tmp/backup      # Export to a specific directory
```

Exports the user config JSON, cached provider YAML, and the final `config.yaml` for backup or debugging.

### Switch system package mirrors

```bash
sudo ./pimiproxy china-repos             # Switch to Aliyun mirrors
sudo ./pimiproxy standard-repos           # Restore original repos from backups
```

### Refresh subscription (manual)

The installer creates a daily cron job (`/etc/cron.daily/pimiproxy-refresh`) that automatically re-fetches your provider's server list and restarts Mihomo. To trigger a refresh manually:

```bash
sudo ./pimiproxy refresh
```

### Change hostname

```bash
sudo ./pimiproxy hostname <new-hostname>
```

Updates the system hostname, automatically restarts avahi-daemon so `.local` resolves immediately, and prompts you to reboot so the router registers the new `.lan` name via DHCP.

### Edit config directly

```bash
sudo nano /opt/pimiproxy/config/config.yaml
sudo ./pimiproxy restart mihomo
```

---

## 10. Uninstalling

```bash
sudo ./pimiproxy uninstall
```

The uninstaller will:

1. Stop and remove all containers (mihomo, dashboard, pimiproxy-api, pimiproxy-ui)
2. Remove the associated Docker images
3. Delete `/opt/pimiproxy` and all contents (including `ui-secret.txt`)
4. Remove the daily subscription refresh cron job (`/etc/cron.daily/pimiproxy-refresh`)
5. Optionally reset the hostname back to `raspberrypi`
6. Optionally remove avahi-daemon

---

## 11. Troubleshooting

### Check if everything is working

```bash
sudo ./pimiproxy status
```

This is the first thing to run — it shows container state, tests the proxy with a real HTTP and HTTPS request, and prints all endpoint URLs in one place.

### Dashboard won't load

- Confirm the containers are running: `sudo ./pimiproxy status`
- Try the IP address instead of the hostname: `http://10.0.0.240:8080`
- Check firewall rules aren't blocking the port

### Hostname doesn't resolve

- **`.lan`** resolves via your router's DNS once the SBC renews its DHCP lease. The fastest way to trigger this is to reboot (`sudo reboot`) or run `sudo ./pimiproxy hostname <current-hostname>` which will prompt you to reboot.
- **`.local`** resolves via mDNS (avahi). It can take up to 30 seconds to propagate after first boot. Some Android versions don't support `.local` — use `.lan` or the IP address instead.
- Confirm avahi-daemon is running: `systemctl status avahi-daemon`
- If neither resolves, use the IP address directly.

### Proxy not working on a device

- Run `sudo ./pimiproxy status` — the proxy test will show exactly where it fails
- Confirm you set proxy settings, not network/WiFi settings
- Confirm the port is `7890`, not `9090` (that's the API, not the proxy)
- Test from the SBC itself: `curl -x http://localhost:7890 https://example.com`
- Check Mihomo logs: `sudo ./pimiproxy logs`

### Install fails with a Docker error

- Confirm Docker is running: `systemctl status docker`
- If using offline archives, confirm the `.tar.gz` files are in the same directory as `pimiproxy-core.py`
- Try pulling manually to see the error: `docker pull --platform linux/arm64 ghcr.io/metacubex/mihomo:latest`

### Subscription URL returns no proxies

Some provider APIs only respond correctly when the client sends a Clash-compatible `User-Agent` header. Use the built-in `debug` subcommand to inspect the raw response:

```bash
sudo ./pimiproxy debug
```

Paste your provider URL when prompted (or pass it directly as an argument). The command sends a request with `User-Agent: Clash-Verge/1.0 (clash-verge-rev; mihomo)`, reports the status code, content type, and how many proxies/groups/rules were found in the response, then saves the full response to `raw_debug_output.yaml` in the current directory.

If the response contains proxies, re-run the installer or use option 1 → option 5 in `configure` to refresh. If it doesn't, the URL or token is wrong for Clash clients — check with your provider.

### Changing ports or API secret after install

Use the configure menu — no need to reinstall:

```bash
sudo ./pimiproxy configure
```

- Option 6 to change the API secret
- Option 7 to change any port
- Option 8 to switch dashboard
- Option 9 to save and apply
