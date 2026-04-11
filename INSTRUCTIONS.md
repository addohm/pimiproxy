# pimiproxy — Installation & Usage Instructions

---

## Table of contents

- [pimiproxy — Installation \& Usage Instructions](#pimiproxy--installation--usage-instructions)
  - [Table of contents](#table-of-contents)
  - [1. Before you begin](#1-before-you-begin)
  - [2. Preparing offline image archives (optional)](#2-preparing-offline-image-archives-optional)
  - [3. Running the installer](#3-running-the-installer)
  - [4. Installer prompts explained](#4-installer-prompts-explained)
    - [Install directory](#install-directory)
    - [Hostname](#hostname)
    - [Dashboard port](#dashboard-port)
    - [Mihomo API port](#mihomo-api-port)
    - [Mixed proxy port](#mixed-proxy-port)
    - [API secret](#api-secret)
    - [Clash API subscription URL](#clash-api-subscription-url)
    - [Dashboard choice](#dashboard-choice)
  - [5. Connecting devices to the proxy](#5-connecting-devices-to-the-proxy)
    - [Windows](#windows)
    - [macOS](#macos)
    - [iPhone / iPad](#iphone--ipad)
    - [Android](#android)
    - [Linux (system-wide)](#linux-system-wide)
  - [6. Accessing the dashboard](#6-accessing-the-dashboard)
  - [7. Configuring an upstream proxy or VPN](#7-configuring-an-upstream-proxy-or-vpn)
  - [8. Routing the Pi's own traffic through the proxy](#8-routing-the-pis-own-traffic-through-the-proxy)
  - [9. Day-to-day management](#9-day-to-day-management)
    - [Start / stop / restart containers](#start--stop--restart-containers)
    - [View logs](#view-logs)
    - [Check container status](#check-container-status)
    - [Update images](#update-images)
    - [Edit config](#edit-config)
  - [10. Uninstalling](#10-uninstalling)
  - [11. Troubleshooting](#11-troubleshooting)
    - [Dashboard won't load](#dashboard-wont-load)
    - [`pimiproxy.local` doesn't resolve](#pimiproxylocal-doesnt-resolve)
    - [Proxy not working on a device](#proxy-not-working-on-a-device)
    - [`clash-system on` has no effect](#clash-system-on-has-no-effect)
    - [Install fails with a Docker error](#install-fails-with-a-docker-error)

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

Then copy the `.tar.gz` files to the Pi into the same directory as `pimiproxy.sh`:

```bash
scp mihomo.tar.gz metacubexd.tar.gz zashboard.tar.gz user@pimiproxy.local:~/pi-proxy/
```

If the archives are present in the same directory as the script when you run it, they will be loaded from disk instead of pulled from the internet.

---

## 3. Running the installer

SSH into your Pi (or open a terminal on it directly), navigate to the directory containing `pimiproxy.sh`, and run:

```bash
sudo bash pimiproxy.sh install
```

The script will walk you through a series of prompts. Press **Enter** at each one to accept the default value shown in brackets.

To uninstall later:

```bash
sudo bash pimiproxy.sh uninstall
```

---

## 4. Installer prompts explained

### Install directory
**Default: `/opt/pimiproxy`**

Where all pimiproxy files live — the Docker Compose file, Mihomo config, and install metadata. Leave this as the default unless you have a specific reason to change it.

### Hostname
**Default: `pimiproxy`**

Sets the Pi's system hostname. After install the Pi will be reachable on the LAN as `pimiproxy.local` via mDNS. Your router may also advertise it as `pimiproxy.lan` depending on your router's DNS settings — that is router-controlled and separate from what this installer does.

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

If you leave it blank, the proxy will still run but will pass all traffic through directly (no upstream provider). You can add a subscription later by editing `/opt/pimiproxy/config/config.yaml`.

### Dashboard choice
**Default: `1` (Metacubexd)**

Two dashboard options are available:

| Option | Description |
|--------|-------------|
| **Metacubexd** | Clean, standard dashboard. Good for most users. |
| **Zashboard** | More detailed, power-user focused. More controls and stats. |

Both connect to the same Mihomo instance. This is cosmetic — you can switch later by re-running the installer.

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
http://pimiproxy.local:8080
```

If `.local` doesn't resolve (can take ~30 seconds on first boot, and some Android devices don't support mDNS), use the IP directly:

```
http://10.0.0.240:8080
```

On first load the dashboard will ask for the API backend address and secret:

- **API URL:** `http://pimiproxy.local:9090` (or `http://10.0.0.240:9090`)
- **Secret:** `pimiproxy` (or whatever you set during install)

The dashboard shows live connections, traffic speed, active rules, and lets you switch proxy modes and groups without restarting Mihomo.

---

## 7. Configuring an upstream proxy or VPN

Out of the box, pimiproxy passes all traffic through directly — it is a proxy server, but with no upstream provider configured. To actually route traffic through a VPN or proxy service, you need to edit the Mihomo config.

```bash
sudo nano /opt/pimiproxy/config/config.yaml
```

Add your provider's details under `proxies`, configure `proxy-groups`, and write `rules` to control which traffic goes where. The [Mihomo configuration wiki](https://wiki.metacubex.one) is the full reference.

After editing, apply the changes:

```bash
cd /opt/pimiproxy && docker compose restart mihomo
```

Or use the dashboard — it can reload config without a full restart.

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

To toggle off and back on (useful after config changes):

```bash
sudo clash-system restart
```

**Note:** `clash-system on` uses Linux policy routing rules and the `mihomo` TUN interface. These rules are not persistent across reboots — you would need to add `clash-system on` to a startup script (e.g. `/etc/rc.local` or a systemd unit) if you want it permanent.

---

## 9. Day-to-day management

### Start / stop / restart containers

```bash
cd /opt/pimiproxy

docker compose start        # start stopped containers
docker compose stop         # stop without removing
docker compose restart      # restart all
docker compose restart mihomo   # restart only the engine
```

### View logs

```bash
cd /opt/pimiproxy
docker compose logs -f mihomo       # live Mihomo logs
docker compose logs -f dashboard    # dashboard logs
```

### Check container status

```bash
docker compose ps
```

### Update images

```bash
cd /opt/pimiproxy
docker compose pull
docker compose up -d
```

### Edit config

```bash
sudo nano /opt/pimiproxy/config/config.yaml
cd /opt/pimiproxy && docker compose restart mihomo
```

---

## 10. Uninstalling

```bash
sudo bash pimiproxy.sh uninstall
```

The uninstaller will:

1. Stop and remove all containers
2. Remove the Mihomo, Metacubexd, and Zashboard Docker images
3. Clear any active routing rules (`clash-system off`)
4. Delete `/opt/pimiproxy` and all contents
5. Remove `/usr/local/bin/clash-system`
6. Optionally reset the hostname back to `raspberrypi`
7. Optionally remove avahi-daemon

---

## 11. Troubleshooting

### Dashboard won't load

- Confirm the containers are running: `cd /opt/pimiproxy && docker compose ps`
- Try the IP address instead of the hostname: `http://10.0.0.240:8080`
- Check firewall rules aren't blocking the port

### `pimiproxy.local` doesn't resolve

- mDNS can take up to 30 seconds to propagate after first boot
- Some Android versions don't support `.local` mDNS — use the IP address
- Confirm avahi-daemon is running: `systemctl status avahi-daemon`

### Proxy not working on a device

- Confirm you set proxy settings, not network/WiFi settings
- Confirm the port is `7890`, not `9090` (that's the API, not the proxy)
- Test from the Pi itself: `curl -x http://localhost:7890 https://example.com`
- Check Mihomo logs: `cd /opt/pimiproxy && docker compose logs -f mihomo`

### `clash-system on` has no effect

- Confirm the mihomo container is running: `docker ps | grep mihomo`
- Confirm the TUN interface exists: `ip link show mihomo`
- If the interface is missing, the container may not have started with the TUN device — check logs

### Install fails with a Docker error

- Confirm Docker is running: `systemctl status docker`
- If using offline archives, confirm the `.tar.gz` files are in the same directory as `pimiproxy.sh`
- Try pulling manually to see the error: `docker pull --platform linux/arm64 ghcr.io/metacubex/mihomo:latest`