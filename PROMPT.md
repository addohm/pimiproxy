# pimiproxy

Self-hosted LAN proxy. Mihomo (Clash.Meta) routes device traffic through provider subscriptions.

## Variants — keep in sync

- **Full** (`pimiproxy`) — compose stack: mihomo + metacubexd/zashboard + pimiproxy-api (Django) + pimiproxy-ui (React/Vite/Nginx).
- **Ultralite** (`pimiproxy-ultralite`) — Docker-free; mihomo as systemd unit, no UI.

## Stack

- Installers: `pimiproxy-core.py`, `pimiproxy-ultralite-core.py` + bash wrappers; shared helpers in `pimiproxy_common.py`. `pimiproxy-api/api/config_builder.py` mirrors `build_config()`.
- API: Django + Gunicorn, session-cookie auth, endpoints in `api/views.py`.
- UI: Vite/React SPA, npm-bundled fonts, nginx proxies `/api/*`.
- Ports: 7890 proxy, 9090 mihomo API, 8080 dashboard, 8585 UI.

## Conventions

- `/srv/aidev/pimiproxy` is dev source ONLY. SBCs (`pimiproxy.lan`, `172.16.9.{91,92,93}`) are separate — never run deploy/docker/python commands on them unless told.
- Remote access: rsync; `ssh $USER@host "sudo docker logs …"`; `ssh $USER@host "cat <file>"`. No root SSH.
- SBCs can't reach registries. Build on dev (`linux/{arm64,amd64,arm/v7}`), `docker save | gzip`, rsync, then on SBC: `docker load -i …` + `docker compose up -d --force-recreate <svc>`. `docker restart` won't apply new images.
- Scripts: `./scripts/{1_update-upstream,2_rebuild,3_build-releases}.sh`.
- Auth: API-secret session; `OriginCheckMiddleware` enforces same-host POSTs; SameSite=Strict; `SECRET_KEY` auto-generated at `/pimiproxy/ui-secret.txt`.
- Config: provider subscription (read-only) + user groups/rules merged on top. Group order provider→user; rule order user→provider→MATCH. The `🧭 Meta Quest` group/rules are **not** injected (moved to the provider config); the `fake-ip-filter` DNS entries for those domains stay. `build_config` is a single copy in `pimiproxy_common.py`.

## Reference

Read `CLAUDE.md` at session start. Propose `docs/CHANGELOG.md` entries for user-visible changes. See `docs/{INSTRUCTIONS,MAINTAINER,TROUBLESHOOTING}.md`.

## Testing

4 VMs (Ubuntu, Fedora, Arch×2) via SSH. Advanced install with random hostnames; exercise Y/N for bypass-on-boot. Verify release tarballs extract-and-install cleanly before sharing.
