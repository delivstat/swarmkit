#!/usr/bin/env bash
set -euo pipefail

# Initialize GBrain for the Vedanta Advisor wisdom graph
# Requires: bun (curl -fsSL https://bun.sh/install | bash)
#           gbrain (bun install -g github:garrytan/gbrain)

echo "=== Initializing GBrain ==="

# Check prerequisites
if ! command -v bun &> /dev/null; then
  echo "Bun not found. Install: curl -fsSL https://bun.sh/install | bash"
  exit 1
fi

if ! command -v gbrain &> /dev/null; then
  echo "GBrain not found. Install: bun install -g github:garrytan/gbrain"
  exit 1
fi

# Check if already initialized
if gbrain list &> /dev/null; then
  echo "GBrain already initialized."
  gbrain config show
  exit 0
fi

# Initialize with PGLite (local, no server needed)
echo "Initializing with PGLite (local Postgres)..."
gbrain init --pglite --no-embedding

# Configure expansion model to use OpenRouter
echo ""
echo "Configuring expansion model..."
gbrain config set expansion_model openrouter:anthropic/claude-haiku-4-5-20251001

echo ""
echo "=== GBrain ready ==="
gbrain config show
echo ""
echo "Next: run the graph builder topology to populate wisdom blocks"
echo "  swarmkit run workspace build-wisdom-graph"
