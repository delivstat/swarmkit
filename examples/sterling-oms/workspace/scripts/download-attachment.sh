#!/bin/bash
# Download a Jira or Confluence attachment, convert to markdown, and
# store in the review-docs directory for agent review.
#
# Converts: .docx, .xlsx, .pptx → markdown (via markitdown)
#           .xml, .txt, .md, .json, .csv → copied as-is
#           .pdf → copied as-is (agents can read text PDFs)
#
# Prerequisites:
#   source .env
#   uvx markitdown --help  (auto-installed on first use)
#
# Usage:
#   # Jira attachment (get ID from ticket or API)
#   ./scripts/download-attachment.sh jira RT-679 174794
#
#   # Confluence attachment
#   ./scripts/download-attachment.sh confluence 2236875064 my-doc.docx
#
#   # Download ALL attachments from a ticket
#   ./scripts/download-attachment.sh all RT-679
#
#   # List Jira attachments to find the ID
#   ./scripts/download-attachment.sh list RT-679

set -e

CMD="${1:?Usage: download-attachment.sh <jira|confluence|list|all> <issue-or-page-id> [attachment-id-or-name]}"

: "${ATLASSIAN_USERNAME:?Set ATLASSIAN_USERNAME}"
: "${ATLASSIAN_API_TOKEN:?Set ATLASSIAN_API_TOKEN}"
: "${STERLING_REVIEW_DOCS_DIR:?Set STERLING_REVIEW_DOCS_DIR}"

AUTH_ARGS=(-u "${ATLASSIAN_USERNAME}:${ATLASSIAN_API_TOKEN}")
mkdir -p "$STERLING_REVIEW_DOCS_DIR"

# ---- download all attachments ----

