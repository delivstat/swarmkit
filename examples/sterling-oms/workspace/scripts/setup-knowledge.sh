#!/bin/bash
# Setup knowledge directories for the Sterling OMS workspace.
#
# Creates the folder structure for product docs, project docs,
# reference designs, project code, API javadocs, and notes.
# Generates a .env file with all required environment variables.
#
# Usage:
#   ./scripts/setup-knowledge.sh                    # uses ~/sterling-knowledge as base
#   ./scripts/setup-knowledge.sh /data/sterling      # custom base directory

set -e

BASE="${1:-$HOME/sterling-knowledge}"

PRODUCT_DOCS="$BASE/product-docs"
PROJECT_DOCS="$BASE/project-docs"
CODE_DIR="$BASE/project-code"
API_JAVADOCS="$BASE/api-javadocs"
REFERENCES="$BASE/references"
NOTES_DIR="$BASE/notes"

echo "Setting up Sterling knowledge directories at: $BASE"
echo ""

# Product docs (stable, ingest once into ChromaDB)
mkdir -p "$PRODUCT_DOCS/product-docs"
mkdir -p "$PRODUCT_DOCS/api-reference"
mkdir -p "$PRODUCT_DOCS/data-model"
mkdir -p "$PRODUCT_DOCS/core-javadocs"
mkdir -p "$PRODUCT_DOCS/erd"

# API Javadocs (served by dedicated MCP server — NOT ingested into RAG)
mkdir -p "$API_JAVADOCS"

# Project docs (changes over time, re-ingest as needed)
mkdir -p "$PROJECT_DOCS/design-docs"
mkdir -p "$PROJECT_DOCS/integrations"
mkdir -p "$PROJECT_DOCS/3rd-party-docs"

# Project code (NOT indexed — developer agent reads via filesystem MCP)
mkdir -p "$CODE_DIR/extensions"
mkdir -p "$CODE_DIR/transforms"
mkdir -p "$CODE_DIR/templates"

# Reference designs (separate ChromaDB index, ingest once)
mkdir -p "$REFERENCES/templates"

# Notes/output (agents write analysis here — not git tracked)
mkdir -p "$NOTES_DIR"

echo "Created directories:"
echo ""
echo "  $PRODUCT_DOCS/              (indexed in ChromaDB via ingest-docs.py)"
echo "  ├── product-docs/        ← Sterling product docs (markdown/HTML)"
echo "  ├── api-reference/       ← API summaries (exported from javadocs server)"
echo "  ├── data-model/          ← Entity XMLs → markdown (convert-entity-xml.py)"
echo "  ├── core-javadocs/       ← Core/baseutils javadocs → markdown (convert-javadocs.py)"
echo "  └── erd/                 ← Database ERD HTML files (ingested as-is)"
echo ""
echo "  $API_JAVADOCS/             (dedicated MCP server — NOT indexed in RAG)"
echo "  └── (symlink your api_javadocs directory here)"
echo ""
echo "  $PROJECT_DOCS/             (indexed in ChromaDB via ingest-docs.py)"
echo "  ├── design-docs/         ← Project Word/PDF/markdown docs"
echo "  ├── integrations/        ← Integration specs (Excel → markdown)"
echo "  └── 3rd-party-docs/      ← README/Javadoc for key libraries"
echo ""
echo "  $CODE_DIR/                 (NOT indexed — developer agent reads directly)"
echo "  ├── extensions/          ← Java extension source code"
echo "  ├── transforms/          ← XSL transform files"
echo "  └── templates/           ← XML API/config templates"
echo ""
echo "  $REFERENCES/               (separate ChromaDB index)"
echo "  └── templates/           ← Reusable Sterling patterns from other projects"
echo ""
echo "  $NOTES_DIR/                (agents write analysis, docs here — not git tracked)"
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
export OPENROUTER_API_KEY=

# Sterling CDT config dump
# Point CDT_DIR to your raw CDT XML dump, CDT_INDEX is the parsed output
export STERLING_CDT_DIR=
export STERLING_CDT_INDEX=$BASE/cdt-index

# Knowledge base directories (indexed in ChromaDB)
export STERLING_PRODUCT_DOCS_DIR=$PRODUCT_DOCS
export STERLING_PROJECT_DOCS_DIR=$PROJECT_DOCS
export REFERENCE_DESIGNS_DIR=$REFERENCES

