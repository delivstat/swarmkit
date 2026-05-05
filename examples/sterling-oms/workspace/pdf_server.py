# /// script
# dependencies = [
#   "mcp[cli]>=1.0", "pypdf>=4.0", "pdf2image>=1.16",
#   "httpx>=0.27", "weasyprint>=62.0",
# ]
# ///
"""PDF reader MCP server — read any PDF with text extraction and image understanding.

Extracts text per page with page numbers for citations. Optionally
describes diagrams/images using a vision model (Gemini Flash via OpenRouter).

Prerequisites:
    poppler-utils (for pdf2image): apt install poppler-utils
    OPENROUTER_API_KEY (for image description only)

Usage:
    uv run pdf_server.py
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("pdf-reader")

_VISION_MODEL = os.environ.get("PDF_VISION_MODEL", "google/gemini-2.5-flash")
_OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
_CONFLUENCE_URL = os.environ.get("CONFLUENCE_URL", "")
_ATLASSIAN_USER = os.environ.get("ATLASSIAN_USERNAME", "")
_ATLASSIAN_TOKEN = os.environ.get("ATLASSIAN_API_TOKEN", "")
_REVIEW_DOCS_DIR = os.environ.get("STERLING_REVIEW_DOCS_DIR", "") or os.environ.get(
    "STERLING_NOTES_DIR", "/tmp"
)


@server.tool()
def read_pdf(path: str, start_page: int = 1, end_page: int | None = None) -> str:
    """Read text from a PDF file with page numbers for citation.

    Returns text content with page markers. Access any file on the filesystem.
    For large PDFs, specify start_page and end_page to read a range.
    """
    from pypdf import PdfReader  # noqa: PLC0415

    pdf_path = Path(path).expanduser().resolve()
    if not pdf_path.is_file():
        return f"Error: file not found: {path}"

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        return f"Error reading PDF: {exc}"

    total_pages = len(reader.pages)
    if end_page is None:
        end_page = min(start_page + 19, total_pages)
    end_page = min(end_page, total_pages)

    if start_page < 1 or start_page > total_pages:
        return f"Error: start_page {start_page} out of range (PDF has {total_pages} pages)."

    if end_page - start_page + 1 > 30:
        end_page = start_page + 29

    lines = [f"# {pdf_path.name}  (pages {start_page}-{end_page} of {total_pages})\n"]

    for page_num in range(start_page - 1, end_page):
        page = reader.pages[page_num]
        text = page.extract_text() or ""
        lines.append(f"\n--- Page {page_num + 1} ---\n")
        if text.strip():
            lines.append(text.strip())
        else:
            lines.append("(no extractable text — may contain images/diagrams only)")

    return "\n".join(lines)


@server.tool()
def get_pdf_info(path: str) -> str:
    """Get PDF metadata: total pages, title, author, file size."""
    from pypdf import PdfReader  # noqa: PLC0415

    pdf_path = Path(path).expanduser().resolve()
    if not pdf_path.is_file():
        return f"Error: file not found: {path}"

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        return f"Error reading PDF: {exc}"

    meta = reader.metadata
    info = {
        "file": str(pdf_path),
        "pages": len(reader.pages),
        "size_bytes": pdf_path.stat().st_size,
        "title": getattr(meta, "title", None) or "",
        "author": getattr(meta, "author", None) or "",
        "subject": getattr(meta, "subject", None) or "",
    }
    return json.dumps(info, indent=2)


@server.tool()
def describe_pdf_page(path: str, page_number: int) -> str:
    """Describe a PDF page visually using a vision model.

    Renders the page as an image and sends to Gemini Flash for description.
    Use this for pages with diagrams, flowcharts, or architecture images
    that have no extractable text. Requires OPENROUTER_API_KEY.
    """
    if not _OPENROUTER_KEY:
        return "Error: OPENROUTER_API_KEY not set. Required for image description."

    from pdf2image import convert_from_path  # noqa: PLC0415

    pdf_path = Path(path).expanduser().resolve()
    if not pdf_path.is_file():
        return f"Error: file not found: {path}"

    try:
        images = convert_from_path(
            str(pdf_path),
            first_page=page_number,
            last_page=page_number,
            dpi=150,
        )
    except Exception as exc:
        return f"Error rendering page {page_number}: {exc}"

    if not images:
        return f"Error: could not render page {page_number}."

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        images[0].save(tmp, format="PNG")
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
    finally:
        os.unlink(tmp_path)

    import httpx  # noqa: PLC0415

    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {_OPENROUTER_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": _VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe this page from a technical document in detail. "
                                "If it contains a diagram, flowchart, or architecture image, "
                                "describe the components, connections, and flow step by step. "
                                "If it contains text, extract and summarise it. "
                                "Be specific about names, labels, and arrows/connections."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                            },
                        },
                    ],
                }
            ],
        },
        timeout=60,
    )

    if response.status_code != 200:
        return f"Error: vision model returned {response.status_code}: {response.text[:200]}"

    result = response.json()
    description = result["choices"][0]["message"]["content"]

    return f"--- Page {page_number} (visual description) ---\n\n{description}"


@server.tool()
def download_confluence_pdf(page_id: str, filename: str = "") -> str:  # noqa: PLR0911
    """Export a Confluence page as PDF and save to review-docs.

    Fetches rendered HTML via REST API, converts to PDF locally with
    weasyprint (preserves tables, formatting). Returns the saved file
    path. Use after search-confluence to find the page ID.
    """
    if not _CONFLUENCE_URL or not _ATLASSIAN_USER or not _ATLASSIAN_TOKEN:
        return "Error: CONFLUENCE_URL, ATLASSIAN_USERNAME, and ATLASSIAN_API_TOKEN must be set."

    import re  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    auth = (_ATLASSIAN_USER, _ATLASSIAN_TOKEN)

    # Fetch rendered HTML via v1 REST API
    try:
        resp = httpx.get(
            f"{_CONFLUENCE_URL}/rest/api/content/{page_id}?expand=body.view,metadata.labels",
            auth=auth,
            timeout=30,
        )
        if resp.status_code != 200:
            return f"Error: API returned {resp.status_code} for page {page_id}."
        data = resp.json()
    except Exception as exc:
        return f"Error fetching page: {exc}"

    title = data.get("title", f"page-{page_id}")
    body_html = data.get("body", {}).get("view", {}).get("value", "")

    if not body_html:
        return f"Error: Page {page_id} has no content."

    # Inline images as base64 data URIs — weasyprint can't auth to Confluence
    def _inline_images(html: str) -> str:
        img_re = re.compile(r'src="((?:https?://[^"]+|/[^"]+))"', re.IGNORECASE)
        base = _CONFLUENCE_URL.rstrip("/")

        def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
            import html as _html  # noqa: PLC0415

            src = _html.unescape(m.group(1))
            if src.startswith("data:"):
                return m.group(0)
            img_url = src if src.startswith("http") else base + src

            # Try multiple URL variants — thumbnails often 401,
            # but attachment URLs work with auth + redirect
            urls = [img_url]
            if "/thumbnails/" in img_url:
                att_url = img_url.replace("/thumbnails/", "/attachments/")
                att_url = re.sub(r"[&?]width=\d+", "", att_url)
                att_url = re.sub(r"[&?]height=\d+", "", att_url)
                urls.append(att_url)

            for url in urls:
                try:
                    r = httpx.get(
                        url,
                        auth=auth,
                        timeout=15,
                        follow_redirects=True,
                    )
                    if r.status_code == 200 and len(r.content) > 100:
                        ct = r.headers.get("content-type", "image/png")
                        b64 = base64.b64encode(r.content).decode("utf-8")
                        return f'src="data:{ct};base64,{b64}"'
                except Exception:
                    continue
            return m.group(0)

        return img_re.sub(_replace, html)

    body_html = _inline_images(body_html)

    if not filename:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "-", title).strip("-")[:80]
        filename = f"{safe}.pdf"

    output_dir = Path(_REVIEW_DOCS_DIR) / "confluence"
    output_dir.mkdir(parents=True, exist_ok=True)
    outpath = output_dir / filename

    # Wrap in a full HTML document with basic styling
    base_url = _CONFLUENCE_URL.rstrip("/")
    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <base href="{base_url}">
    <style>
        body {{ font-family: -apple-system, sans-serif; font-size: 11pt;
               max-width: 800px; margin: 40px auto; padding: 0 20px; }}
        h1 {{ font-size: 20pt; border-bottom: 1px solid #ccc; padding-bottom: 8px; }}
        h2 {{ font-size: 16pt; margin-top: 24px; }}
        h3 {{ font-size: 13pt; margin-top: 20px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left;
                  font-size: 10pt; }}
        th {{ background-color: #f5f5f5; }}
        code {{ background: #f4f4f4; padding: 2px 4px; font-size: 10pt; }}
        pre {{ background: #f4f4f4; padding: 12px; overflow-x: auto; }}
        img {{ max-width: 100%; height: auto; }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <p style="color: #666; font-size: 9pt;">Page ID: {page_id}</p>
    <hr>
    {body_html}
</body>
</html>"""

    try:
        from weasyprint import HTML  # noqa: PLC0415

        HTML(string=full_html, base_url=base_url).write_pdf(str(outpath))
    except ImportError:
        return "Error: weasyprint not installed. Install with: pip install weasyprint"
    except Exception as exc:
        return f"Error converting to PDF: {exc}"

    size_kb = outpath.stat().st_size / 1024
    return (
        f"Downloaded: {outpath} ({size_kb:.0f} KB)\n"
        f"Read text: read_pdf(path='{outpath}')\n"
        f"Describe diagrams: "
        f"describe_pdf_page(path='{outpath}', page_number=N)"
    )


if __name__ == "__main__":
    server.run()
