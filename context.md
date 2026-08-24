# pimiproxy — Project Context

> The "why" behind pimiproxy: intent, design decisions, constraints, and the traps that
> will bite anyone (human or AI) who changes the code without understanding it first.
> For *how to build/deploy*, see `CLAUDE.md`; for user docs, `README.md` and `docs/`.
> This file is maintained by hand — update it when a decision or constraint changes.

---

## 1. What this is for (the north star)

pimiproxy turns a cheap Linux SBC into a **LAN proxy that other devices opt into** by pointing
their proxy settings (or their gateway) at it. It wraps **Mihomo (Clash.Meta)** with a provider
subscription, a dashboard, and a small web UI.

**The original and driving use case is a Meta Quest 3.** In the user's words: *"want my meta quest 3
to work. This is the main reason for the proxy."* The Quest uses the SBC as its **network gateway**
(not just an HTTP proxy), which is why the config leans on TUN + fake-IP + DNS hijack rather than a
plain mixed-port proxy. A second hard requirement rode alongside it from day one: **the SBC's own
traffic must stay separately controllable** — *"I do not want the pi's traffic routed unless it's
been turned on with clash-system on."* That tension (gateway clients always proxied, host traffic
opt-in) shapes the whole networking design.

Everything else is in service of making that reliable for a non-expert, on hardware and networks
the author doesn't control.

---

## 2. Requirements, in the priority the author set

1. **Reliability across distributions above all.** *"Reliability is the most important across all
   distributions."* Tested on Raspberry Pi OS, Ubuntu/Xubuntu, Fedora/Nobara, Arch (+ arch32), on
   amd64/arm64/armv7. Cross-distro correctness beats features.
2. **Provider-agnostic and distro-agnostic.** *"I want it to work while being provider and distro
   agnostic."* The user brings a Clash/Mihomo subscription URL; pimiproxy adapts to whatever it
   returns.
3. **Globally usable, including inside China.** *"The installer needs to be globally usable, even
   for someone within the chinese borders."* Docker Hub, ghcr.io, GitHub, PyPI, and npm may all be
   unreachable. The provider itself is China-oriented (nodes are tagged `[CHINA]`). **Offline-first
   is a core constraint, not an afterthought.**
4. **Fresh-OS-installs only.** *"noted in the readme early on and bold that this installer is meant
   for fresh OS installs."* The installer modifies hostname, DNS, firewall, IP forwarding, and
   package repos — it assumes it owns the box.
5. **Path of least resistance for a layman.** Express install, sensible defaults, press-Enter-through.
6. **Headless operation → a web UI** for day-to-day config, because the device has no screen.

---

## 3. The two-layer config model (the heart of it)

The author's own framing: *"the install creates a 'base config' and the web interface creates a
'user config' and the system behind the scenes is always merging them together on change."*

- **Base layer (provider):** fetched from the subscription URL. The URL is usually an *API endpoint*,
  not raw YAML — pimiproxy fetches it with a Clash User-Agent and extracts the `proxies:`,
  `proxy-groups:`, and `rules:` blocks verbatim, caching them in `.pimiproxy-config.json`. Refreshed
  daily by cron.
- **User layer (always on top):** proxy groups and rules the user adds via the UI or `configure`.

**Merge order matters and is deliberate:**
- Groups: provider groups verbatim → user groups appended.
- Rules: user rules first (highest priority) → provider rules → `MATCH` fallback. (User rules
  outrank the provider so a user can always override routing.)
- The `🧭 Meta Quest` group + its 19 Quest-domain rules used to be **injected** by `build_config`;
  they were removed (moved into the provider/subscription config). The `fake-ip-filter` Quest DNS
  entries stay — they govern DNS, not routing.

`build_config()` is the single function that renders this merged dict into `config.yaml`. It writes
YAML **by hand as strings — no PyYAML** — so the installer stays dependency-free on a bare OS.
`yaml_quote()` must wrap every proxy name emitted as a list item (names contain `[`, `:`, spaces).

---

## 4. The two variants — and why both exist

| | **Full** (`pimiproxy`) | **Ultralite** (`pimiproxy-ultralite`) |
|---|---|---|
| Engine | Mihomo in Docker | Mihomo as a native systemd unit |
| Extras | dashboard + web UI + API | none |
| Install dir | `/opt/pimiproxy` | `/opt/pimiproxy-lite` |
| Needs Docker | yes | **no** |
| Arches | amd64, arm64, armv7 | + 386 |

Ultralite was a deliberate later addition: *"an 'ultralite' version… no docker, no ui, no api, no
metacubexd, no zashboard. Just a script that will set the device up perfectly to be a mihomo clash
proxy that doesn't require rule changes from whatever config the API url provides."* It exists for
devices where Docker is unwanted or unavailable, and it is the **fully-offline answer** — it needs
only a mihomo binary, no container engine (see §6).

**They must be kept in sync.** The installer logic, config generation, and subcommands are mirrored;
`pimiproxy_common.py` holds the genuinely shared helpers. A change to one variant that isn't
Docker-specific belongs in the other too.

---

## 5. The Quest / gateway networking model (don't casually change this)

