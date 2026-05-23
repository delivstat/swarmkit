#!/usr/bin/env bash
set -euo pipefail

# Initialize GBrain for the Vedanta Advisor wisdom graph
# Usage: ./scripts/init-gbrain.sh [brain_dir]

BRAIN_DIR="${1:-${VEDANTA_BRAIN_DIR:-./knowledge/brain}}"

echo "=== Initializing GBrain ==="
echo "Brain directory: $BRAIN_DIR"
echo ""

if [ -d "$BRAIN_DIR/.git" ]; then
  echo "GBrain already initialized at $BRAIN_DIR"
  echo "To reset: rm -rf $BRAIN_DIR && ./scripts/init-gbrain.sh"
  exit 0
fi

mkdir -p "$BRAIN_DIR"
cd "$BRAIN_DIR"

# Initialize GBrain
npx gbrain init

echo ""
echo "=== GBrain initialized ==="
echo ""
echo "Next steps:"
echo "  1. Run the ingestion topology to populate ChromaDB"
echo "  2. Run the graph builder topology to create wisdom blocks"
echo "  3. Test with: npx gbrain search 'detachment from outcomes'"
echo ""
echo "To start the MCP server:"
echo "  GBRAIN_PATH=$BRAIN_DIR npx gbrain mcp-serve"
