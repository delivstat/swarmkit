#!/bin/bash
# Download a Confluence page by URL or ID as PDF (default) or markdown.
#
# PDF preserves images, diagrams, and formatting. Use for design reviews.
# Markdown is text-only but searchable by agents via ChromaDB.
#
# Usage:
#   # Download as PDF (default — preserves images)
#   ./scripts/download-confluence-page.sh "https://tatacroma.atlassian.net/wiki/spaces/CSO/pages/2236875064"
#
#   # Download as markdown (for RAG ingestion)
#   ./scripts/download-confluence-page.sh 2236875064 --md
#
#   # Save to notes directory
#   ./scripts/download-confluence-page.sh 2236875064 --notes
#
#   # Both flags
#   ./scripts/download-confluence-page.sh 2236875064 --md --notes

set -e

INPUT=""
FORMAT="pdf"
DEST="docs"

for arg in "$@"; do
    case "$arg" in
        --md|--markdown) FORMAT="md" ;;
        --notes) DEST="notes" ;;
        *) INPUT="$arg" ;;
    esac
done

if [ -z "$INPUT" ]; then
    echo "Usage: download-confluence-page.sh <URL or PAGE_ID> [--md] [--notes]"
    echo ""
    echo "Options:"
    echo "  --md      Download as markdown instead of PDF"
    echo "  --notes   Save to notes directory instead of project-docs"
    exit 1
fi

: "${CONFLUENCE_URL:?Set CONFLUENCE_URL (e.g. https://your-site.atlassian.net/wiki)}"
: "${ATLASSIAN_USERNAME:?Set ATLASSIAN_USERNAME}"
: "${ATLASSIAN_API_TOKEN:?Set ATLASSIAN_API_TOKEN}"

AUTH_ARGS=(-u "${ATLASSIAN_USERNAME}:${ATLASSIAN_API_TOKEN}")

# Extract page ID from URL or use directly
if echo "$INPUT" | grep -q "pages/"; then
    PAGE_ID=$(echo "$INPUT" | grep -oP 'pages/\K[0-9]+')
elif echo "$INPUT" | grep -qE "^[0-9]+$"; then
    PAGE_ID="$INPUT"
else
    echo "ERROR: Cannot extract page ID from: $INPUT"
    echo "Expected a Confluence URL with /pages/<id> or a numeric page ID."
    exit 1
fi

if [ -z "$PAGE_ID" ]; then
    echo "ERROR: Could not parse page ID from input."
    exit 1
fi

# Determine output directory
if [ "$DEST" = "notes" ]; then
    : "${STERLING_NOTES_DIR:?Set STERLING_NOTES_DIR}"
    OUTPUT_DIR="$STERLING_NOTES_DIR"
else
    : "${STERLING_PROJECT_DOCS_DIR:?Set STERLING_PROJECT_DOCS_DIR}"
    OUTPUT_DIR="$STERLING_PROJECT_DOCS_DIR/confluence"
fi
mkdir -p "$OUTPUT_DIR"

# Get page title for filename
TITLE=$(curl -s "${AUTH_ARGS[@]}" \
    -H "Accept: application/json" \
    "${CONFLUENCE_URL}/api/v2/pages/${PAGE_ID}" | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('title','page-${PAGE_ID}'))" 2>/dev/null)

SAFE_NAME=$(echo "$TITLE" | sed 's/[^a-zA-Z0-9_-]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//' | cut -c1-80)

echo "Downloading: ${TITLE} (page ${PAGE_ID})"
echo "  Format: ${FORMAT}"

