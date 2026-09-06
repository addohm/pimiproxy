# pimiproxy — Version History

A narrative overview of every release: what each version was *about*, in a few
lines. For the full itemized changes behind any entry, see
[CHANGELOG.md](CHANGELOG.md). Since 8.0.0 the release version lives in the
root `VERSION` file, is stamped into the api/ui images
(`io.pimiproxy.version` label), ships inside every archive, and is shown by
`status` and the login banner.

| Version | Date | Theme |
|---|---|---|
| [8.0.0 (unreleased)](#800--in-progress-2026-08--09) | in progress | Reliability remediation, doctor, versioned releases + update path, provider-managed DNS |
| [7.0.0](#700--2026-04-20) | 2026-04-20 | Host-traffic toggle returns; shared common module; API hardening |
| [6.1.0](#610--2026-04-16) | 2026-04-16 | Always-on TUN — toggle removed |
| [6.0.0](#600--2026-04-15) | 2026-04-15 | pimiproxy-ultralite: the Docker-free variant |
| [5.1.0](#510--2026-04-14) | 2026-04-14 | YAML-quoting and offline-geodata crash fixes; Check-URL tool |
| [5.0.0](#500--2026-04-14) | 2026-04-14 | Web management UI; two-layer config model |
| [4.0.0–4.7.0](#40x--2026-04-11--12) | 2026-04-11 → 12 | Cross-distro bootstrap and the management subcommand suite |
| [3.0.0](#300--2026-04-11) | 2026-04-11 | Complete rewrite in Python |
| [2.0.0–2.2.0](#20x--2026-04-11) | 2026-04-11 | The bash era: single-script install, hard lessons in heredocs |

---

## 8.0.0 — in progress (2026-08 → 09)

The reliability release, driven by faults observed on a real, long-running
install. The remediation work landed in phases: one shared `build_config()`
in `pimiproxy_common.py` instead of three hand-synced copies; locked, atomic
config I/O across the three concurrent writers (CLI, cron, API) — fixing the
"provider URL reverted on its own" bug; `mihomo -t` validation *before* any
config is persisted or the engine restarted; engine health measured by the
controller answering, not the TUN device existing; an install preflight that
refuses to collide with a foreign mihomo; loud Git-LFS-pointer guards in
release packaging; and a bundled static Docker engine so the full variant
installs on a truly offline box.

On top of that: **`pimiproxy doctor`** (seven read-only end-to-end checks
with plain-language fix steps); **anti-poisoning DNS** (tunneled DoH-by-IP
fallback with geoip filtering, `proxy-server-nameserver` to break the DoH
bootstrap deadlock, captive-portal domains pinned past ISP hijacks);
**glibc container images** (musl segfaults on the Pi 5's 16 KB-page kernel);
**versioned releases** (`VERSION` file, labeled images, version-aware
never-downgrading `update`, offline/online release flavors, per-arch update
bundles + manifest for the unset-by-default `update_url` channel); and
**provider-managed DNS** — refresh now lifts `fake-ip-filter` and
`nameserver-policy` out of the subscription's dns block and merges them,
deduplicated, into the generated config.

## 7.0.0 — 2026-04-20

The host-traffic toggle came back, properly designed: `clash-system` (and
`pimiproxy system on|off|status`) bypasses the proxy for the *host's own*
traffic only — gateway clients like the Quest stay proxied because only the
`OUTPUT` chain is marked, never `FORWARD`. Optional `pimiproxy-bypass.service`
persists the choice across boots. `pimiproxy_common.py` was extracted to end
copy-paste drift between the variants (a drift bug had already shipped), the
API gained login rate-limiting, Origin-check CSRF middleware, and strict
session cookies, wrapper scripts lost their `.sh` suffix, and armv7 got a
full-variant archive (zashboard-only). Raspbian and archlinux32 mirror
handling rounded out China support.

## 6.1.0 — 2026-04-16

Simplification release: the old clash-system toggle and boot service were
removed and TUN became always-on for everything — host and gateway clients
alike. (7.0.0 would revisit this with the correct OUTPUT-only design; the
lesson was that the toggle was right, its first implementation wasn't.)

## 6.0.0 — 2026-04-15

**pimiproxy-ultralite**: a parallel Docker-free variant — mihomo as a native
systemd unit with ambient capabilities, arch-detected binary install,
`/opt/pimiproxy-lite`, scripted rule management (`rule-add`/`rule-remove`/
`rule-list`), no UI/dashboard/API. Born for devices where Docker is unwanted
or unavailable, and it remains the fully-offline answer with the smallest
footprint (and the only 386 build).

## 5.1.0 — 2026-04-14

Three crash classes fixed: proxy names with YAML-special characters
(`[REALITY] [CHINA] …`) now always `yaml_quote()`d; the injected Quest group
no longer references a group name the provider doesn't have; and geodata is
bundled at install with `geo-auto-update: false` so mihomo never dies trying
to reach GitHub for it (the origin of today's offline-geodata policy). The
UI gained the Check URL tool showing live mihomo routing decisions.

## 5.0.0 — 2026-04-14

The web management UI: `pimiproxy-api` (Django) and `pimiproxy-ui`
(React + nginx) containers on port 8585, sharing the mihomo API secret for
login. With it came the **two-layer config model** that still defines the
project — provider subscription as the base, user groups appended and user
rules prepended on top, so users can override routing without touching the
provider's YAML. Fonts npm-bundled (no CDN — China), "Wannaflix" genericized
to "Provider", and `docker-images/` established as the offline archive home.

## 4.x — 2026-04-11 → 12

Two days of rapid platformization on the new Python base:

- **4.0.0** — cross-distro bootstrap: distro/package-manager/arch detection
  (Debian/Raspbian/Ubuntu, Fedora/RHEL, Arch, openSUSE, Alpine), Docker via
  get.docker.com, platform-aware hostname/avahi handling.
- **4.1.0** — subscription handling rewritten to fetch-and-inject verbatim
  blocks (no `proxy-providers` indirection), GEOIP rules stripped (MMDB needs
  GitHub), `refresh` subcommand + daily cron.
- **4.2.0** — `hostname` subcommand; smarter install-time hostname prompt.
- **4.3.0** — the management suite: `status`, `logs`, `restart`, `update`,
  plus configure options for secret, ports, and dashboard switching.
- **4.4.0** — `debug` subcommand (provider URL inspector).
- **4.5.0** — login banner + AES hardware-acceleration detection.
- **4.6.0** — gateway mode made real: `auto-route`, IP forwarding, the
  fwmark-based clash-system rewrite, expanded fake-ip-filter (connectivity
  checks, NTP, STUN, streaming DRM domains).
- **4.7.0** — the `🧭 Meta Quest` proxy group injection (later moved to the
  provider config in 8.0.0).

## 3.0.0 — 2026-04-11

Complete rewrite in Python; the bash script retired. Interactive `configure`
menu (group builder, rule editor with presets), structured state in
`.pimiproxy-config.json` + JSON metadata, and all file generation moved to
line-by-line Python string building — making the tab/quoting corruption
class from the bash era structurally impossible.

## 2.x — 2026-04-11

The bash era, all in one day. **2.0.0** established the shape: single-script
install/uninstall, compose detection, archive-or-pull image loading, install
metadata, mDNS via avahi, the first `clash-system`, and the first two
`fake-ip-filter` entries (`*.lan`, `*.local`). **2.1.0** added the
subscription URL prompt and provider-based config generation. **2.1.1** and
**2.2.0** fought sourcing/quoting/heredoc corruption in generated files —
the battle that motivated the Python rewrite hours later. Everything before
2.0.0 was development iteration and is undocumented.
