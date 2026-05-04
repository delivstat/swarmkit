#!/bin/bash
# Export all pages from a Confluence Cloud space as markdown for RAG ingestion.
#
# Uses the Confluence Cloud v2 API (REST v1 is deprecated on Cloud).
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

mkdir -p "$OUTPUT_DIR"

echo "Exporting Confluence space: ${SPACE_KEY}"
echo "  URL: ${CONFLUENCE_URL}"
echo "  Output: ${OUTPUT_DIR}"
echo "  Max pages: ${MAX_PAGES}"
echo ""

# Step 1: Get space ID from space key
SPACE_ID=$(curl -s -u "${ATLASSIAN_USERNAME}:${ATLASSIAN_API_TOKEN}" \
    -H "Accept: application/json" \
    "${CONFLUENCE_URL}/api/v2/spaces?keys=${SPACE_KEY}" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d['results'][0]['id'] if d.get('results') else '')")

if [ -z "$SPACE_ID" ]; then
    echo "ERROR: Space '${SPACE_KEY}' not found. Check the space key and your credentials."
    exit 1
fi
echo "  Space ID: ${SPACE_ID}"

# Step 2: Fetch pages using v2 API with cursor pagination
CURSOR=""
EXPORTED=0

while [ "$EXPORTED" -lt "$MAX_PAGES" ]; do
    URL="${CONFLUENCE_URL}/api/v2/spaces/${SPACE_ID}/pages?limit=25&body-format=storage"
    if [ -n "$CURSOR" ]; then
        URL="${URL}&cursor=${CURSOR}"
    fi

    RESPONSE=$(curl -s -u "${ATLASSIAN_USERNAME}:${ATLASSIAN_API_TOKEN}" \
        -H "Accept: application/json" \
        "$URL")

    # Check for errors
    ERROR=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'errors' in d or 'message' in d:
        print(d.get('message', json.dumps(d.get('errors', ''))))
    elif not d.get('results'):
        print('NO_RESULTS')
    else:
        print('')
except: print('PARSE_ERROR')
" 2>/dev/null)

    if [ "$ERROR" = "PARSE_ERROR" ]; then
        echo "ERROR: Failed to parse API response."
        echo "$RESPONSE" | head -5
        exit 1
    fi
    if [ -n "$ERROR" ] && [ "$ERROR" != "NO_RESULTS" ]; then
        echo "ERROR: API returned: $ERROR"
        exit 1
    fi
    if [ "$ERROR" = "NO_RESULTS" ]; then
        break
    fi

    # Process pages
    BATCH_COUNT=$(echo "$RESPONSE" | python3 -c "
import sys, json, re, os, html

data = json.load(sys.stdin)
output_dir = '${OUTPUT_DIR}'
count = 0

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
    status = page.get('status', '')
    body_html = page.get('body', {}).get('storage', {}).get('value', '')
    parent_id = page.get('parentId', '')
    space_id = page.get('spaceId', '')

    md = f'# {title}\n\n'
    md += f'**Page ID:** {page_id}\n'
    md += f'**Space:** ${SPACE_KEY}\n'
    md += f'**Status:** {status}\n'
    if parent_id:
        md += f'**Parent ID:** {parent_id}\n'
    md += f'\n---\n\n'
    md += html_to_markdown(body_html)

    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '-', title).strip('-')[:80]
    filename = f'{safe_name}.md'
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'w') as f:
        f.write(md)
    print(f'  {filename}', file=sys.stderr)
    count += 1

print(count)
" 2>&1)

    # Separate count (stdout) from filenames (stderr)
    PAGE_COUNT=$(echo "$BATCH_COUNT" | tail -1)
    echo "$BATCH_COUNT" | head -n -1

    if ! [[ "$PAGE_COUNT" =~ ^[0-9]+$ ]]; then
        PAGE_COUNT=0
    fi

    EXPORTED=$((EXPORTED + PAGE_COUNT))

    # Get next cursor for pagination
    CURSOR=$(echo "$RESPONSE" | python3 -c "
import sys, json, urllib.parse
d = json.load(sys.stdin)
links = d.get('_links', {})
next_link = links.get('next', '')
if next_link and 'cursor=' in next_link:
    parts = urllib.parse.urlparse(next_link)
    params = urllib.parse.parse_qs(parts.query)
    cursor = params.get('cursor', [''])[0]
    print(cursor)
else:
    print('')
" 2>/dev/null)

    if [ -z "$CURSOR" ]; then
        break
    fi

    echo "  ... ${EXPORTED} pages exported so far"
done

echo ""
echo "Exported ${EXPORTED} pages to ${OUTPUT_DIR}"
echo ""
echo "Next steps:"
echo "  1. Ingest into ChromaDB:"
echo "     STERLING_DOCS_DIR=${STERLING_PROJECT_DOCS_DIR} uv run scripts/ingest-docs.py"
echo "  2. Or add to ingest-all.py (already picks up project-docs)"
