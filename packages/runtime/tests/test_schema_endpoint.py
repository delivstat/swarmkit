"""GET /api/schema/{artifact_type} — the canonical JSON Schema that drives the UI's schema-generated
designer (design: details/workspace-ui.md, slice 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.mark.parametrize("artifact", ["topology", "skill", "archetype", "workspace", "trigger"])
def test_schema_endpoint_returns_a_json_schema(artifact: str) -> None:
    with TestClient(create_app(EXAMPLE_WS)) as client:
        res = client.get(f"/api/schema/{artifact}")
        assert res.status_code == 200
        schema = res.json()
        assert isinstance(schema, dict)
        # A JSON Schema the form engine can walk (has a shape it can render fields from).
        assert "properties" in schema or "$ref" in schema or "type" in schema


def test_schema_endpoint_404_for_unknown_type() -> None:
    with TestClient(create_app(EXAMPLE_WS)) as client:
        assert client.get("/api/schema/bogus").status_code == 404
