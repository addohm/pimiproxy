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
| `pimiproxy-core.py` | Main installer: `install`, `uninstall`, `configure`, `refresh` subcommands |
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

## Uninstalling

```bash
sudo python3 pimiproxy-core.py uninstall
```

This stops containers, removes images, deletes all installed files, clears routing rules, and optionally resets the hostname and removes avahi-daemon.

---

## Subscription management

If you provided a Clash/Mihomo-compatible subscription URL at install time, pimiproxy fetches it during install and extracts the proxy list, proxy groups, and routing rules directly from the provider's config. The result is written into `config.yaml` — no separate provider fetch happens at Mihomo startup.

The subscription is refreshed automatically each night via a cron job. To refresh manually at any time:

```bash
sudo python3 /opt/pimiproxy/pimiproxy-core.py refresh
```

This re-fetches the URL, rebuilds `config.yaml`, and restarts Mihomo.

---

## Configuring proxy groups and rules

Run the interactive configurator to manage your subscription URL, proxy groups, and routing rules:

```bash
sudo python3 pimiproxy-core.py configure
```

From there you can:
- Add or change your Clash API subscription URL
- Refresh the subscription immediately
- Add proxy groups (select, url-test, fallback, load-balance)
- Add, remove, and reorder rules
- Apply common rule presets (LAN bypass, catch-all)
- Preview the generated config before saving
- Restart Mihomo automatically after saving

---

## After install: next steps

If you provided a subscription URL at install time, your proxy groups and rules are already configured — open the dashboard and select a server in the proxy group.

If you skipped the subscription prompt, the default config passes all traffic through directly (`MATCH,DIRECT`). Add a subscription later:

```bash
sudo python3 /opt/pimiproxy/pimiproxy-core.py configure
```

Choose option 1 to set the URL, then option 5 to fetch it immediately.

To restart Mihomo manually after editing `/opt/pimiproxy/config/config.yaml`:

```bash
cd /opt/pimiproxy && docker compose restart mihomo
```

See the [Mihomo documentation](https://wiki.metacubex.one) for config reference.

---

## License

MIT