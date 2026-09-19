"""Topology input_schema: validate-and-reject at the entry, before any agent runs (input-schema.md).

Covers the pure check, the WorkspaceRuntime.run choke point (which the CLI/A2A inherit), and the
serve 422 — including that a rejected request never becomes a job (no LLM spend).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from swarmkit_runtime._input_schema import InputValidationError, check_entry_input

_WS = (
    "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: t, name: T}\n"
    "governance: {provider: mock}\n"
)


def _topo(input_schema_yaml: str) -> str:
    return (
        "apiVersion: swarmkit/v1\nkind: Topology\nmetadata: {name: triage, version: 0.1.0}\n"
        f"{input_schema_yaml}"
        "agents:\n  root: {id: g, role: root, model: {provider: mock, name: m},"
        " prompt: {system: hi}}\n"
    )


_OBJECT_SCHEMA = (
    "input_schema:\n"
    "  type: object\n"
    "  required: [ticket_id, severity]\n"
    "  properties:\n"
    "    ticket_id: {type: string}\n"
    "    severity: {enum: [P0, P1, P2]}\n"
)


# ---- the pure check -------------------------------------------------------


def test_object_schema_rejects_non_json() -> None:
    schema = {"type": "object", "required": ["ticket_id"], "properties": {"ticket_id": {}}}
    with pytest.raises(InputValidationError, match="must be a JSON object"):
        check_entry_input("just some prose", schema)


def test_object_schema_rejects_missing_required_field_naming_it() -> None:
    schema = {"type": "object", "required": ["ticket_id"], "properties": {"ticket_id": {}}}
    with pytest.raises(InputValidationError, match="ticket_id"):
        check_entry_input('{"other": 1}', schema)


def test_string_schema_accepts_plain_text() -> None:
    check_entry_input("hello there", {"type": "string", "minLength": 1})  # no raise


def test_valid_object_passes() -> None:
    check_entry_input(
        '{"ticket_id": "T-1", "severity": "P0"}',
        {"type": "object", "required": ["ticket_id"], "properties": {"severity": {"enum": ["P0"]}}},
    )


def test_no_schema_is_a_noop() -> None:
    check_entry_input("anything", None)  # no raise


# ---- WorkspaceRuntime.run choke point (CLI/A2A inherit this) --------------


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "triage.yaml").write_text(_topo(_OBJECT_SCHEMA))
    return tmp_path


def test_run_rejects_before_executing_and_audits(ws: Path) -> None:
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    rt = WorkspaceRuntime.from_workspace_path(ws)
    with pytest.raises(InputValidationError, match="severity"):
        asyncio.run(rt.run("triage", '{"ticket_id": "T-1", "severity": "nope"}'))


def test_run_accepts_valid_input(ws: Path) -> None:
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    rt = WorkspaceRuntime.from_workspace_path(ws)
    result = asyncio.run(rt.run("triage", '{"ticket_id": "T-1", "severity": "P0"}'))
    assert result.output is not None


# ---- serve 422 + never-becomes-a-job -------------------------------------


async def _post(app: Any, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return await client.post("/run/triage", json=body)


@pytest.mark.asyncio
async def test_serve_rejects_with_422_and_creates_no_job(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws)
    async with app.router.lifespan_context(app):
        r = await _post(app, {"input": '{"ticket_id": "T-1"}'})  # missing required severity
        assert r.status_code == 422, r.text
        assert "severity" in r.text
        # No LLM spend: the malformed request never became a job.
        assert await app.state.job_store.list_all() == []
        assert app.state.store.count_jobs("pending") == 0


@pytest.mark.asyncio
async def test_serve_rejects_non_json_for_object_schema(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws)
    async with app.router.lifespan_context(app):
        r = await _post(app, {"input": "free text, not json"})
        assert r.status_code == 422
        assert "JSON object" in r.text