Getting the Quest working took real tuning; the settings below are load-bearing, not decoration.

- **TUN + `auto-route`** so the SBC can route *forwarded* traffic from devices using it as their
  gateway, not just its own.
- **`dns-hijack` + `enhanced-mode: fake-ip`** — gaming/VR devices need fake-IP DNS. The Quest sets
  the SBC as both gateway and DNS.
- **`fake-ip-filter` allow-list** — connectivity-check, NTP, STUN/WebRTC, and Meta/Oculus/Facebook
  domains must resolve to **real** IPs or the OS reports "no internet" and throttles. This list was
  built empirically against a real Quest; entries were added because something broke without them.
- **`🧭 Meta Quest` routing** — historically a hardcoded group + 19 Quest-domain rules injected by
  `build_config`; now moved to the provider/subscription config. The `fake-ip-filter` DNS allow-list
  for those same domains stays (DNS resolution, not routing).
- **`clash-system` on/off toggle** — the host-traffic control. It mangles the **`OUTPUT` chain only**
  (fwmark 888, ip rule at priority 5000, POSTROUTING MASQUERADE), never `FORWARD`. That asymmetry is
  the whole point: **gateway clients (the Quest) stay proxied regardless of the toggle**, while the
  SBC's own traffic can bypass. `pimiproxy-bypass.service` persists the "off" state across reboots
  when the advanced install opts in.
- **Known app caveat:** some apps (e.g. Unity titles using their own HTTP client) ignore the system
  proxy entirely and only work in gateway mode — documented in `docs/TROUBLESHOOTING.md`.

Invariant to protect: **gateway clients always proxied; host traffic opt-in.** If a change routes
host traffic unconditionally, or lets a `clash-system off` also drop gateway clients, it's wrong.

---

## 5a. Hard-won gotcha: no Alpine/musl in the container images

The `pimiproxy-api` / `pimiproxy-ui` images use **glibc** bases (`python:3.12-slim`, `nginx:1.27`,
`node:20-slim`), never Alpine. musl libc **segfaults on 16 KB-page arm64 kernels** — the Raspberry
Pi 5 (`rpt-rpi-2712`, `PAGE_SIZE=16384`) is the common one. A single `arm64` package must run on
every arm64 Pi (4 KB Pi 2/3/4, 16 KB Pi 5) — the tag is `arm64`, not model-specific — so glibc is
mandatory. Symptom if this regresses: the API container crash-loops with exit 139 (SIGSEGV), no
logs, and UI login returns a 502 that looks like "wrong password". mihomo (a Go binary) is
page-size-agnostic and unaffected.

---

## 6. Offline-first: what's bundled vs. what still needs the network

**Bundled offline in the release archive / `bin/` — no network:**
- Container **images** (`docker-images/*.tar.gz`: mihomo, metacubexd/zashboard, pimiproxy-api,
  pimiproxy-ui) — loaded via `docker load` (`load_image()` in core: reuse-if-present → local archive
  → registry pull as last resort).
- Mihomo **binary** for ultralite (`bin/mihomo-linux-<arch>.gz`, auto-decompressed).
- **Geodata** (`geoip.metadb`, `geosite.dat`, `country.mmdb`) — copied into `config/`.
- **Fonts** — npm-bundled into the UI build, no CDN.

- **The Docker Engine** — a static `dockerd`/`containerd`/`runc` bundle + the compose plugin per
  arch (`docker-engine/docker-<arch>.tgz`, fetched by `1_update-upstream.sh --docker`). The wrapper
  installs it (binaries → `/usr/local/bin`, shipped systemd units) **before** any network path, so
  the full variant bootstraps on a fully-offline box. get.docker.com / Aliyun remain fallbacks; the
  installer still asks *"Is this device located in China?"* only when it must go to the network and
  no bundle is present.

**Still from the network:** system packages (python3, curl, avahi, cron) from distro repos
(Aliyun-mirrored in China mode). These are usually already present on a fresh OS.

**Practical consequence:** the full variant now installs offline from the bundle alone; the
**ultralite** variant needs even less (just the mihomo binary, no Docker). The earlier trap —
"images bundled but the engine wasn't" — is closed.

---

## 7. Runtime layout & write path

**Full variant, on the device:**
- `/opt/pimiproxy/` — root-owned runtime dir (never hand-edit).
  - `.pimiproxy-config.json` — the merged config dict (base + user + subscription cache).
  - `.pimiproxy-meta.json` — install metadata (ports, install_dir, secret, dashboard).
  - `config/config.yaml` — generated Mihomo config, rebuilt on every write.
  - `docker-compose.yml`, `ui-secret.txt`, `ui-sessions.db`.
- Containers: `mihomo` (host network, privileged, owns TUN + mixed port 7890 + controller 9090),
  `metacubexd`/`zashboard` (dashboard), `pimiproxy-api` (Django, 8000→ mounted `/opt/pimiproxy`
  + docker.sock), `pimiproxy-ui` (nginx SPA, 8585, proxies `/api/*` to the API).

