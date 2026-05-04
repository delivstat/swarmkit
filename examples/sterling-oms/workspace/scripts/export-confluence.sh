#!/bin/bash
# Export all pages from a Confluence space as markdown for RAG ingestion.
#
# Uses the Confluence REST API to crawl pages and convert to markdown.
# Output goes to $STERLING_PROJECT_DOCS_DIR/confluence/ for ingestion
# via ingest-docs.py.
#
# Prerequisites:
#   source .env  (needs CONFLUENCE_URL, ATLASSIAN_USERNAME, ATLASSIAN_API_TOKEN)
#
# Usage:
#   ./scripts/export-confluence.sh CSO                    # export space CSO
#   ./scripts/export-confluence.sh CSO /custom/output/    # custom output dir
#   ./scripts/export-confluence.sh CSO "" 500             # custom page limit

set -e

SPACE_KEY="${1:?Usage: export-confluence.sh <SPACE_KEY> [OUTPUT_DIR] [MAX_PAGES]}"
OUTPUT_DIR="${2:-${STERLING_PROJECT_DOCS_DIR}/confluence/${SPACE_KEY}}"
MAX_PAGES="${3:-1000}"

: "${CONFLUENCE_URL:?Set CONFLUENCE_URL (e.g. https://your-site.atlassian.net/wiki)}"
: "${ATLASSIAN_USERNAME:?Set ATLASSIAN_USERNAME (your email)}"
: "${ATLASSIAN_API_TOKEN:?Set ATLASSIAN_API_TOKEN}"

WIKI_URL="${CONFLUENCE_URL}"
AUTH=$(echo -n "${ATLASSIAN_USERNAME}:${ATLASSIAN_API_TOKEN}" | base64)

mkdir -p "$OUTPUT_DIR"

echo "Exporting Confluence space: ${SPACE_KEY}"
echo "  URL: ${WIKI_URL}"
echo "  Output: ${OUTPUT_DIR}"
echo "  Max pages: ${MAX_PAGES}"
echo ""

START=0
LIMIT=25
TOTAL=0
EXPORTED=0

while [ "$TOTAL" -lt "$MAX_PAGES" ]; do
    RESPONSE=$(curl -s -H "Authorization: Basic ${AUTH}" \
        -H "Accept: application/json" \
        "${WIKI_URL}/rest/api/space/${SPACE_KEY}/content/page?limit=${LIMIT}&start=${START}&expand=body.storage,metadata.labels,ancestors")

    PAGE_COUNT=$(echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('results', [])
print(len(results))
" 2>/dev/null || echo "0")

    if [ "$PAGE_COUNT" = "0" ]; then
        break
    fi

    echo "$RESPONSE" | python3 -c "
import sys, json, re, os, html

data = json.load(sys.stdin)
output_dir = '${OUTPUT_DIR}'

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

for page in data.get('results', []):
    title = page.get('title', 'Untitled')
    page_id = page.get('id', '')
    body_html = page.get('body', {}).get('storage', {}).get('value', '')

    ancestors = page.get('ancestors', [])
    breadcrumb = ' > '.join([a.get('title', '') for a in ancestors] + [title])

    labels = []
    label_data = page.get('metadata', {}).get('labels', {}).get('results', [])
    for l in label_data:
        labels.append(l.get('name', ''))

    md = f'# {title}\n\n'
    md += f'**Page ID:** {page_id}\n'
    md += f'**Path:** {breadcrumb}\n'
    if labels:
        md += f'**Labels:** {\", \".join(labels)}\n'
    md += f'\n---\n\n'
    md += html_to_markdown(body_html)

    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '-', title).strip('-')[:80]
    filename = f'{safe_name}.md'
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'w') as f:
        f.write(md)
    print(f'  {filename} ({len(body_html)} chars)')
"

    START=$((START + LIMIT))
    TOTAL=$((TOTAL + PAGE_COUNT))
    EXPORTED=$((EXPORTED + PAGE_COUNT))

    if [ "$PAGE_COUNT" -lt "$LIMIT" ]; then
        break
    fi
done

echo ""
echo "Exported ${EXPORTED} pages to ${OUTPUT_DIR}"
echo ""
echo "Next steps:"
echo "  1. Ingest into ChromaDB:"
echo "     STERLING_DOCS_DIR=${STERLING_PROJECT_DOCS_DIR} uv run scripts/ingest-docs.py"
echo "  2. Or add to ingest-all.py (already picks up project-docs)"
