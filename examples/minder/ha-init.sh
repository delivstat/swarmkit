#!/bin/sh
# Pre-install HACS + popular integrations into HA on first boot.
# Runs as an init script before HA starts.

CONFIG_DIR="/config"
CUSTOM_DIR="$CONFIG_DIR/custom_components"
MARKER="$CONFIG_DIR/.minder-initialized"

if [ -f "$MARKER" ]; then
  echo "[minder-ha] Already initialized, skipping."
  exec /init
fi

echo "[minder-ha] First boot — installing integrations..."

mkdir -p "$CUSTOM_DIR"
cd /tmp

# 1. HACS (Home Assistant Community Store)
if [ ! -d "$CUSTOM_DIR/hacs" ]; then
  echo "[minder-ha] Installing HACS..."
  wget -q "https://github.com/hacs/integration/releases/latest/download/hacs.zip" -O hacs.zip && \
    mkdir -p "$CUSTOM_DIR/hacs" && \
    cd "$CUSTOM_DIR/hacs" && \
    unzip -oq /tmp/hacs.zip && \
    cd /tmp && \
    rm -f hacs.zip && \
    echo "[minder-ha] HACS installed" || \
    echo "[minder-ha] HACS install failed"
fi

# 2. LocalTuya (direct local control without cloud)
if [ ! -d "$CUSTOM_DIR/localtuya" ]; then
  echo "[minder-ha] Installing LocalTuya integration..."
  wget -q "https://github.com/rospogrigio/localtuya/archive/refs/heads/master.zip" -O localtuya.zip && \
    unzip -oq localtuya.zip && \
    mv localtuya-master/custom_components/localtuya "$CUSTOM_DIR/localtuya" && \
    rm -rf localtuya-master localtuya.zip && \
    echo "[minder-ha] LocalTuya installed" || \
    echo "[minder-ha] LocalTuya install failed"
fi

touch "$MARKER"
echo "[minder-ha] Initialization complete. Starting Home Assistant..."

exec /init
