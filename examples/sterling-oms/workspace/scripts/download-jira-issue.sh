#!/bin/bash
# Download a Jira issue as markdown with comments and attachment list.
#
# Fetches issue details, comments, and attachment metadata via REST API.
# Saves as markdown to the review-docs directory.
#
# Usage:
#   ./scripts/download-jira-issue.sh RT-679
#   ./scripts/download-jira-issue.sh RT-679 --with-attachments   # also download all attachments
#   ./scripts/download-jira-issue.sh RT-679 --notes              # save to notes dir instead

set -e

ISSUE_KEY=""
WITH_ATTACHMENTS=false
DEST="docs"

for arg in "$@"; do
    case "$arg" in
        --with-attachments) WITH_ATTACHMENTS=true ;;
        --notes) DEST="notes" ;;
        *) ISSUE_KEY="$arg" ;;
    esac
done

if [ -z "$ISSUE_KEY" ]; then
    echo "Usage: download-jira-issue.sh <ISSUE_KEY> [--with-attachments] [--notes]"
    echo ""
    echo "Options:"
    echo "  --with-attachments  Also download all file attachments"
    echo "  --notes             Save to notes directory instead of review-docs"
    exit 1
fi

: "${JIRA_URL:?Set JIRA_URL (e.g. https://your-site.atlassian.net)}"
: "${ATLASSIAN_USERNAME:?Set ATLASSIAN_USERNAME}"
: "${ATLASSIAN_API_TOKEN:?Set ATLASSIAN_API_TOKEN}"

AUTH_ARGS=(-u "${ATLASSIAN_USERNAME}:${ATLASSIAN_API_TOKEN}")

if [ "$DEST" = "notes" ]; then
    : "${STERLING_NOTES_DIR:?Set STERLING_NOTES_DIR}"
    OUTPUT_DIR="$STERLING_NOTES_DIR"
else
    : "${STERLING_REVIEW_DOCS_DIR:?Set STERLING_REVIEW_DOCS_DIR}"
    OUTPUT_DIR="$STERLING_REVIEW_DOCS_DIR/$ISSUE_KEY"
fi
mkdir -p "$OUTPUT_DIR"

echo "Downloading Jira issue: ${ISSUE_KEY}"

# Fetch issue with all fields + comments + attachment metadata
RESPONSE=$(curl -s "${AUTH_ARGS[@]}" \
    -H "Accept: application/json" \
    "${JIRA_URL}/rest/api/3/issue/${ISSUE_KEY}?expand=renderedFields")

ERROR=$(echo "$RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'errorMessages' in d:
        print(d['errorMessages'][0])
    elif 'key' not in d:
        print('Unknown error')
    else:
        print('')
except: print('Parse error')
" 2>/dev/null)

if [ -n "$ERROR" ]; then
    echo "ERROR: $ERROR"
    exit 1
fi

# Convert to markdown
OUTFILE="$OUTPUT_DIR/${ISSUE_KEY}.md"

echo "$RESPONSE" | python3 -c "
import sys, json, re, html

data = json.load(sys.stdin)
fields = data.get('fields', {})
rendered = data.get('renderedFields', {})
key = data.get('key', '')

title = fields.get('summary', 'Untitled')
status = fields.get('status', {}).get('name', '')
priority = fields.get('priority', {}).get('name', '')
issue_type = fields.get('issuetype', {}).get('name', '')
assignee = (fields.get('assignee') or {}).get('displayName', 'Unassigned')
reporter = (fields.get('reporter') or {}).get('displayName', '')
created = fields.get('created', '')[:10]
updated = fields.get('updated', '')[:10]
labels = fields.get('labels', [])

# Description — use rendered HTML, convert to text
desc_html = rendered.get('description', '') or ''
def html_to_text(h):
    if not h:
        return ''
    h = re.sub(r'<h[1-4][^>]*>(.*?)</h[1-4]>', r'### \1\n', h, flags=re.DOTALL)
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

desc = html_to_text(desc_html)

# Comments
comments = fields.get('comment', {}).get('comments', [])

# Attachments
attachments = fields.get('attachment', [])

# Build markdown
md = f'# {key}: {title}\n\n'
md += f'| Field | Value |\n|---|---|\n'
md += f'| **Status** | {status} |\n'
md += f'| **Priority** | {priority} |\n'
md += f'| **Type** | {issue_type} |\n'
md += f'| **Assignee** | {assignee} |\n'
md += f'| **Reporter** | {reporter} |\n'
md += f'| **Created** | {created} |\n'
md += f'| **Updated** | {updated} |\n'
if labels:
    md += f'| **Labels** | {\", \".join(labels)} |\n'
md += '\n'

if desc:
    md += f'## Description\n\n{desc}\n\n'

if comments:
    md += f'## Comments ({len(comments)})\n\n'
    for c in comments:
        author = c.get('author', {}).get('displayName', '')
        date = c.get('created', '')[:10]
        body_html = c.get('renderedBody', c.get('body', ''))
        if isinstance(body_html, dict):
            body_text = str(body_html)
        else:
            body_text = html_to_text(str(body_html))
        md += f'**{author}** ({date}):\n{body_text}\n\n---\n\n'

if attachments:
    md += f'## Attachments ({len(attachments)})\n\n'
    md += '| ID | Filename | Size | Type | Author | Date |\n'
    md += '|---|---|---|---|---|---|\n'
    for a in attachments:
        aid = a.get('id', '')
        name = a.get('filename', '')
        size = a.get('size', 0)
        if size > 1048576:
            size_str = f'{size/1048576:.1f}MB'
        elif size > 1024:
            size_str = f'{size/1024:.0f}KB'
        else:
            size_str = f'{size}B'
        mime = a.get('mimeType', '')
        author = a.get('author', {}).get('displayName', '')
        date = a.get('created', '')[:10]
        md += f'| {aid} | {name} | {size_str} | {mime} | {author} | {date} |\n'
    md += '\n'
    md += 'Download attachments with:\n'
    md += f'  ./scripts/download-attachment.sh all {key}\n'

with open('${OUTFILE}', 'w') as f:
    f.write(md)
print(f'  Saved: ${OUTFILE} ({len(md)} chars)')
print(f'  Title: {title}')
print(f'  Status: {status}')
print(f'  Comments: {len(comments)}')
print(f'  Attachments: {len(attachments)}')
"

# Download attachments if requested
if [ "$WITH_ATTACHMENTS" = true ]; then
    echo ""
    echo "Downloading attachments..."
    ./scripts/download-attachment.sh all "$ISSUE_KEY"
fi

echo ""
echo "Done. Review with:"
echo "  swarmkit chat . sterling-assistant"
echo "  > Read the file at ${OUTFILE}"
