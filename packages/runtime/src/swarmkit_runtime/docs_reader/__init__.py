"""Document reader MCP server — extract text from PDFs, DOCX, Excel, and more.

See ``design/details/document-reader-mcp.md``.
"""

from ._server import run_server, server

__all__ = ["run_server", "server"]
