"""Minder Doctor MCP Server — system self-diagnosis & repair.

Exposes Minder's recovery operations as tools a SwarmKit agent can call to
diagnose and fix the appliance: validate state, restore corrupt files from
backup, regenerate derived config, and take backups. Tools delegate to the
webapp's recovery API (the single source of truth for recovery logic), the
same way device actions route through the ops API.
"""

import json
import os
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("minder-doctor")

DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
OPS_URL = os.environ.get("MINDER_API_URL", "http://localhost:80")
INTERNAL_TOKEN_FILE = DATA_DIR / "internal_token"


def _ops(method: str, path: str, body: dict | None = None) -> dict:
    token = INTERNAL_TOKEN_FILE.read_text().strip() if INTERNAL_TOKEN_FILE.exists() else ""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{OPS_URL}{path}", data=data,
        headers={"Content-Type": "application/json", "X-Minder-Internal": token},
        method=method)
    return json.loads(urllib.request.urlopen(req, timeout=180).read())


@mcp.tool()
def system_health() -> str:
    """Check appliance health WITHOUT changing anything: which state files are
    ok/corrupt/missing, whether Home Assistant and Frigate are reachable, and
    how many backups exist."""
    return json.dumps(_ops("GET", "/api/ops/health"))


@mcp.tool()
def diagnose() -> str:
    """Check appliance health and, if anything is wrong, raise an alert asking a
    human to approve a repair. READ-ONLY — this never changes or fixes anything.
    Fixes require explicit human approval (the human replies /repair). Returns
    the health report and whether an approval alert was raised."""
    return json.dumps(_ops("POST", "/api/ops/diagnose"))


@mcp.tool()
def create_backup() -> str:
    """Back up precious state files and the Home Assistant config volume now."""
    return json.dumps({
        "state": _ops("POST", "/api/ops/backup"),
        "ha_volume": _ops("POST", "/api/ops/backup/ha"),
    })


if __name__ == "__main__":
    mcp.run(transport="stdio")