if [ "$CMD" = "all" ]; then
    ISSUE_KEY="${2:?Usage: download-attachment.sh all <ISSUE_KEY>}"
    : "${JIRA_URL:?Set JIRA_URL}"

    echo "Downloading all attachments for ${ISSUE_KEY}..."
    echo ""

    ATTACH_IDS=$(curl -s "${AUTH_ARGS[@]}" \
        "${JIRA_URL}/rest/api/3/issue/${ISSUE_KEY}?fields=attachment" | \
        python3 -c "
import sys, json
data = json.load(sys.stdin)
for a in data.get('fields', {}).get('attachment', []):
    print(a['id'])
")

    if [ -z "$ATTACH_IDS" ]; then
        echo "  No attachments found."
        exit 0
    fi

    COUNT=0
    for AID in $ATTACH_IDS; do
        "$0" jira "$ISSUE_KEY" "$AID"
        COUNT=$((COUNT + 1))
        echo ""
    done

    echo "Downloaded ${COUNT} attachments to ${STERLING_REVIEW_DOCS_DIR}"
    exit 0
fi

# ---- list attachments ----

if [ "$CMD" = "list" ]; then
    ISSUE_KEY="${2:?Usage: download-attachment.sh list <ISSUE_KEY>}"
    : "${JIRA_URL:?Set JIRA_URL}"

    echo "Attachments for ${ISSUE_KEY}:"
    echo ""
    curl -s "${AUTH_ARGS[@]}" \
        "${JIRA_URL}/rest/api/3/issue/${ISSUE_KEY}?fields=attachment" | \
        python3 -c "
import sys, json
data = json.load(sys.stdin)
attachments = data.get('fields', {}).get('attachment', [])
if not attachments:
    print('  (none)')
    sys.exit(0)
for a in attachments:
    aid = a['id']
    name = a['filename']
    size = a['size']
    mime = a['mimeType']
    if size > 1048576:
        size_str = f'{size/1048576:.1f}MB'
    elif size > 1024:
        size_str = f'{size/1024:.0f}KB'
    else:
        size_str = f'{size}B'
    print(f'  {aid:>8}  {size_str:>8}  {mime:<60}  {name}')
"
    echo ""
    echo "Download with: ./scripts/download-attachment.sh jira ${ISSUE_KEY} <ID>"
    exit 0
fi

# ---- download jira attachment ----

if [ "$CMD" = "jira" ]; then
    ISSUE_KEY="${2:?Usage: download-attachment.sh jira <ISSUE_KEY> <ATTACHMENT_ID>}"
    ATTACH_ID="${3:?Usage: download-attachment.sh jira <ISSUE_KEY> <ATTACHMENT_ID>}"
    : "${JIRA_URL:?Set JIRA_URL}"

    # Get attachment metadata
    FILENAME=$(curl -s "${AUTH_ARGS[@]}" \
        "${JIRA_URL}/rest/api/3/attachment/${ATTACH_ID}" | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('filename','attachment'))")

    echo "Downloading: ${FILENAME} (attachment ${ATTACH_ID} from ${ISSUE_KEY})"

    TMPFILE=$(mktemp "/tmp/swarmkit-attach-XXXXXX")
    curl -sL "${AUTH_ARGS[@]}" \
        -o "$TMPFILE" \
        "${JIRA_URL}/rest/api/3/attachment/content/${ATTACH_ID}"

    FILESIZE=$(stat -c%s "$TMPFILE" 2>/dev/null || stat -f%z "$TMPFILE" 2>/dev/null || echo "0")
    if [ "$FILESIZE" = "0" ]; then
        echo "  ERROR: Download returned empty file. Check credentials and attachment ID."
        rm -f "$TMPFILE"
        exit 1
    fi
fi

# ---- download confluence attachment ----

if [ "$CMD" = "confluence" ]; then
    PAGE_ID="${2:?Usage: download-attachment.sh confluence <PAGE_ID> <FILENAME>}"
    ATTACH_NAME="${3:?Usage: download-attachment.sh confluence <PAGE_ID> <FILENAME>}"
    : "${CONFLUENCE_URL:?Set CONFLUENCE_URL}"

    FILENAME="$ATTACH_NAME"
    echo "Downloading: ${FILENAME} from Confluence page ${PAGE_ID}"

    # Get download URL from v2 API
    DOWNLOAD_URL=$(curl -s "${AUTH_ARGS[@]}" \
        "${CONFLUENCE_URL}/api/v2/pages/${PAGE_ID}/attachments" | \
        python3 -c "
import sys, json
data = json.load(sys.stdin)
target = '${ATTACH_NAME}'
for a in data.get('results', []):
    if a.get('title', '') == target:
        dl = a.get('downloadLink', '')
        print(dl)
        sys.exit(0)
print('')
")

    if [ -z "$DOWNLOAD_URL" ]; then
        echo "ERROR: Attachment '${ATTACH_NAME}' not found on page ${PAGE_ID}"
        exit 1
    fi

    TMPFILE=$(mktemp "/tmp/swarmkit-attach-XXXXXX")
    curl -sL "${AUTH_ARGS[@]}" \
        -o "$TMPFILE" \
        "${CONFLUENCE_URL}${DOWNLOAD_URL}"
fi

# ---- convert and store ----

EXT="${FILENAME##*.}"
EXT_LOWER=$(echo "$EXT" | tr '[:upper:]' '[:lower:]')
SAFE_NAME=$(echo "$FILENAME" | sed 's/[^a-zA-Z0-9._-]/-/g' | sed 's/--*/-/g' | sed 's/^-//' | sed 's/-$//')

case "$EXT_LOWER" in
    docx|xlsx|pptx)
        echo "Converting ${EXT_LOWER} → markdown..."
        MD_NAME="${SAFE_NAME%.*}.md"
        uvx markitdown "$TMPFILE" > "$STERLING_REVIEW_DOCS_DIR/$MD_NAME" 2>/dev/null
        rm -f "$TMPFILE"
        CHARS=$(wc -c < "$STERLING_REVIEW_DOCS_DIR/$MD_NAME")
        echo "Saved: $STERLING_REVIEW_DOCS_DIR/$MD_NAME (${CHARS} bytes)"
        echo ""
        echo "Review with:"
        echo "  swarmkit chat . sterling-assistant"
        echo "  > Review the document ${MD_NAME}"
        ;;
    xml|txt|md|json|csv|html|htm|xsl|properties|yaml|yml)
        cp "$TMPFILE" "$STERLING_REVIEW_DOCS_DIR/$SAFE_NAME"
        rm -f "$TMPFILE"
        echo "Saved: $STERLING_REVIEW_DOCS_DIR/$SAFE_NAME"
        ;;
    pdf)
        cp "$TMPFILE" "$STERLING_REVIEW_DOCS_DIR/$SAFE_NAME"
        rm -f "$TMPFILE"
        echo "Saved: $STERLING_REVIEW_DOCS_DIR/$SAFE_NAME"
        echo "Note: PDF saved as-is. Text content readable, images not accessible to agents."
        ;;
    *)
        cp "$TMPFILE" "$STERLING_REVIEW_DOCS_DIR/$SAFE_NAME"
        rm -f "$TMPFILE"
        echo "Saved: $STERLING_REVIEW_DOCS_DIR/$SAFE_NAME (no conversion available for .${EXT_LOWER})"
        ;;
esac
