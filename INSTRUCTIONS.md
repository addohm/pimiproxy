# pimiproxy — Installation & Usage Instructions

---

## Table of contents

1. [Before you begin](#1-before-you-begin)
2. [Preparing offline image archives (optional)](#2-preparing-offline-image-archives-optional)
3. [Running the installer](#3-running-the-installer)
4. [Installer prompts explained](#4-installer-prompts-explained)
5. [Connecting devices to the proxy](#5-connecting-devices-to-the-proxy)
6. [Accessing the dashboard](#6-accessing-the-dashboard)
7. [Configuring proxy groups and rules](#7-configuring-proxy-groups-and-rules)
8. [Routing the Pi's own traffic through the proxy](#8-routing-the-pis-own-traffic-through-the-proxy)
9. [Day-to-day management](#9-day-to-day-management)
10. [Uninstalling](#10-uninstalling)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Before you begin

**On the Pi:**
- Fresh install of Raspberry Pi OS (Bookworm or Bullseye) or Debian 11/12
- Pi is connected to your network via ethernet (recommended) or WiFi
- You know the Pi's IP address — check your router's device list, or run `hostname -I` on the Pi
- You have SSH access or a keyboard/monitor connected

**On your network:**
- The Pi's IP should ideally be static, or reserved in your router's DHCP settings so it doesn't change. pimiproxy does not configure a static IP for you.

---

## 2. Preparing offline image archives (optional)

If your Pi has limited or no internet access, you can pre-pull the Docker images on another machine (must be Linux/macOS with Docker installed and able to pull `linux/arm64` images) and transfer them to the Pi.

Run the following on your other machine:

```bash
# Pull for arm64
docker pull --platform linux/arm64 ghcr.io/metacubex/mihomo:latest
docker pull --platform linux/arm64 ghcr.io/metacubex/metacubexd:latest
docker pull --platform linux/arm64 ghcr.io/zephyruso/zashboard:latest

# Save to compressed archives
docker save ghcr.io/metacubex/mihomo:latest      | gzip > mihomo.tar.gz
docker save ghcr.io/metacubex/metacubexd:latest   | gzip > metacubexd.tar.gz
docker save ghcr.io/zephyruso/zashboard:latest    | gzip > zashboard.tar.gz
```

Then copy the `.tar.gz` files to the Pi into the same directory as `pimiproxy-core.py`:

```bash
scp mihomo.tar.gz metacubexd.tar.gz zashboard.tar.gz user@pimiproxy.lan:~/pimiproxy/
```

If the archives are present in the same directory as the script when you run it, they will be loaded from disk instead of pulled from the internet.

---

## 3. Running the installer

SSH into your Pi (or open a terminal on it directly) and navigate to the directory containing the pimiproxy files.

### Option A — Bootstrap (recommended)

The bootstrap script detects your distro, installs Python 3, Docker (via the official `get.docker.com` installer), avahi-daemon, and raspi-config automatically, then launches the Python installer. This is the easiest path and works on Debian, Ubuntu, Fedora, Arch, openSUSE, Alpine, and their derivatives.

```bash
sudo bash pimiproxy.sh
```

Pass a subcommand to control what happens after prerequisites are confirmed:

```bash
sudo bash pimiproxy.sh install     # default
sudo bash pimiproxy.sh uninstall
sudo bash pimiproxy.sh configure
```

### Option B — Python script directly

If Python 3, Docker + Compose, and avahi-daemon are already installed:

```bash
sudo python3 pimiproxy-core.py install
```

The script will walk you through a series of prompts. Press **Enter** at each one to accept the default value shown in brackets.

To uninstall later:

```bash
sudo python3 pimiproxy-core.py uninstall
```

### Supported platforms

| Distro family | Package manager | Notes |
|---|---|---|
| Debian / Raspberry Pi OS / Ubuntu / Mint | apt | Fully tested baseline |
| Fedora / RHEL / CentOS / Rocky / Alma | dnf | Docker installed via get.docker.com |
| Arch / Manjaro / EndeavourOS | pacman | Docker installed via get.docker.com |
| openSUSE | zypper | Docker installed via get.docker.com |
| Alpine | apk | Docker installed via get.docker.com |

---

## 4. Installer prompts explained

### Install directory
**Default: `/opt/pimiproxy`**

Where all pimiproxy files live — the Docker Compose file, Mihomo config, and install metadata. Leave this as the default unless you have a specific reason to change it.

### Hostname
**Default: `pimiproxy` (or the current hostname if the device has already been renamed)**

Sets the Pi's system hostname. After install the Pi will be reachable as `<hostname>.lan` on routers that assign `.lan` hostnames via DHCP/DNS, and as `<hostname>.local` via mDNS on devices that support it.

If the device's hostname is already something other than a generic default (`raspberrypi`, `localhost`, `debian`, `ubuntu`, `alarm`), the installer will pre-fill the prompt with the existing name — press Enter to keep it unchanged.

If the hostname is changed during install, you will be asked at the end whether you want to reboot. A reboot is the easiest way to ensure the router registers the new hostname via DHCP. The `.local` mDNS name is available immediately without a reboot.

You can also change the hostname at any time after install:

```bash
sudo python3 pimiproxy-core.py hostname <new-hostname>
```

This updates the system hostname, automatically restarts avahi-daemon so `.local` takes effect immediately, and prompts you to reboot for the `.lan` name to propagate.

### Dashboard port
**Default: `8080`**

The port the web dashboard is served on. Open `http://<pi-ip>:8080` in a browser to access it.

### Mihomo API port
**Default: `9090`**

The port the Mihomo API listens on. The dashboard communicates with Mihomo through this port. You generally do not need to change this unless something else on the Pi is already using 9090.

### Mixed proxy port
**Default: `7890`**

The port other devices use to connect to the proxy. This is the port you enter in proxy settings on phones, computers, etc. It accepts both HTTP and SOCKS5 connections on the same port.

### API secret
**Default: `pimiproxy`**

A password used by the dashboard to authenticate with the Mihomo API. You will be prompted to enter this in the dashboard on first load. Change it to something stronger if the Pi is accessible outside your home network.

### Clash API subscription URL
**Default: none (optional)**

If you have a proxy provider service, paste your Clash-compatible API URL here. This must be a **Clash/Mihomo API URL** — the kind your provider gives you specifically for use with Clash, Mihomo, or compatible clients. It is not a regular VPN config file, an app download link, or a WireGuard/OpenVPN config.

Examples of what this looks like:
```
https://provider.example.com/api/v1/client/subscribe?token=abc123
https://api.wannaflix.link/apiv4.php?token=abc123
```

If you provide a URL, the installer will generate a complete working config with:
- A `select` group — pick a server manually from the dashboard
- An `Auto - Fastest` group — automatically uses the lowest-latency server
- LAN and private IP bypass rules so local traffic never goes through the proxy
- Daily automatic refresh of the server list from your provider

If you leave it blank, the proxy will still run but will pass all traffic through directly (no upstream provider). You can add a subscription later by running `sudo python3 pimiproxy-core.py configure`.

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

The proxy is available immediately after install. Devices connect by setting their **proxy settings** — not their WiFi/network settings. The device stays connected to the network normally; you are only telling it to route application traffic through the Pi.

**The proxy address for all devices (default settings):**

| Field | Value |
|-------|-------|
| Host / Server | Your Pi's IP address, e.g. `10.0.0.240` |
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

## 7. Configuring proxy groups and rules

Use the interactive configurator to manage your subscription URL, proxy groups, rules, ports, API secret, and dashboard choice:

```bash
sudo python3 pimiproxy-core.py configure
```

### Configure mode menu

| Option | Description |
|--------|-------------|
| 1) Manage subscription URL | Add or change your Clash/Mihomo API URL |
| 2) Manage proxy groups | Add or remove proxy groups |
| 3) Manage rules | Add, remove, or reorder routing rules |
| 4) Show current config | Print the live config.yaml to screen |
| 5) Refresh subscription now | Re-fetch server list and restart Mihomo |
| 6) Change API secret | Update the dashboard API password |
| 7) Change ports | Change dashboard port, API port, or proxy port |
| 8) Switch dashboard | Swap between Metacubexd and Zashboard |
| 9) Save and apply | Write changes and optionally restart containers |
| 10) Exit without saving | Discard all changes |

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
sudo python3 pimiproxy-core.py restart mihomo
```

---

## 8. Routing the Pi's own traffic through the proxy

By default, only devices that have manually configured their proxy settings use pimiproxy. The Pi itself sends its own traffic directly to the internet, bypassing Mihomo entirely.

To make the Pi route its own traffic through Mihomo:

```bash
sudo clash-system on
```

To revert:

```bash
sudo clash-system off
```

To check current state:

```bash
sudo clash-system status
```

To turn off and immediately back on (useful after config changes):

```bash
sudo clash-system restart
```

**Note:** `clash-system on` uses Linux policy routing rules and the `mihomo` TUN interface. These rules are not persistent across reboots — you would need to add `clash-system on` to a startup script (e.g. `/etc/rc.local` or a systemd unit) if you want it permanent.

---

## 9. Day-to-day management

### Check status

```bash
sudo python3 pimiproxy-core.py status
```

Shows container health, subscription info (URL and number of proxies loaded), clash-system routing state, and runs a live HTTP and HTTPS connectivity test through the proxy with latency results.

### View logs

```bash
sudo python3 pimiproxy-core.py logs              # Mihomo logs (default)
sudo python3 pimiproxy-core.py logs dashboard    # Dashboard logs
```

Press Ctrl-C to stop the log stream.

### Restart containers

```bash
sudo python3 pimiproxy-core.py restart           # Restart all containers
sudo python3 pimiproxy-core.py restart mihomo    # Restart Mihomo only
sudo python3 pimiproxy-core.py restart dashboard # Restart dashboard only
```

### Update to latest images

```bash
sudo python3 pimiproxy-core.py update
```

Pulls the latest Docker images for all containers and recreates them. Safe to run at any time.

### Refresh subscription (manual)

The installer creates a daily cron job (`/etc/cron.daily/pimiproxy-refresh`) that automatically re-fetches your provider's server list and restarts Mihomo. To trigger a refresh manually:

```bash
sudo python3 pimiproxy-core.py refresh
```

### Change hostname

```bash
sudo python3 pimiproxy-core.py hostname <new-hostname>
```

Updates the system hostname, automatically restarts avahi-daemon so `.local` resolves immediately, and prompts you to reboot so the router registers the new `.lan` name via DHCP.

### Edit config directly

```bash
sudo nano /opt/pimiproxy/config/config.yaml
sudo python3 pimiproxy-core.py restart mihomo
```

---

## 10. Uninstalling

```bash
sudo python3 pimiproxy-core.py uninstall
```

The uninstaller will:

1. Stop and remove all containers
2. Remove the Mihomo, Metacubexd, and Zashboard Docker images
3. Clear any active routing rules (`clash-system off`)
4. Delete `/opt/pimiproxy` and all contents
5. Remove `/usr/local/bin/clash-system`
6. Remove the daily subscription refresh cron job (`/etc/cron.daily/pimiproxy-refresh`)
7. Optionally reset the hostname back to `raspberrypi`
8. Optionally remove avahi-daemon

---

## 11. Troubleshooting

### Check if everything is working

```bash
sudo python3 pimiproxy-core.py status
```

This is the first thing to run — it shows container state, tests the proxy with a real HTTP and HTTPS request, and prints all endpoint URLs in one place.

### Dashboard won't load

- Confirm the containers are running: `sudo python3 pimiproxy-core.py status`
- Try the IP address instead of the hostname: `http://10.0.0.240:8080`
- Check firewall rules aren't blocking the port

