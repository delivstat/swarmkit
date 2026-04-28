#!/bin/bash
# Setup knowledge directories for the Sterling OMS workspace.
#
# Creates the folder structure for product docs, project docs,
# and reference designs. Generates a .env file with all required
# environment variables.
#
# Usage:
#   ./scripts/setup-knowledge.sh                    # uses ~/sterling-knowledge as base
#   ./scripts/setup-knowledge.sh /data/sterling      # custom base directory

set -e

BASE="${1:-$HOME/sterling-knowledge}"

PRODUCT_DOCS="$BASE/product-docs"
PROJECT_DOCS="$BASE/project-docs"
REFERENCES="$BASE/references"

echo "Setting up Sterling knowledge directories at: $BASE"
echo ""

# Product docs (stable, ingest once)
mkdir -p "$PRODUCT_DOCS/product-docs"
mkdir -p "$PRODUCT_DOCS/api-javadocs"
mkdir -p "$PRODUCT_DOCS/data-model"

# Project docs (changes over time, re-ingest as needed)
mkdir -p "$PROJECT_DOCS/design-docs"
mkdir -p "$PROJECT_DOCS/extensions"
mkdir -p "$PROJECT_DOCS/transforms"
mkdir -p "$PROJECT_DOCS/templates"
mkdir -p "$PROJECT_DOCS/integrations"
mkdir -p "$PROJECT_DOCS/3rd-party-docs"

# Reference designs (separate index, ingest once)
mkdir -p "$REFERENCES/templates"

echo "Created directories:"
echo ""
echo "  $PRODUCT_DOCS/"
echo "  ├── product-docs/        ← Sterling product docs (markdown/HTML)"
echo "  ├── api-javadocs/        ← Javadocs from Sterling install"
echo "  └── data-model/          ← Database ERD from Sterling install"
echo ""
echo "  $PROJECT_DOCS/"
echo "  ├── design-docs/         ← Your project's Word/PDF/markdown docs"
echo "  ├── extensions/          ← Java extension source code"
echo "  ├── transforms/          ← XSL transform files"
echo "  ├── templates/           ← XML API/config templates"
echo "  ├── integrations/        ← Integration specs (Excel → markdown)"
echo "  └── 3rd-party-docs/      ← README/Javadoc for key libraries"
echo ""
echo "  $REFERENCES/"
echo "  └── templates/           ← Reusable Sterling patterns"
echo ""

# Generate .env file
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$WORKSPACE_DIR/.env"

cat > "$ENV_FILE" << ENVEOF
# Sterling OMS workspace environment variables
# Source this file: source .env
# Or set these in your shell profile.

# Model provider
OPENROUTER_API_KEY=

# Sterling API (your local instance)
STERLING_API_URL=http://localhost:9080/smcfs/restapi/
STERLING_API_USER=admin
STERLING_API_PASSWORD=

# Knowledge base directories
STERLING_PRODUCT_DOCS_DIR=$PRODUCT_DOCS
STERLING_PROJECT_DOCS_DIR=$PROJECT_DOCS
REFERENCE_DESIGNS_DIR=$REFERENCES

# GitHub (for code review topology)
GITHUB_TOKEN=
ENVEOF

echo "Generated: $ENV_FILE"
echo ""
echo "Next steps:"
echo ""
echo "  1. Copy/symlink your files into the directories above"
echo ""
echo "  2. If you have a large markdown file, split it:"
echo "     python scripts/split-markdown.py /path/to/docs.md --output $PRODUCT_DOCS/product-docs/"
echo ""
echo "  3. If you have Excel integration specs, convert them:"
echo "     pip install openpyxl"
echo "     python scripts/convert-excel.py /path/to/excel-files/"
echo "     mv /path/to/excel-files/*.md $PROJECT_DOCS/integrations/"
echo ""
echo "  4. Edit .env with your API keys and Sterling credentials:"
echo "     nano $ENV_FILE"
echo ""
echo "  5. Source the env and ingest:"
echo "     source $ENV_FILE"
echo "     STERLING_DOCS_DIR=$PRODUCT_DOCS python scripts/ingest-docs.py"
echo "     STERLING_DOCS_DIR=$PROJECT_DOCS python scripts/ingest-docs.py"
echo "     STERLING_DOCS_DIR=$REFERENCES python scripts/ingest-docs.py"
echo ""
echo "  6. Validate and run:"
echo "     swarmkit validate ."
echo "     swarmkit chat . sterling-qa"
