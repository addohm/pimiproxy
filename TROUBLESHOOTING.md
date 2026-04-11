# Troubleshooting

---

## Table of contents

1. [Mihomo on Raspberry Pi: High Latency vs Other Devices](#1-mihomo-on-raspberry-pi-high-latency-vs-other-devices)
2. [Subscription URL: No Proxies Loaded](#2-subscription-url-no-proxies-loaded)
3. [DNS Port 53 Conflict](#3-dns-port-53-conflict)
4. [Mihomo Container Won't Start or Keeps Restarting](#4-mihomo-container-wont-start-or-keeps-restarting)
5. [Port Already in Use](#5-port-already-in-use)
6. [clash-system on Has No Effect](#6-clash-system-on-has-no-effect)
7. [Pi's Own Traffic Not Going Through the Proxy](#7-pis-own-traffic-not-going-through-the-proxy)
8. [Daily Subscription Refresh Not Working](#8-daily-subscription-refresh-not-working)
9. [Dashboard Can't Reach the Mihomo API](#9-dashboard-cant-reach-the-mihomo-api)
10. [Recovering a Broken Config](#10-recovering-a-broken-config)
11. [Devices on the Network Can't Connect to the Proxy](#11-devices-on-the-network-cant-connect-to-the-proxy)

---

## 1. Mihomo on Raspberry Pi: High Latency vs Other Devices

**Symptom:** Mihomo reports significantly higher latency on a Raspberry Pi than on another device (e.g., laptop) on the same WiFi network when connecting to the same server.

### Likely Causes (in order of probability)

**1. WiFi Band Mismatch**
The Pi may be connecting to 2.4 GHz while other devices use 5 GHz. 2.4 GHz has higher congestion and inherently higher latency.

Check which band the Pi is using:
```bash
iw dev wlan0 link
```
Look for the `freq:` field — 2.4 GHz frequencies are in the 2400–2484 MHz range; 5 GHz is 5180–5825 MHz.

**2. No Hardware AES Acceleration (CPU-bound crypto)**
Older Pi models (Pi 3, Zero) lack hardware AES-NI. Mihomo's TLS encryption is handled entirely in software, adding CPU overhead that shows up as latency.

Check for AES hardware support:
```bash
grep aes /proc/cpuinfo
```
No output means no hardware acceleration. Pi 4 and Pi 5 have AES-NI.

**3. Weak WiFi Signal / Low Link Rate**
The Pi's onboard WiFi antenna is small and often achieves a lower link rate than a laptop even at the same physical location.

Check the negotiated link rate:
```bash
iw dev wlan0 link | grep "tx bitrate"
```
Compare this to the rate on your laptop.

**4. Slow DNS Resolution**
If mihomo performs DNS lookups during latency tests, a slower upstream DNS resolver on the Pi will inflate results. Verify the DNS server configured in mihomo's config and consider using a fast resolver like `1.1.1.1` or `8.8.8.8`.

### Isolating the Issue

Run a raw ping to the proxy server's IP to determine whether the latency is at the network level or within mihomo:
```bash
ping -c 10 <proxy-server-ip>
```
- If raw ping is also ~3x higher on the Pi → WiFi or hardware issue, not mihomo config.
- If raw ping is similar but mihomo latency is high → crypto overhead or DNS.

### Resolution Options

- **Switch to 5 GHz WiFi** if your router supports it and the Pi model does too (Pi 3B+ and newer support 5 GHz).
- **Use a USB WiFi adapter** with better antenna and 5 GHz support.
- **Use Ethernet** to eliminate WiFi as a variable entirely.
- **Upgrade to Pi 4 or Pi 5** if crypto overhead is the bottleneck.

---

## 2. Subscription URL: No Proxies Loaded

**Symptom:** The installer reports "No proxies: block found in subscription response" or Mihomo starts but has no servers to choose from.

### Cause A — Provider requires a Clash-compatible User-Agent

Many subscription APIs gate their response behind a `User-Agent` check and return a login page or error to unrecognised clients. Use `getapiconfig.py` to test the raw response your URL returns:

```bash
python3 getapiconfig.py
```

Paste your URL when prompted. The script sends the request with `User-Agent: Clash-Verge/1.0 (clash-verge-rev; mihomo)` and saves the full response to `raw_debug_output.yaml`. Open that file and check whether it contains a `proxies:` block. If the file contains HTML or a plain-text error message, the URL is wrong or the provider is rejecting the request.

### Cause B — URL is not a Clash API URL

A valid Clash/Mihomo subscription URL returns YAML beginning with a `proxies:` list. It is **not**:
- A WireGuard or OpenVPN config file
- A QR code link
- An app download URL

If your provider offers multiple export formats, look specifically for "Clash", "Mihomo", or "Meta" as the client type.

### Cause C — GEOIP-only ruleset

pimiproxy automatically strips `GEOIP,` rules from provider configs because they require the MaxMind MMDB database, which Mihomo downloads from GitHub on first run. On networks that can't reach GitHub this causes a startup crash. The stripped GEOIP rules are redundant when the provider already includes `IP-CIDR` entries for those regions — which most providers do. If you need GEOIP rules, add them manually via `configure` after install.

### Cause D — Fetch failed silently during install

If the subscription fetch failed at install time, Mihomo starts with no proxies. Re-fetch manually:

```bash
sudo python3 pimiproxy-core.py refresh
```

---

## 3. DNS Port 53 Conflict

**Symptom:** Mihomo container fails to start; logs contain `bind: address already in use` on port 53.

### Cause

Mihomo's config sets DNS to listen on `0.0.0.0:53`. On many modern Debian/Ubuntu systems, `systemd-resolved` already occupies port 53.

### Resolution

Check what's holding port 53:

```bash
sudo ss -tulpn | grep ':53'
```

If `systemd-resolved` is listed, disable its stub listener:

```bash
sudo nano /etc/systemd/resolved.conf
```

Set or add:
```ini
[Resolve]
DNSStubListener=no
```

Then restart resolved and the containers:

```bash
sudo systemctl restart systemd-resolved
cd /opt/pimiproxy && docker compose restart
```

Alternatively, if you don't need Mihomo to handle DNS at all (you're using it only as an HTTP/SOCKS5 proxy for configured devices, not for transparent proxying), you can edit `/opt/pimiproxy/config/config.yaml` and change:
```yaml
dns:
  enable: false
```
Then restart Mihomo.

---

## 4. Mihomo Container Won't Start or Keeps Restarting

**Symptom:** `docker compose ps` shows mihomo as `Restarting` or `Exited`.

### Step 1 — Check the logs

```bash
cd /opt/pimiproxy && docker compose logs --tail=50 mihomo
```

### Common log errors and fixes

| Log message | Cause | Fix |
|-------------|-------|-----|
| `bind: address already in use` on port 53 | systemd-resolved conflict | See [section 3](#3-dns-port-53-conflict) |
| `bind: address already in use` on port 7890/9090 | Port conflict | See [section 5](#5-port-already-in-use) |
| `failed to get GeoIP data` or MMDB errors | MaxMind database not yet downloaded | Wait ~60s after first start; or check network access to GitHub |
| `no such file or directory` for config | Config path misconfigured | Confirm `/opt/pimiproxy/config/config.yaml` exists |
| Container exits immediately with no log | Privileged mode blocked | Confirm Docker daemon is running as root and the container spec has `privileged: true` |

### Step 2 — Confirm the compose file is intact

```bash
cat /opt/pimiproxy/docker-compose.yml
```

The mihomo service should have `network_mode: host` and `privileged: true`. If either is missing, re-run the installer to regenerate it:

```bash
sudo python3 pimiproxy-core.py install
```

---

## 5. Port Already in Use

**Symptom:** A container fails to start with `bind: address already in use` on port 7890, 8080, or 9090.

### Identify the conflicting process

```bash
sudo ss -tulpn | grep -E ':(7890|8080|9090)'
```

### Options

1. **Change the pimiproxy port** — run `sudo python3 pimiproxy-core.py configure`, but note that port changes require editing the compose file and metadata manually or re-running install with a different port.

2. **Stop the conflicting service** — identify the process from the `ss` output and stop or disable it.

3. **Re-run install with a different port** — if pimiproxy isn't yet in use, just re-run:
   ```bash
   sudo python3 pimiproxy-core.py install
   ```
   and enter a different port at the relevant prompt.

---

## 6. clash-system on Has No Effect

**Symptom:** `sudo clash-system on` runs without error but the Pi's own traffic still goes directly to the internet.

### Check 1 — Is the mihomo TUN interface up?

```bash
ip link show mihomo
```

If the interface doesn't exist, the mihomo container isn't running or didn't create the TUN device. Check container status and logs:

```bash
cd /opt/pimiproxy && docker compose ps
docker compose logs --tail=30 mihomo
```

### Check 2 — Are the policy routing rules active?

```bash
ip rule show
```

You should see rules at priorities 90, 100, and 101. If they're absent, `clash-system on` failed silently. Re-run it and look for error output:

```bash
sudo clash-system on
```

### Check 3 — Is routing table 101 populated?

```bash
ip route show table 101
```

You should see a default route via the `mihomo` interface. If the table is empty, the TUN interface was down when `clash-system on` ran.

### Fix

Ensure the container is fully started before running `clash-system on`:

```bash
cd /opt/pimiproxy && docker compose up -d
sleep 5
sudo clash-system on
sudo clash-system status
```

---

## 7. Pi's Own Traffic Not Going Through the Proxy

**Symptom:** `clash-system status` shows ON but traffic from the Pi itself (e.g., `curl ifconfig.me`) doesn't appear to use the proxy.

### Note on persistence

`clash-system on` sets Linux policy routing rules that are **not persistent across reboots**. After a reboot, the rules are gone.

To make it permanent, add `clash-system on` to a startup script. The simplest approach on a Pi is `/etc/rc.local`:

```bash
sudo nano /etc/rc.local
```

Add before the `exit 0` line:
```bash
sleep 10 && clash-system on
```

The `sleep 10` gives the Docker container time to start and create the TUN interface before the routing rules are applied.

Alternatively, create a systemd unit:

```bash
sudo nano /etc/systemd/system/clash-system.service
```

```ini
[Unit]
Description=Enable clash-system transparent proxy routing
After=docker.service
Wants=docker.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 10
ExecStart=/usr/local/bin/clash-system on
RemainAfterExit=yes
ExecStop=/usr/local/bin/clash-system off

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now clash-system
```

---

## 8. Daily Subscription Refresh Not Working

**Symptom:** Server list becomes stale; providers often rotate servers and expire old ones. The automatic daily refresh appears not to run.

### Check 1 — Does the cron file exist and is it executable?

```bash
ls -l /etc/cron.daily/pimiproxy-refresh
```

It should be owned by root and have execute permission (`-rwxr-xr-x`). If not:

```bash
sudo chmod 755 /etc/cron.daily/pimiproxy-refresh
```

### Check 2 — Is a subscription URL configured?

```bash
sudo python3 pimiproxy-core.py configure
```

Choose option 1 to check the current subscription URL. If it's blank, the refresh script will exit immediately without fetching anything.

### Check 3 — Run the refresh manually to see errors

```bash
sudo python3 pimiproxy-core.py refresh
```

This is the same command the cron job runs. Any fetch errors will print to the terminal.

### Check 4 — Confirm cron is running on the system

```bash
sudo systemctl status cron
# or on some systems:
sudo systemctl status crond
```

---

## 9. Dashboard Can't Reach the Mihomo API

**Symptom:** Dashboard loads but shows a connection error when trying to reach the backend, or prompts for an API URL and secret repeatedly.

### Check the API URL format

On first load the dashboard asks for the API backend. The correct values with default settings are:

| Field | Value |
|-------|-------|
| API URL | `http://<pi-ip>:9090` |
| Secret | `pimiproxy` (or whatever was set during install) |

Do **not** use `https://` — Mihomo's API runs plain HTTP. Do **not** use the dashboard port (8080) — that's the frontend, not the API.

### Confirm the API port is reachable

```bash
curl -s http://localhost:9090/version
```

Expected output: `{"version":"...","premium":true}`. If this returns nothing or an error, Mihomo is not running or is bound to a different port. Check the config:

```bash
grep external-controller /opt/pimiproxy/config/config.yaml
```

### Confirm the secret

```bash
grep secret /opt/pimiproxy/config/config.yaml
```

The dashboard secret must match exactly. It is case-sensitive.

---

## 10. Recovering a Broken Config

**Symptom:** `config.yaml` was manually edited and broke Mihomo, or the file is missing.

pimiproxy keeps a structured copy of the full configuration in `/opt/pimiproxy/.pimiproxy-config.json`. As long as that file is intact, you can regenerate `config.yaml` from it by running configure and saving:

```bash
sudo python3 pimiproxy-core.py configure
```

Choose option 6 (Save and apply) — this rewrites `config.yaml` from the JSON source and restarts Mihomo.

If `.pimiproxy-config.json` is also missing or corrupt, you can re-run the full installer. It will overwrite the existing install with fresh prompts:

```bash
sudo python3 pimiproxy-core.py install
```

To inspect what pimiproxy thinks the current config is without making changes:

```bash
sudo python3 pimiproxy-core.py configure
# Choose option 4 (Show current config)
# Choose option 7 (Exit without saving)
```

---

## 11. Devices on the Network Can't Connect to the Proxy

**Symptom:** You've configured proxy settings on a device but connections fail or the device shows "proxy unreachable".

### Check 1 — Confirm the correct port

The proxy port is **7890** (default). Port 9090 is the Mihomo API — sending proxy traffic there will not work.

### Check 2 — Test from the Pi itself

```bash
curl -x http://localhost:7890 https://example.com
```

If this fails, Mihomo is not running or the mixed-port is wrong. Check:

```bash
grep mixed-port /opt/pimiproxy/config/config.yaml
cd /opt/pimiproxy && docker compose ps
```

### Check 3 — Confirm allow-lan is enabled

```bash
grep allow-lan /opt/pimiproxy/config/config.yaml
```

Should be `allow-lan: true`. If it's `false`, other devices cannot connect.

### Check 4 — Firewall

Raspberry Pi OS doesn't enable a firewall by default, but if `ufw` or `iptables` rules have been added:

```bash
sudo ufw status
sudo iptables -L INPUT -n | grep 7890
```

If port 7890 is blocked, allow it:

```bash
sudo ufw allow 7890/tcp
```

### Check 5 — Confirm the Pi's IP hasn't changed

If the Pi's IP address changed since you configured the proxy settings on the device, the device will be pointing at the wrong host. Set a static IP or DHCP reservation on your router for the Pi's MAC address to prevent this.
