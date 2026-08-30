# pimiproxy — Maintainer Guide

Documentation for developers, maintainers, and admins working on the
pimiproxy codebase. For end-user installation and usage see
[INSTRUCTIONS.md](INSTRUCTIONS.md).

---

## Table of contents

1. [Repository layout](#1-repository-layout)
2. [Two variants: full vs ultralite](#2-two-variants-full-vs-ultralite)
3. [Architecture overview](#3-architecture-overview)
4. [Development workflow](#4-development-workflow)
5. [Docker image build & deploy](#5-docker-image-build--deploy)
6. [Release process](#6-release-process)
7. [Config generation — two-layer model](#7-config-generation-two-layer-model)
8. [Adding a new subcommand](#8-adding-a-new-subcommand)
9. [Runtime paths on target devices](#9-runtime-paths-on-target-devices)
10. [Cross-distro compatibility](#10-cross-distro-compatibility)
11. [China mirrors handling](#11-china-mirrors-handling)
12. [Release archive matrix](#12-release-archive-matrix)

---

## 1. Repository layout

```
/srv/aidev/pimiproxy/
├── VERSION                       # Single source of truth for the release version
├── pimiproxy                  # Full-variant bootstrap
├── pimiproxy-core.py             # Full-variant installer + subcommands
├── pimiproxy-ultralite        # Ultralite bootstrap
├── pimiproxy-ultralite-core.py   # Ultralite installer + subcommands
├── pimiproxy_common.py           # Helpers shared by both cores (shipped alongside them)
├── pimiproxy-api/                # Django REST API container source
│   ├── Dockerfile
│   ├── manage.py
│   ├── api/                      # Views, URLs, middleware
│   └── pimiproxy_api/            # Django project settings
├── pimiproxy-ui/                 # React + Vite + Nginx frontend source
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   └── src/
├── bin/                          # Mihomo release binaries (.gz) — ultralite (Git LFS)
├── docker-engine/                # Static Docker engine bundles per arch (gitignored;
│                                 #   fetched by 1_update-upstream.sh --docker)
├── docker-images/                # Docker image archives + geodata (Git LFS)
│   ├── mihomo.tar.gz             # Multi-arch (amd64 + arm64 + armv7)
│   ├── metacubexd.tar.gz
│   ├── zashboard.tar.gz
│   ├── pimiproxy-api.tar.gz
│   ├── pimiproxy-ui.tar.gz
│   ├── geoip.metadb
│   ├── geosite.dat
│   └── country.mmdb
├── releases/                     # Output of 3_build-releases.sh (Git LFS)
│   └── updates/                  # Per-arch api/ui update bundles + manifest.json
│                                 #   for the update_url channel (gitignored)
├── scripts/                      # Developer/maintainer helper scripts
│   ├── 1_update-upstream.sh      # Pull latest mihomo bins + upstream Docker images
│   ├── 2_rebuild.sh              # Build pimiproxy-api/ui Docker images (version-labelled)
│   └── 3_build-releases.sh       # Package everything into release archives
├── CLAUDE.md                     # Instructions for Claude Code sessions (gitignored)
└── docs/
    ├── INSTRUCTIONS.md           # End-user install & usage guide
    ├── DEV_INSTRUCTIONS.md       # Full install walkthrough, prompt by prompt
    ├── TROUBLESHOOTING.md        # End-user troubleshooting
    ├── CHANGELOG.md              # Per-version change log
    ├── MAINTAINER.md             # This file
    └── IMAGE_VERSIONS.md         # Tracked upstream image versions
```

---

## 2. Two variants: full vs ultralite

### Full (`pimiproxy`)

Docker-based. Four containers: `mihomo`, `metacubexd` (or `zashboard`),
`pimiproxy-api`, `pimiproxy-ui`.

Targets: amd64, arm64, armv7. armv7 archives use `zashboard` as the
dashboard because `metacubexd` doesn't publish an armv7 Docker image
upstream — the installer auto-selects whichever dashboard image is
present. 386 has no full variant (node-alpine base image used by
pimiproxy-ui has no 386 build).

### Ultralite (`pimiproxy-ultralite`)

No Docker. Mihomo runs as a native `systemd` service with a binary from
GitHub releases. No web dashboard, no management UI. CLI only.

Targets: amd64, arm64, armv7, 386.

### What they share

- Same two-layer config model (provider + user)
- Same rule/group management concepts
- Same `test`, `status`, `configure`, `refresh`, `logs`, `restart`,
  `hostname`, `debug`, `export` subcommands
- Same China mirror support (`china-repos`, `standard-repos`)
- Same `test --url` feature with live rule-chain display via
  Mihomo's `/connections` API

### What differs

| Concern | Full | Ultralite |
|---|---|---|
| Mihomo runtime | Docker container | systemd service |
| Web dashboard | metacubexd/zashboard container | none |
| Management UI | pimiproxy-ui container | none |
| REST API | pimiproxy-api container | none |
| Config builder | `pimiproxy-api/api/config_builder.py` (in-container) + `pimiproxy-core.py:build_config()` (at install time) | `pimiproxy-ultralite-core.py:build_config()` |
| Install dir | `/opt/pimiproxy` | `/opt/pimiproxy-lite` |
| Rules CLI | Via web UI or `configure` menu | `rule-list` / `rule-add` / `rule-remove` |
| Meta Quest group | Not injected (add to provider/user config) | Not injected (add to provider/user config) |

### Keeping them in sync

Any change to installer behaviour (firewall, DNS, system repos, hostname
handling) **must be mirrored in both variants**. When editing one,
grep for the same pattern in the other:

```bash
grep -nE 'firewall-cmd|systemd-resolved' pimiproxy-core.py pimiproxy-ultralite-core.py
```

Special case: `config_builder.py` inside the API container is a copy of
the config-generation logic from `pimiproxy-core.py`. It is called by
the Django API when a user edits rules via the web UI. Keep these two
in lockstep — if you change `build_config()` in one, change it in the
other.

---

## 3. Architecture overview

```
                        ┌──────────────────────────────┐
                        │  Target device (Pi / VM)     │
                        │                              │
  Device proxy: 7890 ───┤  Mihomo (Docker or native)   │── Internet
                        │    ↓                         │
                        │  Reads config.yaml           │
                        │    (two-layer merged)        │
                        └──────────────────────────────┘
                                    ▲
                                    │ generated at install + on every edit
                                    │
  .pimiproxy-config.json ───────────┘
    (provider YAML blocks + user rules/groups)
```

### Layered config flow

1. Provider subscription fetched → raw YAML stored as three strings
   (`subscription_proxies_yaml`, `subscription_groups_yaml`,
   `subscription_rules_yaml`) in `.pimiproxy-config.json`.
2. User edits stored as structured `proxy_groups` and `rules` arrays.
3. On every write, `build_config()` merges:
   - User rules first (highest priority)
   - Provider rules next
   - MATCH fallback last
4. Result written to `config.yaml` → `docker restart mihomo` (or
   `systemctl restart mihomo` for ultralite).

### Default behaviour

- TUN mode **always on** (`auto-route: true`, DNS hijack on port 53) —
  gateway clients (Meta Quest etc.) and host traffic both flow through
  Mihomo by default
- systemd-resolved stub listener disabled during install so Mihomo can
  bind port 53
- systemd-resolved's mihomo DNS scope stripped after TUN comes up (via
  `ExecStartPost` in ultralite, via installer post-launch step in full)
- firewalld ports opened: 7890, 8080, 8585, 9090
- `iptables FORWARD -i/-o mihomo -j ACCEPT` installed so gateway
  clients can reach TUN even when FORWARD policy is DROP (Docker)

### Host-traffic toggle (`pimiproxy system` / `clash-system`)

Advanced users can bypass Mihomo for host-originated traffic while
keeping gateway clients proxied:

- `sudo pimiproxy system on` — host traffic through Mihomo (default)
- `sudo pimiproxy system off` — host traffic direct
- `sudo pimiproxy system status` — current state

The `system` subcommand in the main CLI dispatches to the underlying
`/usr/local/bin/clash-system` binary, which also accepts the same
`on|off|status` arguments directly. Ultralite users run
`pimiproxy-ultralite system ...` instead.

Implementation (`/usr/local/bin/clash-system`):

- fwmark **888** on `iptables mangle OUTPUT` — not 666, which is
  Mihomo's internal `routing-mark` for its own upstream sockets
- `ip rule add fwmark 888 lookup main priority 5000` — higher priority
  than Mihomo's auto-route rules at ~9000
- `iptables -t nat -A POSTROUTING -m mark --mark 888 -j MASQUERADE` —
  after the re-route, packets inherit the TUN's source IP (198.18.0.1)
  which isn't routable externally; MASQUERADE rewrites to the outgoing
  interface's address
- Only mangles `OUTPUT` (local traffic). `FORWARD` is untouched so
  gateway clients still route through TUN via Mihomo's auto-route rules
- Toggle doesn't persist across reboots by default. The advanced install
  prompt "Bypass proxy for this device's traffic on boot?" installs an
  optional `pimiproxy-bypass.service` that runs `clash-system off`
  after Mihomo starts

---

## 4. Development workflow

### Dev machine location

Source of truth lives at `/srv/aidev/pimiproxy/`. **Never run the
installer on this machine** — it's source-only. Test on:

| Host | Distro | IP |
|---|---|---|
| `pimiproxy.lan` | Raspberry Pi OS (Debian) | `10.0.0.240` |
| `xumiproxy.lan` | Xubuntu 25.10 | `10.0.0.91` / `172.16.9.91` |
| `femiproxy.lan` | Fedora 43 | `10.0.0.92` / `172.16.9.92` |
| `armiproxy.lan` | Arch Linux | `10.0.0.93` / `172.16.9.93` |

The VMs have a second NIC on `vmnet1` (host-only) so the dev machine
can reach them directly. SSH with key-based auth, passwordless sudo.

### Sync source to a target

```bash
rsync -avz \
  --exclude=.git --exclude=.claude --exclude=docs \
  --exclude=__pycache__ --exclude=node_modules \
  --del \
  /srv/aidev/pimiproxy/ $USER@pimiproxy.lan:/home/$USER/pimiproxy/
```

Then run commands on the target:

```bash
ssh $USER@pimiproxy.lan "sudo /home/$USER/pimiproxy/pimiproxy test"
```

### Read-only debugging on the Pi

```bash
# Container logs
ssh $USER@pimiproxy.lan "sudo docker logs mihomo --tail 50"

# Mihomo config
ssh $USER@pimiproxy.lan "sudo cat /opt/pimiproxy/config/config.yaml"

# Install metadata
ssh $USER@pimiproxy.lan "sudo cat /opt/pimiproxy/.pimiproxy-meta.json"
```

---

## 5. Docker image build & deploy

Two scripts handle image lifecycle. Both are dev-machine-only.

### `scripts/2_rebuild.sh` — build pimiproxy-api and pimiproxy-ui

Multi-arch (amd64 + arm64 + armv7, each stamped with the
`io.pimiproxy.version` / `io.pimiproxy.git` labels from `VERSION` and
git), saves as a single tarball per image with
`image:arm64` and `image:amd64` tags.

```bash
./scripts/2_rebuild.sh              # build both, sync to default target
./scripts/2_rebuild.sh --api        # build pimiproxy-api only
./scripts/2_rebuild.sh --ui         # build pimiproxy-ui only
./scripts/2_rebuild.sh --third-party    # pull + save mihomo/metacubexd/zashboard
./scripts/2_rebuild.sh --sync-only      # rsync source without rebuilding

# On the target:
./scripts/2_rebuild.sh --deploy         # load both images, recreate containers
./scripts/2_rebuild.sh --deploy-api
./scripts/2_rebuild.sh --deploy-ui
```

Override the sync target:
```bash
./scripts/2_rebuild.sh --target user@host
# or
PIMIPROXY_TARGET=user@host ./scripts/2_rebuild.sh
```

### `scripts/1_update-upstream.sh` — fetch latest upstream

```bash
./scripts/1_update-upstream.sh                # bins + images (all)
./scripts/1_update-upstream.sh --bins         # mihomo release binaries only
./scripts/1_update-upstream.sh --images       # Docker images only
./scripts/1_update-upstream.sh --dry-run      # show what would happen
```

Downloads latest mihomo release from GitHub (arm64/amd64/armv7 `.gz`
binaries to `bin/`), pulls latest `metacubex/mihomo`, `metacubexd`,
`zashboard` Docker images (arm64 + amd64 each), and saves as multi-arch
tarballs in `docker-images/`.

Skips the download if the current `bin/` version matches the latest
GitHub tag.

### Multi-arch tarball format

All `.tar.gz` archives in `docker-images/` contain two tags:

```
<image>:arm64
<image>:amd64
```

`pimiproxy-core.py`'s `load_image()` selects the tag matching the host
architecture at install time and retags it as the canonical image name.

---

## 6. Release process

```bash
# 0. Bump the root VERSION file — it is the single source of truth and is
#    stamped into the api/ui images as the io.pimiproxy.version label.
./scripts/1_update-upstream.sh              # fetch latest mihomo + upstream Docker images
./scripts/2_rebuild.sh                      # build pimiproxy-api + pimiproxy-ui (version-labelled)
./scripts/3_build-releases.sh               # package all archives + releases/updates/
```

Besides the release archives, `3_build-releases.sh` writes `releases/updates/`
(gitignored): per-arch `pimiproxy-{api,ui}-<arch>.tar.gz` bundles plus a
`manifest.json` (version + sha256 per file). Copying that directory verbatim
onto any static file host gives devices with the `update_url` config key set a
`sudo pimiproxy update` network path — the api/ui images exist on no registry,
so this is their only over-the-network update channel. Which host (and whose
name is on it) is deliberately not baked into the repo or the packages.

`scripts/3_build-releases.sh` produces two flavors per variant per arch
(offline / online) — see §12 for the full archive matrix, sizes, and
contents. It extracts the single-arch image from each multi-arch tarball
via `docker load` + `docker save <image>:<arch>` so a release only
contains what the target architecture needs.

---

## 7. Config generation — two-layer model

### Storage: `.pimiproxy-config.json`

```json
{
  "subscription_url": "...",
  "subscription_proxies_yaml": "proxies:\n  - name: ...",
  "subscription_groups_yaml":  "proxy-groups:\n  - name: ...",
  "subscription_rules_yaml":   "rules:\n  - DOMAIN-SUFFIX,...",
  "proxy_groups": [ {...user groups...} ],
  "rules":        [ "TYPE,value,target", ... ],
  "api_port":     "9090",
  "mixed_port":   "7890",
  "api_secret":   "...",
  "auto_refresh_enabled": true,
  "refresh_interval_days": 1
}
```

### Merge order in `build_config()`

1. **Global Mihomo options** — ports, TUN, DNS, fake-ip-filter
2. **Proxies** — subscription proxies verbatim
3. **Proxy groups** — subscription groups verbatim → user groups appended
4. **Rules** — user rules first → provider rules → MATCH fallback

The user layer is always applied on top of the provider layer — this
lets users override provider routing without editing the subscription
YAML.

### fake-ip-filter

Includes all `.lan`, `.local`, and search-domain variants
(`*.lan.lan`, `*.local.localdomain`, etc.) to handle systems with
`search lan` in `resolv.conf`. Also excludes connectivity check domains
(gstatic, msft, apple) and Meta/Oculus/Facebook/Instagram so real IPs
resolve for devices that need them.

### yaml_quote()

Used when writing proxy group names to YAML. Proxy names may contain
`[`, `:`, and other YAML-special characters. Applied to **every**
proxy name reference in generated YAML. If you skip it, Mihomo will
silently fail to parse the config.

---

## 8. Adding a new subcommand

### In `pimiproxy-core.py` (or ultralite)

1. Define the handler function:
   ```python
   def do_mything():
       meta = read_meta()
       # ... logic ...
   ```

2. Register it in the `COMMANDS` dict (near the bottom of the file):
   ```python
   COMMANDS = {
       ...
       "mything": do_mything,
   }
   ```

3. If it takes an argument after the subcommand name:
   ```python
   _TAKES_ARG = {"hostname", "logs", ..., "mything"}
   ```

4. Update the usage text in `main()`:
   ```python
   print("       sudo ./pimiproxy mything [arg]")
   ```

### In `pimiproxy` / `pimiproxy-ultralite`

Non-install subcommands are passed through automatically via
`exec python3`, so no shell changes needed — but add the command to
the `--help` menu so it's discoverable:

```bash
  ${BOLD}  mything${NC}             What it does
```

### Keep both variants in sync

If the subcommand applies to both, replicate in the ultralite files
too. If it's full-only (e.g. Docker-specific), leave ultralite alone.

### Commit after each subcommand

Per the project's commit-after-task rule, each completed subcommand
should get its own detailed commit rather than being batched.

---

## 9. Runtime paths on target devices

### Full variant

| Path | Purpose |
|---|---|
| `/opt/pimiproxy/` | Install dir, root-owned, never edit manually |
| `/opt/pimiproxy/pimiproxy` | Wrapper script (copy of the release wrapper) |
| `/opt/pimiproxy/pimiproxy-core.py` | Core script (copy of the release core) |
| `/opt/pimiproxy/.pimiproxy-config.json` | Live config, read/written by API container |
| `/opt/pimiproxy/.pimiproxy-meta.json` | Install metadata (ports, paths, arch) |
| `/opt/pimiproxy/config/config.yaml` | Generated Mihomo config |
| `/opt/pimiproxy/docker-compose.yml` | Runtime compose file (no `build:` directive — uses pre-loaded images) |
| `/opt/pimiproxy/ui-secret.txt` | Django SECRET_KEY for pimiproxy-api |
| `/usr/local/bin/pimiproxy` | Symlink → `/opt/pimiproxy/pimiproxy` |
| `/usr/local/bin/clash-system` | Host-traffic bypass toggle |
| `/etc/profile.d/pimiproxy-banner.sh` | Login banner (also sourced from `/etc/bash.bashrc` / `/etc/bashrc` for desktop terminals) |
| `/etc/cron.daily/pimiproxy-refresh` | Daily provider refresh |
| `/etc/sysctl.d/99-pimiproxy.conf` | IP forwarding |
| `/etc/systemd/system/pimiproxy-bypass.service` | Optional — installed only if advanced install opts in to "bypass on boot" |

### Ultralite variant

| Path | Purpose |
|---|---|
| `/opt/pimiproxy-lite/` | Install dir |
| `/opt/pimiproxy-lite/pimiproxy-ultralite` | Wrapper script |
| `/opt/pimiproxy-lite/pimiproxy-ultralite-core.py` | Core script |
| `/opt/pimiproxy-lite/.pimiproxy-config.json` | Live config |
| `/opt/pimiproxy-lite/.pimiproxy-meta.json` | Metadata |
| `/opt/pimiproxy-lite/config/config.yaml` | Mihomo config |
| `/usr/local/bin/mihomo` | Mihomo binary (extracted from bin/*.gz) |
| `/usr/local/bin/pimiproxy-ultralite` | Symlink → install-dir wrapper |
| `/usr/local/bin/clash-system` | Host-traffic bypass toggle |
| `/etc/systemd/system/mihomo.service` | systemd unit |
| `/etc/systemd/system/pimiproxy-bypass.service` | Optional — installed only if advanced install opts in |

### Docker socket mount (full only)

The `pimiproxy-api` container mounts `/var/run/docker.sock` so it can
run `docker restart mihomo` after a config edit. If Docker isn't
listening on the default socket path, this will break — not currently
configurable.

---

## 10. Cross-distro compatibility

Tested on:

| Distro | Package manager | Notes |
|---|---|---|
| Raspberry Pi OS (Debian) | apt | Reference platform |
| Ubuntu / Lubuntu / Xubuntu 25.10 | apt | Same as Debian |
| Fedora 43 | dnf | systemd-resolved must be neutered |
| Arch Linux | pacman | Doesn't ship `hostname` command (fallback to `hostnamectl`) |

### Gotchas per distro

- **Fedora**: `metalink=` URLs in `fedora*.repo` are unreachable from
  China — `china-repos` switches to Aliyun baseurl and disables
  third-party repos (cisco-openh264 etc.)
- **Fedora**: Docker manages its own firewalld zone; never add
  `docker0` to any zone manually or `ZONE_CONFLICT` kills Docker
- **Arch**: `hostname` is in `inetutils` (optional). `current_hostname()`
  falls back to `hostnamectl` → `/etc/hostname` → `"localhost"`
- **Arch**: No cron shipped by default. Bootstrap installs `cronie`.
- **Ubuntu 25.10 (plucky)**: Codename not yet in Aliyun Docker CE
  mirror — bootstrap probes and falls back to `noble` if the current
  codename isn't available
- **Debian trixie**: Security packages live at `/debian-security` not
  `/debian` — Aliyun source file splits these into two entries

### Prerequisites auto-installed

| Prereq | Debian | Fedora | Arch |
|---|---|---|---|
| Python 3 | `python3` | `python3` | `python` |
| curl | `curl` | `curl` | `curl` |
| ip | `iproute2` | `iproute` | `iproute2` |
| sysctl | `procps` | `procps-ng` | `procps-ng` |
| Docker | `docker-ce` / `docker.io` | `docker-ce` | `docker` |
| cron | `cron` | `cronie` | `cronie` |
| mDNS | `avahi-daemon` | `avahi` + `nss-mdns` | `avahi` + `nss-mdns` |

---

## 11. China mirrors handling

Users in China get a prompt at install time:

```
Is this device located in China? (use Aliyun mirrors) [y/N]:
```

Answering **y** triggers `china-repos` logic during install:

1. **System package repos** are switched to Aliyun:
   - Debian/Ubuntu: DEB822-format sources file at
     `/etc/apt/sources.list.d/aliyun-system.sources`. Debian gets two
     entries (`/debian` for main+updates, `/debian-security` for
     security). Existing `.sources` and `.list` files are renamed to
     `.disabled`.
   - Fedora: `metalink=` is commented out and `baseurl=` is rewritten
     to Aliyun in `fedora.repo` and `fedora-updates.repo`.
     `fedora-cisco-openh264`, `fedora-updates-testing`, and
     `fedora-modular` are disabled because they have no Aliyun mirror.
   - Arch: `/etc/pacman.d/mirrorlist` is replaced with a single Aliyun
     entry. Original is backed up to `mirrorlist.bak`.

2. **Docker CE repo** is added from Aliyun for apt/dnf hosts. Arch
   installs Docker via pacman from the main community repo (which is
   now Aliyun).

3. **Backups**: original `.repo` / `.sources` / `.list` files are
   renamed to `.bak` so `standard-repos` can restore them.

The subcommands `china-repos` and `standard-repos` replicate this
behaviour post-install.

### Testing in China

The user is in China. Any change to mirror handling must be validated
by actually running the install on a fresh VM in-country. GitHub,
ghcr.io, and docker.io are all blocked — the offline-first path is the
default, not a fallback.

---

## 12. Release archive matrix

> Each variant is built in two flavors per arch: `*-offline.tar.gz`
> (self-contained — images/binary + Docker engine + geodata, installs with no network) and
> `*.tar.gz` (online — source + geodata, fetches the heavy parts at install). Geodata is in both.


Produced by `./scripts/3_build-releases.sh`. All archives land in `releases/`
(tracked via Git LFS); the `releases/updates/` bundles + `manifest.json` for
the `update_url` channel are gitignored and copied to the update host at
publish time.

Fourteen archives are produced in a full build (two flavors × seven
variant/arch combinations):

| Archive (offline / online) | Variant | Arch | Approx size |
|---|---|---|---|
| `pimiproxy-amd64[-offline].tar.gz` | Full | amd64 (x86_64) | ~360 MB / ~11 MB |
| `pimiproxy-arm64[-offline].tar.gz` | Full | arm64 (aarch64) | ~345 MB / ~11 MB |
| `pimiproxy-armv7[-offline].tar.gz` | Full | armv7 (32-bit ARM) | ~281 MB / ~11 MB — offline ships zashboard only (no metacubexd armv7 upstream) |
| `pimiproxy-ultralite-amd64[-offline].tar.gz` | Ultralite | amd64 | ~23 MB / ~11 MB |
| `pimiproxy-ultralite-arm64[-offline].tar.gz` | Ultralite | arm64 | ~22 MB / ~11 MB |
| `pimiproxy-ultralite-armv7[-offline].tar.gz` | Ultralite | armv7 | ~22 MB / ~11 MB |
| `pimiproxy-ultralite-386[-offline].tar.gz` | Ultralite | 386 | ~23 MB / ~11 MB |

No full-variant 386 archive: pimiproxy-ui's Node build stage has no
386 base image, so the Docker build fails. Users on 32-bit x86 should use
`pimiproxy-ultralite-386.tar.gz`.

### What's inside each archive

**Full (`pimiproxy-<arch>-offline.tar.gz`; the online flavor omits
`docker-engine/` and the five image tarballs):**
```
pimiproxy/
├── VERSION
├── pimiproxy
├── pimiproxy-core.py
├── pimiproxy_common.py
├── README.md
├── docs/                       (user docs; maintainer/dev docs excluded)
├── docker-engine/
│   ├── docker-<arch>.tgz       (static engine bundle)
│   └── docker-compose-<arch>
├── docker-images/
│   ├── mihomo.tar.gz           (single-arch)
│   ├── metacubexd.tar.gz       (single-arch; amd64/arm64 only)
│   ├── zashboard.tar.gz        (single-arch)
│   ├── pimiproxy-api.tar.gz    (single-arch)
│   ├── pimiproxy-ui.tar.gz     (single-arch)
│   ├── geoip.metadb
│   ├── geosite.dat
│   └── country.mmdb
├── pimiproxy-api/              (source — fallback if archive load fails)
└── pimiproxy-ui/               (source — fallback)
```

**Ultralite (`pimiproxy-ultralite-<arch>-offline.tar.gz`; the online
flavor omits `bin/` and downloads the binary at install):**
```
pimiproxy-ultralite/
├── VERSION
├── pimiproxy-ultralite
├── pimiproxy-ultralite-core.py
├── pimiproxy_common.py
├── README.md
├── docs/
├── bin/
│   └── mihomo-linux-<arch>-<version>.gz
└── docker-images/
    ├── geoip.metadb
    ├── geosite.dat
    └── country.mmdb
```

**Update bundles (`releases/updates/`, for the `update_url` channel):**
```
updates/
├── manifest.json               (version + sha256 + size per file)
├── pimiproxy-api-<arch>.tar.gz (single-arch, ~75 MB; amd64/arm64/armv7)
└── pimiproxy-ui-<arch>.tar.gz  (single-arch, ~60–71 MB)
```

### armv7 and 386 availability

The build scripts target four architectures: `amd64`, `arm64`, `armv7`,
`386`. Not all upstream Docker images publish every platform variant —
`scripts/1_update-upstream.sh` tries each platform and silently skips
those that aren't available. `scripts/3_build-releases.sh` then only
produces archives for arches where every required image tag exists.

For the ultralite variant, mihomo ships binaries for all four
architectures from its GitHub releases. Ultralite archives are produced
for any arch that has a matching binary in `bin/`.

Running armv7/386 Docker containers on older/slower hardware is viable
but noticeably slower than native ultralite — users on Pi 3 or similar
should prefer ultralite when possible.

---

## Git hygiene

- Commit after each completed task (not batched). Write descriptive
  messages that explain *why*, not just *what*.
- `.gitignore` covers: `releases/`, `docker-images/*.tar.gz`, `bin/*.gz`,
  `__pycache__`, `node_modules`, and the dev-only `scripts/2_rebuild.sh`.
- Provider names (`Wannaflix` etc.) should never appear in
  user-facing text or rule targets. The project is provider-agnostic.
