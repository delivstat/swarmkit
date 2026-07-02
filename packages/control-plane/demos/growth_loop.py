"""Demo: the growth-loop automation — signal → surface → propose → test (design 17).

Seeds a fleet with recurring skill-gap signals and stubs the authoring swarm + eval so
the whole loop runs without live instances. Everything but the swarm/eval replies is the
real code path: GET /gaps ranks the gaps, POST /gaps/propose drafts a fix, runs the eval,
and lands a *pending* proposal in the approval queue (the human gate is never bypassed).

Run it:

    uv run python packages/control-plane/demos/growth_loop.py

Then point the fleet UI at it (packages/control-plane-ui):

    NEXT_PUBLIC_CONTROL_PLANE_API=http://localhost:8844 pnpm dev

and open http://localhost:3000/approvals — the "Skill gaps" panel ranks the gaps;
"Draft a fix" turns the top gap into a drafted, eval-tested pending proposal.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._aggregation import AggregationStore
from swarmkit_control_plane._models import Instance

PORT = 8844

_GAPS = [
    ("pdf-extract", ["edgealpha001", "edgebravo002", "edgealpha001"]),
    ("web-search", ["edgebravo002", "edgealpha001"]),
    ("calendar-book", ["edgealpha001"]),
]


async def _stub_author(
    endpoint: str, token_ref: str, topology: str, message: str
) -> dict[str, Any]:
    # The message names the capability; draft a matching skill artifact.
    cap = message.split("'")[1] if "'" in message else "capability"
    draft = {"kind": "skill", "id": cap, "content": {"category": "capability", "provides": cap}}
    return {"reply": f"Drafted a skill for {cap}.\n{json.dumps(draft)}", "status": "completed"}


async def _stub_eval(
    endpoint: str, token_ref: str, eval_topology: str, payload: str
) -> dict[str, Any]:
    return {"passed": 8, "total": 10, "pass_rate": 0.8, "status": "completed"}


def build() -> Any:
    db = Path(tempfile.mkdtemp()) / "growth_loop.sqlite"
    registry = SqliteRegistry(db)
    for i, name in enumerate(("edge-alpha", "edge-bravo")):
        registry.add(
            Instance(
                id=("edgealpha001" if i == 0 else "edgebravo002"),
                name=name,
                endpoint=f"http://{name}.local:8000",
                connection="direct",
                health="healthy",
                created_at="2026-07-02T09:00:00Z",
            )
        )
    agg = AggregationStore(db)
    for cap, instances in _GAPS:
        for n, iid in enumerate(instances):
            agg.ingest(
                iid,
                "gap",
                [
                    {
                        "id": f"{cap}-{iid}-{n}",
                        "capability": cap,
                        "description": f"a worker needed {cap}",
                    }
                ],
            )
    return create_app(
        registry,
        aggregation=agg,
        author=_stub_author,
        eval_run=_stub_eval,
        cors_origins=["http://localhost:3000"],
    )


if __name__ == "__main__":
    print(f"Growth-loop panel → http://localhost:{PORT}  (UI: /approvals)")
    uvicorn.run(build(), host="127.0.0.1", port=PORT, log_level="warning")