### Hostname doesn't resolve

- **`.lan`** resolves via your router's DNS once the Pi renews its DHCP lease. The fastest way to trigger this is to reboot (`sudo reboot`) or run `sudo python3 pimiproxy-core.py hostname <current-hostname>` which will prompt you to reboot.
- **`.local`** resolves via mDNS (avahi). It can take up to 30 seconds to propagate after first boot. Some Android versions don't support `.local` — use `.lan` or the IP address instead.
- Confirm avahi-daemon is running: `systemctl status avahi-daemon`
- If neither resolves, use the IP address directly.

### Proxy not working on a device

- Run `sudo python3 pimiproxy-core.py status` — the proxy test will show exactly where it fails
- Confirm you set proxy settings, not network/WiFi settings
- Confirm the port is `7890`, not `9090` (that's the API, not the proxy)
- Test from the Pi itself: `curl -x http://localhost:7890 https://example.com`
- Check Mihomo logs: `sudo python3 pimiproxy-core.py logs`

### `clash-system on` has no effect

- Confirm the mihomo container is running: `sudo python3 pimiproxy-core.py status`
- Confirm the TUN interface exists: `ip link show mihomo`
- If the interface is missing, the container may not have started with the TUN device — check logs: `sudo python3 pimiproxy-core.py logs`

### Install fails with a Docker error

- Confirm Docker is running: `systemctl status docker`
- If using offline archives, confirm the `.tar.gz` files are in the same directory as `pimiproxy-core.py`
- Try pulling manually to see the error: `docker pull --platform linux/arm64 ghcr.io/metacubex/mihomo:latest`

### Subscription URL returns no proxies

Some provider APIs only respond correctly when the client sends a Clash-compatible `User-Agent` header. Use the built-in `debug` subcommand to inspect the raw response:

```bash
sudo python3 pimiproxy-core.py debug
```

Paste your provider URL when prompted (or pass it directly as an argument). The command sends a request with `User-Agent: Clash-Verge/1.0 (clash-verge-rev; mihomo)`, reports the status code, content type, and how many proxies/groups/rules were found in the response, then saves the full response to `raw_debug_output.yaml` in the current directory.

If the response contains proxies, re-run the installer or use option 1 → option 5 in `configure` to refresh. If it doesn't, the URL or token is wrong for Clash clients — check with your provider.

### Changing ports or API secret after install

Use the configure menu — no need to reinstall:

```bash
sudo python3 pimiproxy-core.py configure
```

- Option 6 to change the API secret
- Option 7 to change any port
- Option 8 to switch dashboard
- Option 9 to save and apply
