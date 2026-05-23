#!/usr/bin/env bash
set -euo pipefail

# Fetch Tier 1 datasets — structured JSON from GitHub repos
# Usage: ./scripts/fetch-tier1-datasets.sh [output_dir]

DATASETS_DIR="${1:-${VEDANTA_DATASETS_DIR:-./knowledge/datasets}}"
mkdir -p "$DATASETS_DIR"

echo "=== Fetching Tier 1 scripture datasets ==="
echo "Output: $DATASETS_DIR"
echo ""

# 1. Bhagavad Gita — richest dataset (700 verses, 21+ translations, commentaries)
echo "[1/6] Bhagavad Gita (vedicscriptures/bhagavad-gita)..."
if [ -d "$DATASETS_DIR/bhagavad-gita" ]; then
  echo "  Already exists, pulling latest..."
  git -C "$DATASETS_DIR/bhagavad-gita" pull --quiet
else
  git clone --depth 1 https://github.com/vedicscriptures/bhagavad-gita.git "$DATASETS_DIR/bhagavad-gita"
fi

# 2. DharmicData — Mahabharata (18 parvas), Ramayana, Vedas
echo "[2/6] DharmicData (bhavykhatri/DharmicData)..."
if [ -d "$DATASETS_DIR/DharmicData" ]; then
  echo "  Already exists, pulling latest..."
  git -C "$DATASETS_DIR/DharmicData" pull --quiet
else
  git clone --depth 1 https://github.com/bhavykhatri/DharmicData.git "$DATASETS_DIR/DharmicData"
fi

# 3. Valmiki Ramayana — shlokas + translations + explanations
echo "[3/6] Valmiki Ramayana (AshuVj/Valmiki_Ramayan_Dataset)..."
if [ -d "$DATASETS_DIR/Valmiki_Ramayan_Dataset" ]; then
  echo "  Already exists, pulling latest..."
  git -C "$DATASETS_DIR/Valmiki_Ramayan_Dataset" pull --quiet
else
  git clone --depth 1 https://github.com/AshuVj/Valmiki_Ramayan_Dataset.git "$DATASETS_DIR/Valmiki_Ramayan_Dataset"
fi

# 4. Gita Datasets — Bhagavata Purana, Chanakya Niti
echo "[4/6] Gita Datasets (gita/Datasets)..."
if [ -d "$DATASETS_DIR/Datasets" ]; then
  echo "  Already exists, pulling latest..."
  git -C "$DATASETS_DIR/Datasets" pull --quiet
else
  git clone --depth 1 https://github.com/gita/Datasets.git "$DATASETS_DIR/Datasets"
fi

# 5. Gita JSON — simple English dataset (backup/cross-reference)
echo "[5/6] Gita JSON (kashishkhullar/gita_json)..."
if [ -d "$DATASETS_DIR/gita_json" ]; then
  echo "  Already exists, pulling latest..."
  git -C "$DATASETS_DIR/gita_json" pull --quiet
else
  git clone --depth 1 https://github.com/kashishkhullar/gita_json.git "$DATASETS_DIR/gita_json"
fi

# 6. Ancient Indian Wisdom — HuggingFace cross-text dataset
echo "[6/6] Ancient Indian Wisdom (HuggingFace)..."
if [ -d "$DATASETS_DIR/ancient-indian-wisdom" ]; then
  echo "  Already exists, skipping..."
else
  if command -v huggingface-cli &> /dev/null; then
    huggingface-cli download Abhaykoul/Ancient-Indian-Wisdom --local-dir "$DATASETS_DIR/ancient-indian-wisdom"
  else
    echo "  huggingface-cli not found. Install with: pip install huggingface-hub"
    echo "  Then run: huggingface-cli download Abhaykoul/Ancient-Indian-Wisdom --local-dir $DATASETS_DIR/ancient-indian-wisdom"
  fi
fi

echo ""
echo "=== Done ==="
echo ""
echo "Datasets fetched to: $DATASETS_DIR"
echo ""
ls -1d "$DATASETS_DIR"/*/
echo ""
echo "Next steps:"
echo "  1. Review dataset quality in each directory"
echo "  2. Run: python scripts/ingest-to-chromadb.py"
echo "  3. Run: ./scripts/init-gbrain.sh"