if [ "$FORMAT" = "pdf" ]; then
    # Export as PDF via Confluence Cloud export endpoint
    OUTFILE="$OUTPUT_DIR/${SAFE_NAME}.pdf"

    # Confluence Cloud PDF export: /wiki/spaces/{spaceKey}/pdfpageexport.action?pageId={id}
    # This returns a PDF directly
    SPACE_KEY=$(curl -s "${AUTH_ARGS[@]}" \
        -H "Accept: application/json" \
        "${CONFLUENCE_URL}/api/v2/pages/${PAGE_ID}" | \
        python3 -c "
import sys, json
d = json.load(sys.stdin)
space_id = d.get('spaceId', '')
# Need to get space key from space ID
print(space_id)
" 2>/dev/null)

    # Step 1: Trigger PDF export — get Location header with download path
    PDF_LOCATION=$(curl -s -o /dev/null -w '%{redirect_url}' \
        "${AUTH_ARGS[@]}" \
        -H "X-Atlassian-Token: no-check" \
        "${CONFLUENCE_URL}/spaces/flyingpdf/pdfpageexport.action?pageId=${PAGE_ID}")

    if [ -z "$PDF_LOCATION" ]; then
        # No redirect — try reading the response body for a download link
        STEP1_BODY=$(curl -s "${AUTH_ARGS[@]}" \
            -H "X-Atlassian-Token: no-check" \
            "${CONFLUENCE_URL}/spaces/flyingpdf/pdfpageexport.action?pageId=${PAGE_ID}")

        PDF_LOCATION=$(echo "$STEP1_BODY" | grep -oP '/download/[^"'"'"'>\s]+\.pdf' | head -1)

        if [ -n "$PDF_LOCATION" ]; then
            # Make relative URL absolute
            BASE_ROOT=$(echo "$CONFLUENCE_URL" | sed 's|/wiki.*||')
            PDF_LOCATION="${BASE_ROOT}${PDF_LOCATION}"
        fi
    fi

    if [ -n "$PDF_LOCATION" ]; then
        # Step 2: Download the actual PDF
        curl -sL "${AUTH_ARGS[@]}" \
            -H "X-Atlassian-Token: no-check" \
            -o "$OUTFILE" \
            "$PDF_LOCATION"
    fi

    FILESIZE=$(stat -c%s "$OUTFILE" 2>/dev/null || stat -f%z "$OUTFILE" 2>/dev/null || echo "0")

    if [ "$FILESIZE" -lt 500 ] || ! head -c 4 "$OUTFILE" | grep -q "%PDF"; then
        rm -f "$OUTFILE" 2>/dev/null
        echo "  ERROR: PDF export not available. Falling back to markdown."
            FORMAT="md"
        fi
    fi

    if [ "$FORMAT" = "pdf" ]; then
        echo "  Saved: $OUTFILE (${FILESIZE} bytes)"
        exit 0
    fi
fi

# Markdown export (fallback or explicit --md)
OUTFILE="$OUTPUT_DIR/${SAFE_NAME}.md"

RESPONSE=$(curl -s "${AUTH_ARGS[@]}" \
    -H "Accept: application/json" \
    "${CONFLUENCE_URL}/api/v2/pages/${PAGE_ID}?body-format=storage")

echo "$RESPONSE" | python3 -c "
import sys, json, re, html, os

data = json.load(sys.stdin)
title = data.get('title', 'Untitled')
page_id = data.get('id', '')
version = data.get('version', {}).get('number', '')
created = data.get('createdAt', '')
parent_id = data.get('parentId', '')
body_html = data.get('body', {}).get('storage', {}).get('value', '')

def html_to_markdown(h):
    h = re.sub(r'<script[^>]*>.*?</script>', '', h, flags=re.DOTALL)
    h = re.sub(r'<style[^>]*>.*?</style>', '', h, flags=re.DOTALL)
    h = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1\n', h, flags=re.DOTALL)
    h = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1\n', h, flags=re.DOTALL)
    h = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1\n', h, flags=re.DOTALL)
    h = re.sub(r'<h4[^>]*>(.*?)</h4>', r'#### \1\n', h, flags=re.DOTALL)
    h = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', h, flags=re.DOTALL)
    h = re.sub(r'<br\s*/?>', '\n', h, flags=re.IGNORECASE)
    h = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', h, flags=re.DOTALL)
    h = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', h, flags=re.DOTALL)
    h = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', h, flags=re.DOTALL)
    h = re.sub(r'<code[^>]*>(.*?)</code>', r'\`\1\`', h, flags=re.DOTALL)
    h = re.sub(r'<a[^>]*href=\"([^\"]*)\"[^>]*>(.*?)</a>', r'[\2](\1)', h, flags=re.DOTALL)
    h = re.sub(r'<[^>]+>', '', h)
    h = html.unescape(h)
    h = re.sub(r'\n{3,}', '\n\n', h)
    return h.strip()

md = f'# {title}\n\n'
md += f'**Page ID:** {page_id}\n'
md += f'**Version:** {version}\n'
md += f'**Created:** {created}\n'
if parent_id:
    md += f'**Parent ID:** {parent_id}\n'
md += f'\n---\n\n'
md += html_to_markdown(body_html)

filepath = '${OUTFILE}'
with open(filepath, 'w') as f:
    f.write(md)
print(f'  Saved: {filepath} ({len(md)} chars)')
print(f'  Note: Images/diagrams not included in markdown. Use without --md for PDF.')
"
