# pimiproxy

Two-file installer and configuration tool for a Mihomo-based proxy server
on Raspberry Pi / Debian systems.

## Files

- `pimiproxy-bootstrap.sh` — bash bootstrap that installs prerequisites
  (Python, Docker, avahi, raspi-config) then calls pimiproxy.py
- `pimiproxy.py` — main Python script: install / uninstall / configure

## Key design decisions

- No heredocs anywhere — all file generation uses explicit Python string
  lists joined with \n to avoid tab/space issues in YAML output
- Metadata stored as JSON (not shell-sourced) to avoid quoting edge cases
- Docker compose stored as two variables (COMPOSE_BIN, COMPOSE_SUB) never
  as a space-separated string
- Platform detection handled by the Platform class in pimiproxy.py
- Docker installed via get.docker.com on all distros

## Target platforms

Raspberry Pi OS (Bookworm/Bullseye), Debian 11/12, Ubuntu, Fedora, Arch,
openSUSE, Alpine

## Style

- Python: standard library only, no external dependencies
- Bash: set -euo pipefail, all logging to stderr, no heredocs
- YAML: written line by line via Python, never via heredoc or string concat