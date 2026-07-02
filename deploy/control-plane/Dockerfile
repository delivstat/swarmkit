# SwarmKit control-plane panel — the fleet API + instance registry.
#
# Build from the repo root (the build context must see packages/control-plane):
#   docker build -f deploy/control-plane/Dockerfile -t swarmkit-control-plane .
# Or via the bundle:  docker compose -f deploy/control-plane/docker-compose.yml up -d
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY packages/control-plane /app/control-plane
RUN uv pip install --system --no-cache /app/control-plane

# The central stores (registry / aggregation / artifacts / proposals sqlite, WAL mode)
# live under /data — mount a volume so they survive restarts/upgrades.
VOLUME /data
EXPOSE 8800

# Binds 0.0.0.0 *inside the container*. AUTH IS NOT OPTIONAL IN PRODUCTION: append
# --operator-token / --oidc-* via the compose `command:` (they append to this
# ENTRYPOINT) or the panel runs open. See README.md.
# (The CLI is a single-command app — invoked without a subcommand name.)
ENTRYPOINT ["swarmkit-control-plane", \
    "--host", "0.0.0.0", "--port", "8800", "--data-dir", "/data"]
