# glibc base (python:*-slim, Debian) — NOT alpine/musl. musl segfaults on
# 16 KB-page arm64 kernels (e.g. Raspberry Pi 5), so the API must use glibc to
# run across the whole Pi range (Zero → 5).
FROM python:3.12-slim

# Docker CLI (the API restarts the mihomo container via the mounted socket).
# Install just the static client binary — small, no daemon, version-pinned to
# match the bundled engine.
ARG DOCKER_CLI_VERSION=29.7.2
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    case "$(dpkg --print-architecture)" in \
      amd64) da=x86_64 ;; arm64) da=aarch64 ;; armhf) da=armhf ;; *) da="$(uname -m)" ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${da}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/d.tgz; \
    tar -xzf /tmp/d.tgz -C /tmp docker/docker; \
    install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    rm -rf /tmp/d.tgz /tmp/docker; \
    apt-get purge -y curl; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir django gunicorn pyyaml
COPY . /app
WORKDIR /app
EXPOSE 8000
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
