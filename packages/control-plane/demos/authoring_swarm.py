"""Demo: the fleet-UI Authoring page against a stub authoring swarm.

Runs the real control-plane panel, but injects a *stub* authoring function so the
demo doesn't need a live instance running a real authoring topology. Only the swarm's
reply is faked — everything downstream is the real code path: `_extract_artifact`
parses the drafted artifact, the UI previews it, and "Propose for approval" hits the
real `POST /proposals`, so the draft shows up in the Approvals queue.

Run it:

    uv run python packages/control-plane/demos/authoring_swarm.py

Then point the fleet UI at it (packages/control-plane-ui):

    NEXT_PUBLIC_CONTROL_PLANE_API=http://localhost:8842 pnpm dev

and open http://localhost:3000/authoring. A Mode A instance ("edge-alpha") is
pre-enrolled so the instance picker is populated.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._models import Instance

PORT = 8842


async def _stub_author(
    endpoint: str, token_ref: str, topology: str, message: str
) -> dict[str, Any]:
    """Stand-in for a real authoring swarm: draft a topology from the request. A real
    instance would run its `authoring` topology on serve and return the same shape."""
    draft = {
        "kind": "topology",
        "id": "daily-standup-summarizer",
        "content": {
            "apiVersion": "swarmkit/v1",
            "kind": "Topology",
            "metadata": {"id": "daily-standup-summarizer"},
            "nodes": [
                {"id": "collector", "archetype": "researcher"},
                {"id": "summarizer", "archetype": "writer"},
            ],
            "edges": [{"from": "collector", "to": "summarizer"}],
        },
    }
    reply = (
        "Here's a first draft: a two-node topology — a collector that gathers each "
        "member's update, feeding a summarizer that writes the standup digest. "
        "Review it and propose it for approval when it looks right.\n\n" + json.dumps(draft)
    )
    return {"reply": reply, "status": "completed"}


def build() -> Any:
    registry = SqliteRegistry(Path(tempfile.mkdtemp()) / "authoring_demo.sqlite")
    # Pre-enroll a Mode A instance so the UI's instance picker is populated.
    registry.add(
        Instance(
            id="edgealpha001",
            name="edge-alpha",
            endpoint="http://serve.local:8000",
            connection="direct",
            created_at="2026-07-01T09:00:00Z",
        )
    )
    return create_app(registry, author=_stub_author, cors_origins=["http://localhost:3000"])


if __name__ == "__main__":
    print(f"Authoring demo panel → http://localhost:{PORT}  (UI: /authoring)")
    uvicorn.run(build(), host="127.0.0.1", port=PORT, log_level="warning")