**Every write follows the same shape:** mutate `.pimiproxy-config.json` → `build_config()` →
`config.yaml` → restart Mihomo. This happens from **three independent writers**: the host CLI, the
daily cron refresh, and the API container (through its bind mount). They all touch the same JSON.

---

## 8. Conventions (the author's working preferences)

- **Commit after each completed task**, with detailed messages. *"each task you fully complete I want
  you to commit to the repo with detailed comments."* No batching.
- **One-line shell commands.** *"I often can't use your command code because it combines tabs and
  spaces. Can you provide one-liners."* Multi-line commands break on paste.
- **User-facing text references the wrapper scripts** (`sudo ./pimiproxy …`), never
  `python3 pimiproxy-core.py`.
- **Deploy by rsync, not clone.** Bare necessities are rsync'd to the device (`.git`, docs, scripts
  excluded); images are built on the dev machine and shipped as tarballs.
- **The dev machine is not the device.** `/srv/aidev/pimiproxy` is source only; never run
  deploy/docker/refresh against a device unless told.

---

## 9. Testing reality

There is **no automated test suite.** Verification is manual, across a fleet:
- A Raspberry Pi (`pimiproxy.lan`) plus VMs for Ubuntu, Fedora, and Arch (32- and 64-bit variants).
- `sudo ./pimiproxy status` (health + live proxy test), `test [--url]`, container logs over SSH.
- Real end-to-end check: a device (ideally the Quest) actually routing through the box.
- Before sharing a release, extract-and-install the built tarball on a fresh VM.

When changing config generation or the networking model, test against the **invariants in §3 and §5**
and, ideally, a real gateway client.

---

## 10. Known issues & design risks (addressed on branch `remediation`, 2026-08)

> **Status:** all items below were fixed on branch `remediation` (Phases 1–9), each verified
> locally against the real `mihomo` image. Live sandbox/VM verification is pending a disk/space
> window on the dev machine. Retire this section when the branch is merged and live-verified.


Diagnosed on a live install; **not currently fixed in the tree.** Recorded here so the analysis
isn't lost. (A set of candidate fixes exists as patches in `~/pimiproxy-fixes-2026-08-23/` — treat as
a starting point, not vetted.)

1. **`build_config()` is duplicated in three files** (`pimiproxy-core.py`,
   `pimiproxy-ultralite-core.py`, `pimiproxy-api/api/config_builder.py`) and must be hand-synced.
   Drift here already caused one shipped regression (the Quest group referencing a missing group).
2. **Config writes are unlocked, whole-file read-modify-writes.** With three concurrent writers
   (CLI, cron, API) and a subscription refresh that holds a stale in-memory copy across a ~30 s
   fetch, a user edit made mid-refresh can be silently overwritten — the observed *"provider URL
   reverted on its own"* symptom.
3. **A bad config is written before Mihomo validates it.** The API restarts Mihomo without checking
   the result, so an invalid provider payload or a user rule targeting a missing group can
   crash-loop the engine while the UI reports success. Mihomo has a test mode (`mihomo -t`) that
   could gate the write.
4. **"Engine up" is inferred from the TUN device existing** (`/sys/class/net/mihomo`), not from the
   engine actually answering. A dead or crash-looping engine — or a *different* Mihomo owning the
   TUN device — reads as healthy in `status`, the banner, and the UI.
5. **No install preflight for an existing Mihomo.** Installing onto a box that already runs a
   Mihomo (packaged, hand-rolled, or the other variant) silently collides on the TUN device and
   port 7890; pimiproxy's own engine never starts while everything *looks* fine. Observed in the
   wild on a shared box that also ran a native mihomo.
6. **Appended group indentation is hardcoded to 2 spaces.** A provider that emits 4-space YAML would
   mis-nest the injected Quest/user groups. (Harmless for 2-space providers, which is most.)
7. **Release packaging can ship Git LFS pointer files as geodata** if `git lfs pull` wasn't run
   before building — yielding 132-byte "geodata" and, with `geo-auto-update: false`, an engine that
   can't resolve GEOSITE rules.

---


**Resolution map (branch `remediation`):**
1. build_config duplication → **Phase 1** (unified into `pimiproxy_common.py`).
2. Unlocked config writes / URL revert → **Phase 2** (flock + atomic writes + `update_config`).
3. No validate-before-restart → **Phase 3** (`mihomo -t` gate, controller probe, 422/502).
4. TUN-existence health → **Phase 4** (controller-based `engine_health`, foreign-mihomo flag).
5. No install preflight → **Phase 5** (`preflight_conflicts`).
6. Hardcoded group indent → **Phase 1** (provider-indent detection).
7. LFS-pointer geodata in releases → **Phase 6** (loud pointer guards, build visibility).
Plus new findings from the full review: divergent API parser/GEOIP (P1), import poisoning (P3),
XFF throttle bypass (P8), offline Docker engine (P7), and assorted robustness (P9).

## 11. Historical note

Built primarily in a long Claude Code session (~500 prompts, Apr–May 2026), then iterated across
follow-ups. The full assistant-side transcripts were later pruned by retention cleanup; the user's
prompts survive in `~/.claude/history.jsonl` and were used to reconstruct the intent captured above.
