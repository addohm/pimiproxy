# pimiproxy

A self-hosted proxy server for your home network, running on a Raspberry Pi. pimiproxy uses [Mihomo](https://github.com/MetaCubeX/mihomo) (the Clash.Meta engine) inside Docker, with a web dashboard for monitoring and control.

Once installed, any device on your network can route its traffic through the Pi by pointing its proxy settings at `<pi-ip>:7890`. No VPN client, no app — just a standard HTTP/SOCKS5 proxy.

---

## What it does

- Runs Mihomo (Clash.Meta engine) in a Docker container on the Pi
- Exposes a mixed HTTP/SOCKS5 proxy port for other devices to use
- Provides a web dashboard (Metacubexd or Zashboard) for live traffic monitoring and config control
- Optionally routes the Pi's own traffic through the proxy via `clash-system`
- Sets up mDNS so the Pi is reachable as `pimiproxy.local` on the LAN

## What it does not do

- It does not configure any upstream proxy or VPN out of the box — you bring your own provider and add it to `config.yaml`
- It does not touch your router — other devices must be configured manually to use the proxy
- It does not intercept LAN traffic automatically — devices opt in via their proxy settings

---

## Requirements

- Raspberry Pi (any model with 64-bit OS support recommended)
- Raspberry Pi OS Bookworm or Bullseye, or Debian 11/12
- Internet access (for pulling Docker images if not using offline archives)
- Root / sudo access

---

## Project files

| File | Purpose |
|------|---------|
| `pimiproxy-bootstrap.sh` | One-command setup — installs all prerequisites then launches pimiproxy-core.py |
| `pimiproxy-core.py` | Main install / uninstall / configure script |
| `mihomo.tar.gz` | *(optional)* Pre-pulled Mihomo image for offline install |
| `metacubexd.tar.gz` | *(optional)* Pre-pulled Metacubexd image for offline install |
| `zashboard.tar.gz` | *(optional)* Pre-pulled Zashboard image for offline install |

The `.tar.gz` archives are not included in the repository. See [INSTRUCTIONS.md](INSTRUCTIONS.md) for how to prepare them.

---

## Quick start

**Option A — Bootstrap (recommended, handles all prerequisites):**

```bash
sudo bash pimiproxy-bootstrap.sh
```

This detects your distro, installs Python, Docker, avahi-daemon, and raspi-config if needed, then launches the installer automatically.

**Option B — Python script directly (if prerequisites are already installed):**

```bash
sudo python3 pimiproxy-core.py install
```

Press Enter through all prompts to accept defaults. See [INSTRUCTIONS.md](INSTRUCTIONS.md) for the full walkthrough.

---

## Default settings

| Setting | Default |
|---------|---------|
| Install directory | `/opt/pimiproxy` |
| Hostname | `pimiproxy` |
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
http://pimiproxy.local:8080
```

Or by IP:

```
http://10.0.0.240:8080
```

The API secret (default: `pimiproxy`) may be required on first load depending on the dashboard.

---

## Uninstalling

```bash
sudo python3 pimiproxy-core.py uninstall
```

This stops containers, removes images, deletes all installed files, clears routing rules, and optionally resets the hostname and removes avahi-daemon.

---

## Configuring proxy groups and rules

After install, run the interactive configurator to manage your subscription, proxy groups, and routing rules without editing YAML by hand:

```bash
sudo python3 pimiproxy-core.py configure
```

From there you can:
- Add or change your Clash API subscription URL
- Add proxy groups (select, url-test, fallback, load-balance)
- Add, remove, and reorder rules
- Apply common rule presets (LAN bypass, China GEOIP, catch-all)
- Preview the generated config before saving
- Restart Mihomo automatically after saving

---

## After install: next steps

The default config passes all traffic through directly (`MATCH,DIRECT`). To actually proxy traffic through an upstream provider, run `sudo python3 pimiproxy-core.py configure` to add proxy groups and rules interactively, or edit `/opt/pimiproxy/config/config.yaml` directly. Then restart Mihomo:

```bash
cd /opt/pimiproxy && docker compose restart mihomo
```

See the [Mihomo documentation](https://wiki.metacubex.one) for config reference.

---

## License

MIT