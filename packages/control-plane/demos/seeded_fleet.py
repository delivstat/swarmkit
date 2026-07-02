"""Demo: a control-plane panel seeded with a few instances.

A no-frills launcher for exercising the fleet UI (dashboard, instance selector,
per-instance pages) without standing up real `swarmkit serve` deployments — it just
pre-enrolls a handful of registry entries (a mix of Mode A / Mode B, health states).

Run it:

    uv run python packages/control-plane/demos/seeded_fleet.py

Then point the fleet UI at it (packages/control-plane-ui):

    NEXT_PUBLIC_CONTROL_PLANE_API=http://localhost:8843 pnpm dev
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._models import Instance

PORT = 8843

_FLEET = [
    ("edge-alpha", "direct", "healthy", "http://alpha.local:8000"),
    ("edge-bravo", "direct", "healthy", "http://bravo.local:8000"),
    ("kiosk-charlie", "poll", "stale", "poll://charlie"),
    ("lab-delta", "direct", "unreachable", "http://delta.local:8000"),
]


def build() -> Any:
    registry = SqliteRegistry(Path(tempfile.mkdtemp()) / "seeded_fleet.sqlite")
    for i, (name, connection, health, endpoint) in enumerate(_FLEET):
        registry.add(
            Instance(
                id=f"inst{i:08d}0000"[:12],
                name=name,
                endpoint=endpoint,
                connection=connection,  # type: ignore[arg-type]
                health=health,  # type: ignore[arg-type]
                schema_version="1.6.0",
                created_at="2026-07-02T09:00:00Z",
            )
        )
    return create_app(registry, cors_origins=["http://localhost:3000"])


if __name__ == "__main__":
    print(f"Seeded fleet panel → http://localhost:{PORT}  ({len(_FLEET)} instances)")
    uvicorn.run(build(), host="127.0.0.1", port=PORT, log_level="warning")
