"""Demo for the skill ↔ credential reverse index (design/details/skill-requires-credentials.md).

Boots a tiny FastAPI app with a workspace that declares one Gmail credential and two skills:

- `gmail-search-threads` — carries explicit `requires_credentials: [gmail]`.
- `gmail-draft-reply` — relies on the fallback (mcp_tool skill, no `requires_credentials`,
  derived from the server's `credentials_ref`).

Hits `GET /api/oauth/my-credentials` and prints the resulting `used_by` list.

Run with:
    just demo-skill-requires-credentials
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import yaml
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from swarmkit_runtime.auth import NoneAuthProvider
from swarmkit_runtime.oauth import KEY_ENV
from swarmkit_runtime.server._app import create_app


def build_workspace(root: Path) -> None:
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "demo", "name": "Demo"},
                "credentials": {
                    "gmail": {
                        "source": "oauth",
                        "identity": "per-user",
                        "config": {"endpoint": "https://stub.example/mcp"},
                    }
                },
                "mcp_servers": [
                    {
                        "id": "gmail",
                        "transport": "http",
                        "endpoint": "https://stub.example/mcp",
                        "credentials_ref": "gmail",
                        "permission": "readonly",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "topologies").mkdir(exist_ok=True)
    skills = root / "skills"
    skills.mkdir(exist_ok=True)
    (skills / "gmail-search-threads.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Skill",
                "metadata": {
                    "id": "gmail-search-threads",
                    "name": "Gmail — search threads",
                    "description": "Searches Gmail threads via the gmail MCP server.",
                },
                "category": "capability",
                "implementation": {
                    "type": "mcp_tool",
                    "server": "gmail",
                    "tool": "search_threads",
                },
                "requires_credentials": ["gmail"],
                "provenance": {"authored_by": "human", "version": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    (skills / "gmail-draft-reply.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Skill",
                "metadata": {
                    "id": "gmail-draft-reply",
                    "name": "Gmail — draft reply",
                    "description": "Drafts a Gmail reply via the gmail MCP server.",
                },
                "category": "capability",
                "implementation": {
                    "type": "mcp_tool",
                    "server": "gmail",
                    "tool": "draft_reply",
                },
                # No `requires_credentials` — fallback from server's `credentials_ref`.
                "provenance": {"authored_by": "human", "version": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )


def main() -> int:
    os.environ.setdefault(KEY_ENV, Fernet.generate_key().decode())

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        build_workspace(workspace)

        app = create_app(workspace, auth_provider=NoneAuthProvider(identity="owner"))
        with TestClient(app) as client:
            payload = client.get("/api/oauth/my-credentials").json()

        print("─── GET /api/oauth/my-credentials ───────────────────────────────")
        print(json.dumps(payload, indent=2))

        rows = payload["credentials"]
        gmail = next(r for r in rows if r["credential_id"] == "gmail")
        print("\n─── used_by for `gmail` ─────────────────────────────────────────")
        for u in gmail["used_by"]:
            print(f"  · {u['id']}  —  {u['name']}")
        print(
            "\nTwo entries: one explicit (requires_credentials), one via the server "
            "fallback (mcp_servers[].credentials_ref)."
        )
        return 0 if len(gmail["used_by"]) == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
