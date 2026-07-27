"""CLI ⇄ serve parity for governed memory (design/details/governed-memory.md).

`swarmkit memory …` and the `/memory` endpoints resolve the store through the SAME service seam —
``WorkspaceRuntime.from_workspace_path(...).governed_memory`` — and emit the same JSON. The store is
seeded via a fake reconciler (keyless); the CLI + serve read through the real service.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.cli import app as cli_app
from swarmkit_runtime.governed_memory import (
    GovernedMemoryStore,
    MemoryCandidate,
    ReconcileVerdict,
)
from swarmkit_runtime.server._routes_memory import _register_memory_routes
from typer.testing import CliRunner

_runner = CliRunner()
REFERENCE_WS = Path(__file__).resolve().parents[3] / "reference"


async def _contradict(_req: Any) -> ReconcileVerdict:
    return ReconcileVerdict(op="contradict", reasoning="conflicts")


def _make_ws(root: Path) -> None:
    """A minimal workspace that declares governed memory — resolvable by from_workspace_path."""
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\n"
        "metadata:\n  id: mem-test\n  name: Mem Test\n  description: governed-memory parity test.\n"
    )
    for skill in ("governed-memory.yaml", "memory-reconcile.yaml"):
        shutil.copy(REFERENCE_WS / "skills" / skill, root / "skills" / skill)


def _seed(root: Path) -> GovernedMemoryStore:
    """Seed the workspace store via a fake reconciler (keyless); same DB the service reads."""
    store = GovernedMemoryStore.for_workspace(root, reconciler=_contradict)
    store.write(MemoryCandidate(subject="user:alice", attribute="lang", value="Python"))
    store.write(MemoryCandidate(subject="user:alice", attribute="editor", value="neovim"))
    return store


def _serve(root: Path) -> TestClient:
    app = FastAPI()
    app.state.runtime = WorkspaceRuntime.from_workspace_path(root)  # the real service seam
    _register_memory_routes(app)
    return TestClient(app)


# ── search parity ────────────────────────────────────────────────────────────────────────────────
def test_search_cli_and_serve_agree(tmp_path: Path) -> None:
    _make_ws(tmp_path)
    _seed(tmp_path)
    cli = _runner.invoke(cli_app, ["memory", "search", "python", "-w", str(tmp_path), "--json"])
    assert cli.exit_code == 0, cli.stdout
    cli_hits = json.loads(cli.stdout)
    assert [m["attribute"] for m in cli_hits] == ["lang"]

    serve = _serve(tmp_path).get("/memory", params={"query": "python"}).json()["memories"]
    assert serve == cli_hits  # same service, same JSON


def test_get_with_history_parity(tmp_path: Path) -> None:
    _make_ws(tmp_path)
    store = _seed(tmp_path)
    store.write(MemoryCandidate(subject="user:alice", attribute="lang", value="Rust"))  # update

    cli = _runner.invoke(
        cli_app, ["memory", "get", "user:alice", "lang", "-w", str(tmp_path), "--history", "--json"]
    )
    cli_payload = json.loads(cli.stdout)
    assert cli_payload["current"]["value"] == "Rust"
    assert [e["op"] for e in cli_payload["history"]] == ["new", "update"]

    serve = (
        _serve(tmp_path)
        .get(
            "/memory/item",
            params={"subject": "user:alice", "attribute": "lang", "history": True},
        )
        .json()
    )
    assert serve == cli_payload


# ── quarantine + resolve parity ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_quarantine_and_resolve_parity(tmp_path: Path) -> None:
    _make_ws(tmp_path)
    store = _seed(tmp_path)
    await store.awrite(MemoryCandidate(subject="user:alice", attribute="lang", value="assembly"))
    assert len(store.list_quarantine()) == 1

    cli = _runner.invoke(cli_app, ["memory", "quarantine", "-w", str(tmp_path), "--json"])
    q = json.loads(cli.stdout)
    assert len(q) == 1 and q[0]["candidate"]["value"] == "assembly"

    serve_q = _serve(tmp_path).get("/memory/quarantine").json()["quarantine"]
    assert serve_q == q  # same service, same JSON

    resp = _serve(tmp_path).post(
        f"/memory/quarantine/{q[0]['id']}/resolve",
        json={"resolved_by": "curator:me", "accept": False},
    )
    assert resp.status_code == 200 and resp.json()["accepted"] is False
    assert store.get("user:alice", "lang").value == "Python"  # type: ignore[union-attr]
    assert store.list_quarantine(status="rejected")[0].resolved_by == "curator:me"


def test_serve_404_when_no_governed_memory(tmp_path: Path) -> None:
    app = FastAPI()

    class _Empty:
        governed_memory = None

    app.state.runtime = _Empty()
    _register_memory_routes(app)
    assert TestClient(app).get("/memory").status_code == 404
