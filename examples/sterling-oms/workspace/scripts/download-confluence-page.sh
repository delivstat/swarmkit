#!/bin/bash
# Download a Confluence page by URL and save as markdown to project-docs or notes.
#
# Fetches the page content via v2 API, converts HTML to markdown, and saves
# to the specified output directory (defaults to project-docs/confluence/).
#
# Usage:
#   # Download by URL
#   ./scripts/download-confluence-page.sh "https://tatacroma.atlassian.net/wiki/spaces/CSO/pages/2236875064"
#
#   # Download to notes directory instead
#   ./scripts/download-confluence-page.sh "https://tatacroma.atlassian.net/wiki/spaces/CSO/pages/2236875064" notes
#
#   # Download by page ID directly
#   ./scripts/download-confluence-page.sh 2236875064
#   ./scripts/download-confluence-page.sh 2236875064 notes

set -e

INPUT="${1:?Usage: download-confluence-page.sh <URL or PAGE_ID> [notes|docs]}"
DEST="${2:-docs}"

: "${CONFLUENCE_URL:?Set CONFLUENCE_URL (e.g. https://your-site.atlassian.net/wiki)}"
: "${ATLASSIAN_USERNAME:?Set ATLASSIAN_USERNAME}"
: "${ATLASSIAN_API_TOKEN:?Set ATLASSIAN_API_TOKEN}"

AUTH_ARGS=(-u "${ATLASSIAN_USERNAME}:${ATLASSIAN_API_TOKEN}")

# Extract page ID from URL or use directly
if echo "$INPUT" | grep -q "pages/"; then
    PAGE_ID=$(echo "$INPUT" | grep -oP 'pages/\K[0-9]+')
elif echo "$INPUT" | grep -q "^[0-9]*$"; then
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

echo "Fetching Confluence page ${PAGE_ID}..."

# Fetch page with body content
RESPONSE=$(curl -s "${AUTH_ARGS[@]}" \
    -H "Accept: application/json" \
    "${CONFLUENCE_URL}/api/v2/pages/${PAGE_ID}?body-format=storage")

# Check for errors
ERROR=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'errors' in d:
        print(json.dumps(d['errors']))
    elif 'message' in d:
        print(d['message'])
    elif not d.get('id'):
        print('Unknown error - no page ID in response')
    else:
        print('')
except Exception as e:
    print(f'Parse error: {e}')
" 2>/dev/null)

if [ -n "$ERROR" ]; then
    echo "ERROR: $ERROR"
    exit 1
fi

# Convert to markdown and save
echo "$RESPONSE" | python3 -c "
import sys, json, re, html

data = json.load(sys.stdin)
title = data.get('title', 'Untitled')
page_id = data.get('id', '')
space_id = data.get('spaceId', '')
status = data.get('status', '')
parent_id = data.get('parentId', '')
body_html = data.get('body', {}).get('storage', {}).get('value', '')
version = data.get('version', {}).get('number', '')
created = data.get('createdAt', '')
author = data.get('authorId', '')

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
    h = re.sub(r'<table[^>]*>(.*?)</table>', lambda m: _table_to_md(m.group(1)), h, flags=re.DOTALL)
    h = re.sub(r'<[^>]+>', '', h)
    h = html.unescape(h)
    h = re.sub(r'\n{3,}', '\n\n', h)
    return h.strip()

def _table_to_md(table_html):
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
    if not rows:
        return ''
    md_rows = []
    for i, row in enumerate(rows):
        cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        md_rows.append('| ' + ' | '.join(cells) + ' |')
        if i == 0:
            md_rows.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
    return '\n'.join(md_rows) + '\n'

md = f'# {title}\n\n'
md += f'**Page ID:** {page_id}\n'
md += f'**Version:** {version}\n'
md += f'**Created:** {created}\n'
if parent_id:
    md += f'**Parent ID:** {parent_id}\n'
md += f'\n---\n\n'
md += html_to_markdown(body_html)

safe_name = re.sub(r'[^a-zA-Z0-9_-]', '-', title).strip('-')[:80]
filename = f'{safe_name}.md'

import os
filepath = os.path.join('${OUTPUT_DIR}', filename)
with open(filepath, 'w') as f:
    f.write(md)

chars = len(md)
print(f'  Title: {title}')
print(f'  Saved: {filepath} ({chars} chars)')
"
