# pimiproxy — User Guide

A step-by-step guide to installing and using pimiproxy on your device.
If you want a technical overview or to contribute to the project, see
[MAINTAINER.md](MAINTAINER.md) instead.

---

## What you'll end up with

A small Linux device (Raspberry Pi, a mini-PC, a spare laptop, or a
virtual machine) that acts as a proxy server for every other device on
your home network. Point your phone, computer, console, or VR headset at
that device on port `7890` and its internet traffic will be routed
through your Clash/Mihomo subscription.

Two flavours:

- **Full** — includes a web dashboard and a browser-based management UI.
  Requires Docker. Good for arm64 (Pi 4/5) or amd64 (PC/mini-PC).
- **Ultralite** — no Docker, no web UI. Just Mihomo as a systemd service
  and a CLI. Good for armv7 (Pi 3), older/low-power hardware, or users
  who prefer minimal installs.

---

## Table of contents

- [pimiproxy — User Guide](#pimiproxy--user-guide)
  - [What you'll end up with](#what-youll-end-up-with)
  - [Table of contents](#table-of-contents)
  - [1. Before you begin](#1-before-you-begin)
  - [2. Pick the right archive](#2-pick-the-right-archive)
  - [3. Transfer the archive to your device](#3-transfer-the-archive-to-your-device)
    - [If you have SSH access](#if-you-have-ssh-access)
    - [If the device has a browser](#if-the-device-has-a-browser)
    - [USB stick](#usb-stick)
  - [4. Extract and run the installer](#4-extract-and-run-the-installer)
  - [5. Answer the installer prompts](#5-answer-the-installer-prompts)
    - ["Is this device located in China?"](#is-this-device-located-in-china)
    - ["Install mode"](#install-mode)
    - ["Hostname"](#hostname)
    - ["Provider URL"](#provider-url)
    - ["Reboot now?" (only if hostname changed)](#reboot-now-only-if-hostname-changed)
  - [6. Connect a device to the proxy](#6-connect-a-device-to-the-proxy)
  - [7. Access the dashboard (full variant only)](#7-access-the-dashboard-full-variant-only)
    - [What you can do in the dashboard](#what-you-can-do-in-the-dashboard)
  - [8. Access the web management UI (full variant only)](#8-access-the-web-management-ui-full-variant-only)
    - [What's in the UI](#whats-in-the-ui)
  - [9. Everyday CLI commands](#9-everyday-cli-commands)
    - [Full variant](#full-variant)
    - [Ultralite variant](#ultralite-variant)
  - [10. Testing that it works](#10-testing-that-it-works)
    - [Quick health check](#quick-health-check)
    - [Test a specific URL](#test-a-specific-url)
    - [Test all proxy groups](#test-all-proxy-groups)
  - [11. Meta Quest / gateway-mode devices](#11-meta-quest--gateway-mode-devices)
  - [12. Uninstalling](#12-uninstalling)
  - [13. Troubleshooting](#13-troubleshooting)

---

## 1. Before you begin

You'll need:

- **A device** to run pimiproxy on, with a supported OS:
  - Raspberry Pi OS (32/64)
  - Debian 12 / 13, Ubuntu 24.04+, Xubuntu, Lubuntu
  - Fedora 41+
  - Arch Linux, Arch Linux 32
- **Root access** on that device (`sudo` works).
- **A Clash/Mihomo subscription URL** from your VPN provider — the one
  that looks like `https://provider.example.com/api?token=abc123`.
  This is NOT the same as a `.ovpn` file or a WireGuard config.
- **The device's IP address.** Run `hostname -I` on the device, or check
  your router's device list. Write it down — you'll need it.

> **This installer assumes a fresh OS install.** It modifies the
> hostname, DNS config, firewall rules, and on China-mirror mode the
> system package sources. Don't run it on a machine you're using for
> other purposes.

---

## 2. Pick the right archive

Download the archive that matches your device's CPU architecture. Run
`uname -m` on the device to find out:

| `uname -m` output | CPU | Full archive | Ultralite archive |
|---|---|---|---|
| `x86_64` | Intel/AMD 64-bit | `pimiproxy-amd64.tar.gz` | `pimiproxy-ultralite-amd64.tar.gz` |
| `aarch64` | ARM 64-bit (Pi 4/5) | `pimiproxy-arm64.tar.gz` | `pimiproxy-ultralite-arm64.tar.gz` |
| `armv7l` | ARM 32-bit (Pi 3) | `pimiproxy-armv7.tar.gz` | `pimiproxy-ultralite-armv7.tar.gz` |
| `i686` | x86 32-bit | *(not available)* | `pimiproxy-ultralite-386.tar.gz` |

> **Raspberry Pi note:** some Pi 4 images (particularly 32-bit Raspbian
> Lite) ship a **64-bit kernel with 32-bit userspace**. On those systems
> `uname -m` returns `aarch64` but `dpkg --print-architecture` returns
> `armhf`. Always use `uname -m` as your guide — the `arm64` archive is
> the correct pick because mihomo runs under the kernel, and a 64-bit
> kernel can execute 64-bit binaries regardless of userspace.

Archive sizes are roughly:

| Archive | Size |
|---|---|
| Any full variant | ~120–180 MB |
| Any ultralite variant | ~12 MB |

### What each archive includes

Not every component is available for every architecture — some upstream
projects don't publish every platform. The installer auto-selects
whatever's available.

| Archive | mihomo | metacubexd | zashboard | pimiproxy-api | pimiproxy-ui |
|---|:---:|:---:|:---:|:---:|:---:|
| `pimiproxy-amd64` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `pimiproxy-arm64` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `pimiproxy-armv7` | ✅ | ❌ | ✅ | ✅ | ✅ |
| `pimiproxy-ultralite-amd64` | ✅ | — | — | — | — |
| `pimiproxy-ultralite-arm64` | ✅ | — | — | — | — |
| `pimiproxy-ultralite-armv7` | ✅ | — | — | — | — |
| `pimiproxy-ultralite-386` | ✅ | — | — | — | — |

Component roles:

- **mihomo** — the proxy engine itself. Every variant has this.
- **metacubexd** — the standard dashboard (port 8080). Missing on
  `armv7` because upstream doesn't publish an armv7 build.
- **zashboard** — alternative dashboard with more stats (same port).
  Used automatically on `armv7` since metacubexd isn't available.
- **pimiproxy-api** — backend for the management UI.
- **pimiproxy-ui** — browser-based management UI (port 8585).

Ultralite variants ship only mihomo as a native binary. No dashboard,
no management UI, no Docker — everything is done via CLI.

---

## 3. Transfer the archive to your device

### If you have SSH access

From your computer:

```bash
scp pimiproxy-amd64.tar.gz user@device-ip:/tmp/
```

Replace `pimiproxy-amd64.tar.gz` with your archive filename and
`user@device-ip` with your device's username and IP (e.g. `pi@10.0.0.240`).

### If the device has a browser

Open a browser on the device, download the archive, and move it to
`/tmp`:

```bash
sudo mv ~/Downloads/pimiproxy-amd64.tar.gz /tmp/
```

### USB stick

Copy the archive to a USB drive, plug it into the device, and copy it
off — or mount it and use it directly.

---

## 4. Extract and run the installer

SSH into your device (or open a terminal on it), then:

```bash
cd /tmp && tar xzf pimiproxy-amd64.tar.gz
```

Replace the filename with your archive's name. You'll get a folder —
either `pimiproxy/` (full) or `pimiproxy-ultralite/` (ultralite).

Now launch the installer:

```bash
sudo ./pimiproxy/pimiproxy install
```

Or for the ultralite variant:

```bash
sudo ./pimiproxy-ultralite/pimiproxy-ultralite install
```

The installer takes a few minutes the first time. It installs
prerequisites (Python, Docker if full, cron, avahi), configures the
firewall, loads the Mihomo binary or Docker images, and starts
everything up.

---

## 5. Answer the installer prompts

You'll see a handful of prompts. Here's what each one asks and what to
put:

### "Is this device located in China?"

Answer **y** if you're in mainland China. This switches system package
sources to Aliyun (so `apt`/`dnf`/`pacman` work without a VPN) and uses
Aliyun for Docker. Answer **n** anywhere else.

### "Install mode"

- **1 — Express** (recommended): asks only for hostname and provider
  URL, uses defaults for everything else.
- **2 — Advanced**: walks through every option (install directory,
  ports, API secret, dashboard choice, and whether to persist a host-
  traffic bypass across reboots — see §9 on `clash-system`).

Pick **1** unless you have a specific reason not to.

### "Hostname"

The name this device will answer to on your network. Defaults to
`pimiproxy`. After install you'll be able to reach it at
`pimiproxy.lan` or `pimiproxy.local`.

- Max 24 characters.
- Letters, digits, and hyphens only.
- Can't start or end with a hyphen.

Press Enter to accept the default, or type a new name.

### "Provider URL"

Paste your Clash/Mihomo subscription URL here. It looks like:

```
https://some-provider.example.com/api/v1/client/subscribe?token=abcdef
```

The installer fetches the URL to make sure it returns a valid config.
If it can't, it'll ask **"Use it anyway?"** — answer **n** to try a
different URL, or **y** to continue (useful if the subscription is
temporarily down but you know the URL is correct).

You can also leave it blank and add it later via `configure`.

### "Reboot now?" (only if hostname changed)

If you gave the device a new hostname, the router might still know it
by the old name until the DHCP lease renews. Reboot to refresh, or
answer **n** and the mDNS name (`<hostname>.local`) will work
immediately anyway.

---

## 6. Connect a device to the proxy

On any device on the same network (laptop, phone, game console,
whatever), open its **proxy settings** — NOT Wi-Fi or network settings.

| Field | Value |
|---|---|
| Proxy type | HTTP or SOCKS5 |
| Host | Your pimiproxy device IP (e.g. `10.0.0.240`) or `pimiproxy.lan` |
| Port | `7890` |
| Username/Password | *(leave blank)* |

The same port accepts both HTTP and SOCKS5 traffic.

Browse a couple of sites to confirm it's working.

---

## 7. Access the dashboard (full variant only)

The dashboard shows live traffic, which rule matched each connection,
latency to each proxy node, and lets you switch which server a proxy
group uses.

Open in any browser on your network:

```
http://pimiproxy.lan:8080
```

Replace `pimiproxy` with whatever hostname you chose. If `.lan` doesn't
work, try `.local`, or use the IP:

```
http://10.0.0.240:8080
```

When it asks for the **API URL** and **secret**:

- **API URL**: `http://pimiproxy.lan:9090` (or the IP equivalent)
- **Secret**: whatever the installer set as the API secret (default is
  `pimiproxy` — shown at the end of the install)

### What you can do in the dashboard

- **Overview** — live upload/download, memory, active connections.
- **Proxies** — tap a proxy group to switch its active node. Useful for
  picking a server in a specific region, or fallback when the default
  node is slow.
- **Rules** — see the rule list (read-only in the dashboard).
- **Connections** — live list of every connection Mihomo is handling,
  with the matched rule and chosen proxy.
- **Logs** — Mihomo's log output.

---

## 8. Access the web management UI (full variant only)

The management UI is separate from the dashboard. It's for editing
pimiproxy's config — adding custom rules and proxy groups on top of
what your provider ships.

```
http://pimiproxy.lan:8585
```

**Login:** use the same API secret (default `pimiproxy`).

### What's in the UI

- **Overview** — proxy engine status, system proxy toggle (if
  applicable), proxy count, provider info.
- **Groups** — add your own proxy groups on top of the provider's.
- **Rules** — add your own routing rules (applied with higher priority
  than provider rules).
- **Settings** — provider URL, manual refresh, port summary.

Changes saved in the UI apply immediately — the Mihomo container
restarts automatically.

---

## 9. Everyday CLI commands

After install, you can run commands from anywhere using the symlink
the installer creates:

```bash
sudo pimiproxy <subcommand>              # full variant
sudo pimiproxy-ultralite <subcommand>    # ultralite variant
```

(Or run the wrapper directly from the install dir: `sudo /opt/pimiproxy/pimiproxy …`.)

### Full variant

```bash
sudo pimiproxy                           # show help
sudo pimiproxy status                    # container health + connectivity test
sudo pimiproxy test                      # test every proxy group
sudo pimiproxy test --url https://google.com   # single URL, shows matched rule
sudo pimiproxy refresh                   # re-fetch provider URL
sudo pimiproxy logs                      # tail Mihomo logs
sudo pimiproxy logs dashboard            # tail dashboard logs
sudo pimiproxy restart                   # restart all containers
sudo pimiproxy restart mihomo            # restart one container
sudo pimiproxy update                    # pull latest Docker images
sudo pimiproxy configure                 # interactive config menu
sudo pimiproxy hostname newname          # change hostname
sudo pimiproxy china-repos               # switch package repos to Aliyun
sudo pimiproxy standard-repos            # restore default package repos
```

### Ultralite variant

```bash
sudo pimiproxy-ultralite                             # show help
sudo pimiproxy-ultralite status                      # service + connectivity test
sudo pimiproxy-ultralite test                        # test every proxy group
sudo pimiproxy-ultralite test --url https://...      # single URL, shows matched rule
sudo pimiproxy-ultralite refresh                     # re-fetch provider URL
sudo pimiproxy-ultralite logs                        # tail Mihomo logs
sudo pimiproxy-ultralite restart                     # restart Mihomo
sudo pimiproxy-ultralite configure                   # interactive config menu
sudo pimiproxy-ultralite rule-list                   # list user rules
sudo pimiproxy-ultralite rule-add "DOMAIN-SUFFIX,example.com,DIRECT"
sudo pimiproxy-ultralite rule-remove 1               # remove rule by index
```

### Host-traffic bypass (advanced — both variants)

By default, the device's own traffic goes through Mihomo. If you want it
to go *direct* while keeping gateway-client traffic (Meta Quest etc.)
proxied, use the `system` subcommand:

```bash
sudo pimiproxy system on        # default — host traffic routes through Mihomo
sudo pimiproxy system off       # host traffic goes direct (bypass)
sudo pimiproxy system status    # show current state
```

(Ultralite users: swap `pimiproxy` for `pimiproxy-ultralite`. The
standalone `clash-system` binary at `/usr/local/bin/clash-system` is
also installed and takes the same arguments.)

The toggle is not persistent across reboots by default. To make a
`clash-system off` state survive reboots, choose the advanced install
mode and answer *yes* to "Bypass proxy for this device's traffic on
boot?" — that installs a systemd unit which reapplies `off` once Mihomo
is up.

---

## 10. Testing that it works

### Quick health check

```bash
sudo ./pimiproxy status
```

You'll see:

- **Containers** (full) or **Mihomo service** (ultralite) — should say
  `up` or `active`.
- **TUN interface** — should say `up`.
- **Provider** — shows the URL and number of proxies loaded.
- **Proxy connectivity test** — sends an HTTP and HTTPS request through
  the proxy. Both should return `204` in a few hundred milliseconds.
- **Endpoints** — summary of URLs and ports.

### Test a specific URL

```bash
sudo ./pimiproxy test --url https://www.google.com
```

Output shows the HTTP status, latency, and the exact rule that matched
the domain, including the proxy chain. Very useful for debugging why a
domain is or isn't being proxied.

### Test all proxy groups

```bash
sudo ./pimiproxy test
```

Exercises each group (DIRECT, General, AI, Google/Youtube, Netflix,
International Media, Apple, Online Storage) with a representative
domain. Colour-coded results:

- ✓ green — working
- ~ yellow — reached the server but got a session-check error (normal
  for Cloudflare-protected sites when tested via curl)
- ✗ red — timeout or error (usually a provider node issue, not
  pimiproxy)

---

## 11. Meta Quest / gateway-mode devices

Some apps (Unity-based VR games, certain TV apps) ignore system proxy
settings entirely. For these, you route the whole device through
pimiproxy by making pimiproxy the device's **gateway** instead of its
proxy.

Set the device's WiFi to **static IP** with these values:

| Field | Value |
|---|---|
| IP address | Any unused IP on your subnet |
| Subnet mask | `/24` (same as your router) |
| Gateway | pimiproxy device IP |
| DNS 1 | pimiproxy device IP |
| DNS 2 | Your router IP (fallback) |

On the full variant, after install you'll see a prompt:

> **Next step:** Open the dashboard and select a proxy server for the
> 🧭 Meta Quest group.

Click **Proxies** in the dashboard, find **🧭 Meta Quest**, and pick a
node. This is the group all gateway-mode traffic from those devices
routes through.

---

## 12. Uninstalling

```bash
sudo ./pimiproxy uninstall
# or
sudo ./pimiproxy-ultralite uninstall
```

You'll be asked to confirm. Then the installer:

- Stops and removes all containers (full) or the mihomo service (ultralite).
- Removes Docker images (full).
- Removes the install directory (`/opt/pimiproxy` or `/opt/pimiproxy-lite`).
- Removes the generated systemd units, cron job, and login banner.
- Asks whether to reset the hostname to the original (typically
  `raspberrypi` or your distro default).
- Asks whether to remove avahi-daemon (say no if anything else on the
  device uses mDNS).

If you added China mirrors, run `standard-repos` *before* uninstalling
if you want the original package sources back:

```bash
sudo ./pimiproxy standard-repos
```

---

## 13. Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common problems and fixes.

Quick answers:

**The dashboard/UI URL doesn't load.** Try the IP directly
(`http://10.0.0.240:8080`) — if that works, it's a DNS/mDNS issue.
Otherwise check `sudo ./pimiproxy status`; a container may be down.

**Google/Twitter/YouTube are slow or time out.** It's almost always the
provider's proxy node. Try switching to a different node in the
dashboard under **Proxies**.

**I'm in China and packages won't install.** Did you answer yes to the
"Is this device in China?" prompt? If not, run `sudo ./pimiproxy
china-repos` to switch system repos to Aliyun.

**My hostname change didn't stick on the router.** Some routers cache
DHCP leases for 24 hours. Reboot the router or wait for the lease to
renew.