# Project code (developer agent reads directly — NOT indexed)
export STERLING_PROJECT_CODE_DIR=$CODE_DIR

# API Javadocs (dedicated MCP server — structured API access)
export STERLING_JAVADOCS_DIR=$API_JAVADOCS

# Notes/output directory (agents write here — not git tracked)
export STERLING_NOTES_DIR=$NOTES_DIR

# Code knowledge graph (optional)
# Build: uvx --from graphifyy graphify update \$STERLING_PROJECT_CODE_DIR
export STERLING_CODE_GRAPH=$CODE_DIR/graphify-out/graph.json

# GitHub (for code review topology)
export GITHUB_TOKEN=

# Confluence (for project wiki access)
# Get API token: https://id.atlassian.com/manage-profile/security/api-tokens
export CONFLUENCE_URL=https://your-site.atlassian.net/wiki
export CONFLUENCE_USERNAME=your-email@example.com
export CONFLUENCE_API_TOKEN=
ENVEOF

echo "Generated: $ENV_FILE"
echo ""
echo "Next steps:"
echo ""
echo "  1. Copy/symlink your source files:"
echo "     # Product docs (markdown/HTML from IBM Knowledge Center)"
echo "     cp -r /path/to/product-docs/* $PRODUCT_DOCS/product-docs/"
echo ""
echo "     # API Javadocs (for the dedicated MCP server)"
echo "     ln -s /path/to/api_javadocs $API_JAVADOCS/api_javadocs"
echo ""
echo "     # Database ERD HTMLs"
echo "     ln -s /path/to/erd $PRODUCT_DOCS/erd"
echo ""
echo "     # Project code"
echo "     ln -s /path/to/extensions/src $CODE_DIR/extensions"
echo "     ln -s /path/to/transforms $CODE_DIR/transforms"
echo "     ln -s /path/to/templates $CODE_DIR/templates"
echo ""
echo "  2. Convert and prepare data for ingestion:"
echo ""
echo "     # Split large markdown files"
echo "     python scripts/split-markdown.py /path/to/docs.md --output $PRODUCT_DOCS/product-docs/"
echo ""
echo "     # Convert entity XMLs to markdown"
echo "     python scripts/convert-entity-xml.py /path/to/entity-xmls/ \\"
echo "       --datatypes /path/to/datatypes.xml --output $PRODUCT_DOCS/data-model/"
echo ""
echo "     # Convert core/baseutils javadocs to markdown"
echo "     python scripts/convert-javadocs.py /path/to/core_javadocs/ --output $PRODUCT_DOCS/core-javadocs/"
echo "     python scripts/convert-javadocs.py /path/to/baseutils_doc/ --output $PRODUCT_DOCS/core-javadocs/"
echo ""
echo "     # Export API summaries for RAG discovery"
echo "     STERLING_JAVADOCS_DIR=$API_JAVADOCS/api_javadocs \\"
echo "       uv run sterling_javadocs_server.py --export-summaries $PRODUCT_DOCS/api-reference/"
echo ""
echo "     # Convert Excel integration specs to markdown"
echo "     pip install openpyxl"
echo "     python scripts/convert-excel.py /path/to/excel-files/"
echo "     mv /path/to/excel-files/*.md $PROJECT_DOCS/integrations/"
echo ""
echo "  3. Edit .env with your API keys and Sterling credentials:"
echo "     nano $ENV_FILE"
echo ""
echo "  4. Ingest CDT config dump:"
echo "     python scripts/ingest-cdt.py /path/to/CDT/ --output $BASE/cdt-index"
echo ""
echo "  5. Source the env and ingest docs into ChromaDB (~30 min for 20K files):"
echo "     source $ENV_FILE"
echo "     STERLING_DOCS_DIR=$PRODUCT_DOCS uv run scripts/ingest-docs.py"
echo "     STERLING_DOCS_DIR=$PROJECT_DOCS uv run scripts/ingest-docs.py"
echo "     STERLING_DOCS_DIR=$REFERENCES uv run scripts/ingest-docs.py"
echo ""
echo "  6. Build code knowledge graph (optional):"
echo "     uvx --from graphifyy graphify update $CODE_DIR"
echo ""
echo "  7. Validate and run:"
echo "     swarmkit validate ."
echo "     swarmkit chat . sterling-qa"
