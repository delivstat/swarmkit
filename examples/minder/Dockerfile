FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nmap \
    iputils-ping \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install SwarmKit runtime. SWARMKIT_VERSION is supplied by docker-compose so
# the pin lives in one place; schema follows the runtime's own dependency.
ARG SWARMKIT_VERSION=1.55.0
RUN uv pip install --system --no-cache \
    swarmkit-runtime==${SWARMKIT_VERSION}

# Minder dependencies
RUN uv pip install --system \
    onvif-zeep==0.2.12 \
    tinytuya==1.18.1 \
    "python-telegram-bot[ext]==22.1" \
    httpx \
    mcp \
    fastapi \
    uvicorn[standard] \
    qrcode[pil] \
    "paho-mqtt==2.1.0" \
    "discord.py==2.4.0"

# Object detection (YOLO) — CPU-only torch to keep the image lean and avoid
# CUDA in-container. Pre-download the nano weights so first run is offline.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN uv pip install --system --no-cache \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    "torch==2.5.1+cpu" "torchvision==0.20.1+cpu" \
    && uv pip install --system --no-cache "ultralytics==8.3.40"
RUN mkdir -p /app/models \
    && python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" \
    && cp yolov8n.pt /app/models/yolov8n.pt 2>/dev/null || true

# Copy MCP servers
COPY mcp-servers/ /app/mcp-servers/

# Copy workspace config (archetypes, topologies, workspace.yaml)
COPY workspace/ /app/workspace/

# Copy legacy discovery-service (used by fallback mode)
COPY discovery-service/ /app/discovery/

# Copy onboarding webapp
COPY webapp/ /app/webapp/

# Copy the channel adapters + shared alert bus
COPY bot.py /app/bot.py
COPY discord_bot.py /app/discord_bot.py
COPY alert_bus.py /app/alert_bus.py

# Copy entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

VOLUME /data

ENTRYPOINT ["/app/entrypoint.sh"]
