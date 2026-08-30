# Troubleshooting

---

## Table of contents

0. [Start Here: `pimiproxy doctor`](#0-start-here-pimiproxy-doctor)
1. [Mihomo: High Latency vs Other Devices](#1-mihomo-high-latency-vs-other-devices)
2. [Subscription URL: No Proxies Loaded](#2-subscription-url-no-proxies-loaded)
3. [DNS Port 53 Conflict](#3-dns-port-53-conflict)
4. [Mihomo Container Won't Start or Keeps Restarting](#4-mihomo-container-wont-start-or-keeps-restarting)
5. [Port Already in Use](#5-port-already-in-use)
6. [Daily Subscription Refresh Not Working](#6-daily-subscription-refresh-not-working)
7. [Dashboard Can't Reach the Mihomo API](#7-dashboard-cant-reach-the-mihomo-api)
8. [Recovering a Broken Config](#8-recovering-a-broken-config)
9. [Devices on the Network Can't Connect to the Proxy](#9-devices-on-the-network-cant-connect-to-the-proxy)
10. [App Shows No Connectivity Despite Proxy Being Configured](#10-app-shows-no-connectivity-despite-proxy-being-configured)
11. [`clash-system off` Doesn't Bypass the Proxy](#11-clash-system-off-doesnt-bypass-the-proxy)
12. [`update` Says "No archive or source found for pimiproxy-api — skipping"](#12-update-says-no-archive-or-source-found-for-pimiproxy-api--skipping)

---

## 0. Start Here: `pimiproxy doctor`

Before working through any section below, run:

```bash
sudo pimiproxy doctor
```

It walks the entire pipeline in order and, for every problem it finds, prints
plain-language, numbered fix steps. It is read-only — safe to run at any time.
What it checks, and the real-world failure each check catches:

| Check | Catches |
|---|---|
| Proxy engine answering | dead/crash-looping mihomo container |
| Connectivity check (`generate_204`) | the exact probe phones/headsets use — failure = devices show "limited connection" and refuse downloads |
| Every group's selected server (delay test) | a group silently pointing at a dead provider server |
| DNS honesty (TLS-certificate verification of local answers) | GFW DNS poisoning and ISP captive-check hijack — devices sent to forged servers |
| Meta Quest store endpoint (`graph.oculus.com` over TLS) | store loads failing while games still work |
| UDP through the proxy (NTP probe) | proxy servers that silently drop UDP — breaks clock sync, QUIC, casting; wrong clocks then break HTTPS everywhere |
| IPv6 leak (active router solicitation) | the router advertising IPv6, letting devices route (and resolve DNS!) straight past the proxy |

If doctor passes cleanly but a device still misbehaves, reboot the device and
toggle its Wi-Fi — client-side DNS and endpoint caches outlive server-side
fixes (Meta devices in particular cache endpoints across reboots).

---

## 1. Mihomo: High Latency vs Other Devices

**Symptom:** Mihomo reports significantly higher latency on the SBC than on another device (e.g., laptop) on the same WiFi network when connecting to the same server.

### Likely Causes (in order of probability)

**1. WiFi Band Mismatch**
The SBC may be connecting to 2.4 GHz while other devices use 5 GHz. 2.4 GHz has higher congestion and inherently higher latency.

Check which band the SBC is using:
```bash
iw dev wlan0 link
```
Look for the `freq:` field — 2.4 GHz frequencies are in the 2400–2484 MHz range; 5 GHz is 5180–5825 MHz.

**2. No Hardware AES Acceleration (CPU-bound crypto)**
Older Pi models (Pi 3, Zero) lack hardware AES-NI. Mihomo's TLS encryption is handled entirely in software, adding CPU overhead that shows up as latency.

Check for AES hardware support:
```bash
grep -i aes /proc/cpuinfo
```
Look for `aes` in the `Features` line. No match means no hardware acceleration.

**Pi 4 (BCM2711):** The Cortex-A72 cores in the BCM2711 do **not** include the optional ARMv8 cryptographic extensions. `aes` will never appear in the Features line regardless of firmware or kernel version — this is a hardware limitation of the chip, not a software issue. Even with a current kernel (6.12+) and up-to-date EEPROM, AES throughput will be ~50 MB/s (software).

**Pi 5 (BCM2712):** Has hardware AES. `aes pmull sha1 sha2` should appear in the Features line.

To confirm whether software or hardware AES is in use:

```bash
openssl speed -evp aes-128-cbc
```

- ~50 MB/s → software AES (no hardware crypto extensions)
- ~500+ MB/s → hardware AES (hardware crypto extensions present)

On a Pi 4, consider using **ChaCha20-Poly1305** instead of AES if your provider supports it — ChaCha20 uses NEON SIMD instructions (which the Pi 4 does have) and performs significantly faster than software AES.

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
- If raw ping is also ~3x higher on the SBC → WiFi or hardware issue, not mihomo config.
- If raw ping is similar but mihomo latency is high → crypto overhead or DNS.

### Resolution Options

- **Switch to 5 GHz WiFi** if your router and SBC both support it.
- **Use a USB WiFi adapter** with better antenna and 5 GHz support.
- **Use Ethernet** to eliminate WiFi as a variable entirely.
- **Upgrade to an SBC with hardware AES** (e.g. Raspberry Pi 5, Radxa Rock 5) if crypto overhead is the bottleneck.

---

## 2. Subscription URL: No Proxies Loaded

**Symptom:** The installer reports "No proxies: block found in subscription response" or Mihomo starts but has no servers to choose from.

### Cause A — Provider requires a Clash-compatible User-Agent

Many subscription APIs gate their response behind a `User-Agent` check and return a login page or error to unrecognised clients. Use the built-in `debug` subcommand to test the raw response your URL returns:

```bash
sudo ./pimiproxy debug
```

Paste your URL when prompted (or pass it directly as an argument). The command sends a request with `User-Agent: Clash-Verge/1.0 (clash-verge-rev; mihomo)`, reports the status code, content type, and how many proxies/groups/rules were found in the response, then saves the full response to `raw_debug_output.yaml`. Open that file and check whether it contains a `proxies:` block. If the file contains HTML or a plain-text error message, the URL is wrong or the provider is rejecting the request.

### Cause B — URL is not a Clash API URL

A valid Clash/Mihomo subscription URL returns YAML beginning with a `proxies:` list. It is **not**:
- A WireGuard or OpenVPN config file
- A QR code link
- An app download URL

If your provider offers multiple export formats, look specifically for "Clash", "Mihomo", or "Meta" as the client type.

### Cause C — GEOIP-only ruleset

pimiproxy automatically strips `GEOIP,` rules from provider configs because they require the MaxMind MMDB database, which Mihomo downloads from GitHub on first run. On networks that can't reach GitHub this causes a startup crash. The stripped GEOIP rules are redundant when the provider already includes `IP-CIDR` entries for those regions — which most providers do. If you need GEOIP rules, add them manually via `configure` after install.

### Cause D — Fetch failed or URL was skipped during install

The installer validates the provider URL at install time and checks that it contains proxies. If you skipped the provider prompt or the fetch failed, Mihomo starts with no proxies. Re-fetch manually:

```bash
sudo ./pimiproxy refresh
```

---

## 3. DNS Port 53 Conflict

**Symptom:** Mihomo container fails to start; logs contain `bind: address already in use` on port 53.

### Cause

Mihomo's config sets DNS to listen on `0.0.0.0:53`. On many modern Debian/Ubuntu/Fedora systems, `systemd-resolved` already occupies port 53.

> **Note:** The installer now automatically disables the systemd-resolved stub listener during install (on systems that have it). This section is only relevant if the conflict reappears after an OS update or manual change.

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
| `failed to get GeoIP data` or MMDB errors | Geo data files missing from config directory | Copy `geoip.metadb`, `geosite.dat`, `country.mmdb` from `docker-images/` to `/opt/pimiproxy/config/`, then restart Mihomo. The installer does this automatically; if upgrading an older install, re-run `sudo ./pimiproxy install` to trigger the copy. |
| `no such file or directory` for config | Config path misconfigured | Confirm `/opt/pimiproxy/config/config.yaml` exists |
| Container exits immediately with no log | Privileged mode blocked | Confirm Docker daemon is running as root and the container spec has `privileged: true` |

### Step 2 — Confirm the compose file is intact

```bash
cat /opt/pimiproxy/docker-compose.yml
```

The mihomo service should have `network_mode: host` and `privileged: true`. If either is missing, re-run the installer to regenerate it:

```bash
sudo ./pimiproxy install
```

---

## 5. Port Already in Use

**Symptom:** A container fails to start with `bind: address already in use` on port 7890, 8080, or 9090.

### Identify the conflicting process

```bash
sudo ss -tulpn | grep -E ':(7890|8080|9090)'
```

### Options

1. **Change the pimiproxy port** — run `sudo ./pimiproxy configure`, but note that port changes require editing the compose file and metadata manually or re-running install with a different port.

2. **Stop the conflicting service** — identify the process from the `ss` output and stop or disable it.

3. **Re-run install with a different port** — if pimiproxy isn't yet in use, just re-run:
   ```bash
   sudo ./pimiproxy install
   ```
   and enter a different port at the relevant prompt.

---

## 6. Daily Subscription Refresh Not Working

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
sudo ./pimiproxy configure
```

Choose option 1 to check the current subscription URL. If it's blank, the refresh script will exit immediately without fetching anything.

### Check 3 — Run the refresh manually to see errors

```bash
sudo ./pimiproxy refresh
```

This is the same command the cron job runs. Any fetch errors will print to the terminal.

### Check 4 — Confirm cron is running on the system

```bash
sudo systemctl status cron
# or on some systems:
sudo systemctl status crond
```

---

## 7. Dashboard Can't Reach the Mihomo API

**Symptom:** Dashboard loads but shows a connection error when trying to reach the backend, or prompts for an API URL and secret repeatedly.

### Check the API URL format

On first load the dashboard asks for the API backend. The correct values with default settings are:

| Field | Value |
|-------|-------|
| API URL | `http://<sbc-ip>:9090` |
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

## 8. Recovering a Broken Config

**Symptom:** `config.yaml` was manually edited and broke Mihomo, or the file is missing.

pimiproxy keeps a structured copy of the full configuration in `/opt/pimiproxy/.pimiproxy-config.json`. As long as that file is intact, you can regenerate `config.yaml` from it by running configure and saving:

```bash
sudo ./pimiproxy configure
```

Choose option 9 (Save and apply) — this rewrites `config.yaml` from the JSON source and restarts Mihomo.

If `.pimiproxy-config.json` is also missing or corrupt, you can re-run the full installer. It will overwrite the existing install with fresh prompts:

```bash
sudo ./pimiproxy install
```

To inspect what pimiproxy thinks the current config is without making changes:

```bash
sudo ./pimiproxy configure
# Choose option 4 (Show current config)
# Choose option 10 (Exit without saving)
```

---

## 9. Devices on the Network Can't Connect to the Proxy

**Symptom:** You've configured proxy settings on a device but connections fail or the device shows "proxy unreachable".

### Check 1 — Confirm the correct port

The proxy port is **7890** (default). Port 9090 is the Mihomo API — sending proxy traffic there will not work.

### Check 2 — Test locally

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

The installer automatically opens ports 7890, 8080, 8585, and 9090 in firewalld during install (on systems that use firewalld, such as Fedora). If you are using a different firewall or the rules were removed:

```bash
# Check firewalld
sudo firewall-cmd --list-ports

# Check ufw
sudo ufw status

# Check iptables directly
sudo iptables -L INPUT -n | grep 7890
```

If port 7890 is blocked, allow it:

```bash
# firewalld
sudo firewall-cmd --permanent --zone=public --add-port=7890/tcp && sudo firewall-cmd --reload

# ufw
sudo ufw allow 7890/tcp
```

### Check 5 — Confirm the SBC's IP hasn't changed

If the SBC's IP address changed since you configured the proxy settings on the device, the device will be pointing at the wrong host. Set a static IP or DHCP reservation on your router for the SBC's MAC address to prevent this.

---

## 10. App Shows No Connectivity Despite Proxy Being Configured

**Symptom:** A device is correctly configured to use pimiproxy as its HTTP/SOCKS5 proxy, other apps work fine, but one specific app shows "no internet" or "offline" — and the app never appears in Mihomo's logs at all.

### Why this happens

The standard pimiproxy setup is an **explicit proxy**: devices are told "send your traffic to `pimiproxy.lan:7890`". This only works for apps that respect the operating system's proxy setting. Apps built on standard HTTP clients (browsers, most apps) honour it automatically.

However, some apps — particularly games built on the **Unity engine** — use their own networking stack and bypass the OS proxy entirely. These apps send traffic directly to the router, skipping the SBC completely. On a network where direct internet access is blocked (e.g. behind a firewall or in a region with internet restrictions), this causes the app to report no connectivity even though the device and all other apps work fine.

**Real-world example:** On a Meta Quest 3 configured with `pimiproxy.lan:7890` as its WiFi proxy, Golf+ (Unity) works correctly because it uses Unity's default WebRequest, which respects the system proxy. Les Mills Bodycombat (also Unity) shows "offline" and makes zero connections through the proxy — because it uses its own HTTP client that ignores the system proxy entirely.

You can confirm this is the cause by checking Mihomo's logs while the app is open:

```bash
sudo ./pimiproxy logs
```

If the app generates zero log entries while other apps show activity, it is bypassing the proxy.

### The fix: transparent gateway mode

Instead of using an explicit proxy setting, configure the **device's WiFi gateway** to point at the SBC. All traffic from the device is then routed through the SBC at the network level — apps can't bypass it because they never make direct decisions about routing. Mihomo's TUN interface captures everything transparently.

**On the SBC:** no changes needed. pimiproxy already runs Mihomo with TUN mode and `auto-route: true`, which handles all gateway traffic automatically.

**On the device** (using Meta Quest 3 as the example):

1. Open **Settings → Wi-Fi**, tap the connected network, and choose **Edit** or **Advanced**.
2. Switch IP settings from **DHCP** to **Static**.
3. Fill in the fields:
   - **IP address:** assign a static IP outside your router's DHCP range (e.g. `192.168.1.200`)
   - **Gateway:** the SBC's LAN IP address (find it with `sudo ./pimiproxy status` or check the login banner)
   - **DNS:** your router's IP, or the Pi's IP if you want Mihomo to handle DNS
   - **Subnet mask / Prefix:** match your network (typically `255.255.255.0` / `24`)
4. Remove any explicit proxy setting — it is no longer needed.

After saving, all traffic from the device routes through the SBC regardless of whether the app honours a proxy setting or not.

> **Note:** Gateway mode requires entering the SBC's IP address directly — WiFi gateway fields do not accept hostnames. The SBC's current IP is shown in the `status` output and the login banner.

### Choosing between explicit proxy and gateway mode

| | Explicit proxy (`pimiproxy.lan:7890`) | Gateway mode (SBC as WiFi gateway) |
|---|---|---|
| Setup | One field in WiFi proxy settings | Static IP + change gateway field |
| Works with | Apps that respect OS proxy | All apps, regardless of networking stack |
| Affects | Only proxied traffic | All traffic from the device |
| Per-device config | Yes | Yes |
| Hostname usable | Yes (`pimiproxy.lan`) | No (IP required for gateway field) |

For most devices and apps, the explicit proxy is simpler and sufficient. Use gateway mode only when you have a specific app that is known to bypass the system proxy.

---

## 11. `clash-system off` Doesn't Bypass the Proxy

**Symptom:** You ran `sudo clash-system off`, expected the device's own traffic to go direct, but it's still being proxied — or blocked sites still time out in both `on` and `off` states.

### Quick check

```bash
sudo clash-system status          # current state + TUN interface
sudo ip rule show | grep 5000     # expect: fwmark 0x378 lookup main (when off)
sudo iptables -t mangle -L OUTPUT -n | grep MARK    # MARK set 0x378 (when off)
sudo iptables -t nat -L POSTROUTING -n | grep MASQ  # mark match 0x378 → MASQUERADE (when off)
```

If the rules show but traffic is still going through the proxy, one of the below is likely.

### Cause A: `systemd-resolved` still has mihomo as a DNS scope

On Fedora, Ubuntu, Arch, and most systemd distros, `systemd-resolved` auto-adds the Mihomo TUN interface as a DNS source when it comes up. Queries via `getaddrinfo()` then hit Mihomo's DNS (which returns fake-IPs) regardless of our bypass. The installer and `clash-system` both strip the mihomo scope, but if the service was restarted without triggering our fix, the scope can reappear.

**Fix:**
```bash
sudo resolvectl dns mihomo ""
sudo resolvectl default-route mihomo false
sudo resolvectl flush-caches
```

Or just toggle to apply it automatically:
```bash
sudo clash-system off    # fix_resolved() runs as a side-effect
```

### Cause B: Gateway clients are fine, host isn't — but app has its own DNS cache

Firefox, Chrome, some Docker containers cache DNS internally. After toggling, their cached fake-IPs linger. Restart the app or flush its DNS cache.

### Cause C: You want bypass to survive reboot

By default, the bypass is runtime-only. After reboot, state resets to "on". To persist, install with advanced mode and answer yes to *"Bypass proxy for this device's traffic on boot?"* — this installs `pimiproxy-bypass.service` which reapplies the bypass after Mihomo comes up. Check status:

```bash
systemctl status pimiproxy-bypass.service
```

### Cause D: Gateway-client traffic also seems affected

Gateway clients (Meta Quest etc. using this device as their DHCP gateway) should **always** be proxied regardless of the toggle — the toggle only touches the `OUTPUT` chain, not `FORWARD`. If gateway clients can't reach anything, it's unrelated to `clash-system`. Check §9 and §10.

---

## 12. `update` Says "No archive or source found for pimiproxy-api — skipping"

`pimiproxy-api` and `pimiproxy-ui` are pimiproxy's own images — no public registry carries them, so `sudo pimiproxy update` can only refresh them from something it can reach:

1. **A release archive's images on disk** — put a newer release's `docker-images/pimiproxy-api.tar.gz` and `docker-images/pimiproxy-ui.tar.gz` next to the installed script (e.g. rsync the extracted release folder to the device) and rerun `sudo pimiproxy update`. Since v8.0.0 the archives are version-stamped: `update` tells you the running vs archive version and skips archives that aren't newer.
2. **The source tree** — if the full source folder sits next to the installed script, `update` rebuilds both images locally (needs the device to reach a package registry, so usually dev-machine-only).
3. **A configured update host** — set `"update_url"` in `/opt/pimiproxy/.pimiproxy-config.json` to the base URL of a host serving the `releases/updates/` directory (`manifest.json` + per-arch bundles) produced by `3_build-releases.sh`. `update` then downloads, sha256-verifies, and loads both images when a newer version is offered. The download goes through the local proxy tunnel first, so it works where the update host is otherwise blocked.

If none of the three exist, `update` still refreshes mihomo and the dashboard but leaves api/ui as they are — that's what the warning means. Check the running version anytime with `sudo pimiproxy status`.
