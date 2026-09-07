"""`swarmkit memory add` — putting a fact into governed memory through the governed path.

`design/details/seeding-governed-memory.md`. The store's only write path was the persistence-skill
hook, so a fact entered memory only when an agent chose to emit it mid-run — and the facts most
worth keeping are the ones discovered incidentally by an agent doing something else.

The assertion that matters most is that a `contradict` does **not** read as success. The trusted
memory is deliberately left untouched, so a command printing "added" would state the opposite of
what happened.
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.cli import _cmd_memory
from swarmkit_runtime.cli._app import app
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.governed_memory import GovernedMemoryStore, MemoryCandidate
from swarmkit_runtime.server import create_app
from typer.testing import CliRunner

runner = CliRunner()
REPO = Path(__file__).resolve().parents[3]

WORKSPACE = (
    "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: m, name: M}\n"
    "governance: {provider: mock, policy_language: yaml}\n"
)


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    (tmp_path / "workspace.yaml").write_text(WORKSPACE)
    # The store exists only for a workspace declaring `governed-memory` — which is the behaviour
    # `test_a_workspace_without_governed_memory_is_refused` covers from the other side.
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "governed-memory.yaml").write_text(
        (REPO / "reference/skills/governed-memory.yaml").read_text()
    )
    return tmp_path


def test_a_workspace_without_governed_memory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse naming the skill, rather than failing obscurely: the store is None, the agent lacks
    the grant, or the model never emits — and every one looks identical to "nothing to remember"."""
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    (tmp_path / "workspace.yaml").write_text(WORKSPACE)
    result = runner.invoke(app, ["memory", "add", "a", "b", "c", "-w", str(tmp_path)])
    assert result.exit_code != 0
    assert "governed-memory" in result.stderr


def add(ws: Path, *args: str) -> Any:
    return runner.invoke(app, ["memory", "add", *args, "-w", str(ws)])


# ---- the reconcile ops, reported honestly ------------------------------------------------------


def test_first_write_is_new_then_reinforce_then_update(ws: Path) -> None:
    first = add(ws, "sn8", "carton-count-source", "From the TASK LIST.")
    assert first.exit_code == 0
    assert "new" in first.stdout

    same = add(ws, "sn8", "carton-count-source", "From the TASK LIST.")
    assert same.exit_code == 0
    assert "reinforce" in same.stdout

    changed = add(ws, "sn8", "carton-count-source", "From YFS_SHIPMENT_CONTAINER.")
    assert changed.exit_code == 0
    assert "update" in changed.stdout


def test_the_op_is_printed_never_a_bare_added(ws: Path) -> None:
    """`write()` reconciles; the command reports which branch happened. "added" would hide it."""
    result = add(ws, "a", "b", "c")
    assert "added" not in result.stdout.lower()
    assert "new" in result.stdout


def test_a_contradiction_is_not_reported_as_success(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE assertion. A contradict leaves the trusted memory untouched and quarantines the
    candidate, so a zero exit would tell a seeding script the fact landed when it did not."""

    class _Contradicting:
        """A store whose write always contradicts, standing in for a wired reconciler."""

        def write(self, candidate: MemoryCandidate) -> Any:
            return type("O", (), {"op": "contradict", "changed": False})()

    monkeypatch.setattr(_cmd_memory, "_store", lambda _w: _Contradicting())
    result = add(ws, "sn8", "carton-count-source", "Something incompatible.")
    assert result.exit_code != 0
    assert "NOT written" in result.stderr
    assert "quarantined" in result.stderr
    assert "added" not in result.stdout.lower()


# ---- bulk ---------------------------------------------------------------------------------------


def test_from_file_takes_the_agents_own_format(ws: Path, tmp_path: Path) -> None:
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            {
                "memories": [
                    {"subject": "sn8", "attribute": "labels", "value": "labels are global"},
                    {"subject": "sn8", "attribute": "wiring", "value": "wiring is not a file"},
                ]
            }
        )
    )
    result = add(ws, "--from-file", str(seed))
    assert result.exit_code == 0
    assert result.stdout.count("new") >= 2
    assert "2 new" in result.stdout


def test_a_malformed_file_fails_without_a_partial_write(ws: Path, tmp_path: Path) -> None:
    """Parsed in full before writing: a half-seeded estate with no way to tell which half is
    worse than a refusal."""
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {"memories": [{"subject": "a", "attribute": "b", "value": "ok"}, {"subject": "c"}]}
        )
    )
    result = add(ws, "--from-file", str(bad))
    assert result.exit_code != 0

    store = GovernedMemoryStore.for_workspace(ws)
    assert store.get("a", "b") is None


def test_from_file_and_positionals_are_mutually_exclusive(ws: Path, tmp_path: Path) -> None:
    seed = tmp_path / "s.json"
    seed.write_text(json.dumps({"memories": []}))
    assert add(ws, "x", "y", "z", "--from-file", str(seed)).exit_code != 0


def test_missing_arguments_are_refused(ws: Path) -> None:
    assert add(ws, "only-subject").exit_code != 0


# ---- provenance ---------------------------------------------------------------------------------


def test_source_defaults_to_the_os_user_and_is_overridable(ws: Path) -> None:
    """ "Who asserted this" is the question asked later, so it is never left null."""
    add(ws, "s", "a", "v")
    store = GovernedMemoryStore.for_workspace(ws)
    assert store.get("s", "a").source == getpass.getuser()  # type: ignore[union-attr]

    add(ws, "s2", "a2", "v2", "--source", "wms-driver")
    assert store.get("s2", "a2").source == "wms-driver"  # type: ignore[union-attr]


def test_type_and_confidence_reach_the_row(ws: Path) -> None:
    add(ws, "s", "a", "v", "--type", "procedural", "--confidence", "0.6")
    row = GovernedMemoryStore.for_workspace(ws).get("s", "a")
    assert row is not None
    assert row.type == "procedural"
    assert row.confidence == pytest.approx(0.6)


# ---- the audit line ------------------------------------------------------------------------------


def test_every_write_is_audited(tmp_path: Path) -> None:
    """`memory.written`, emitted by the STORE so the agent hook is covered by the same line —
    governed memory feeds agent prompts, so a fact entering it changes what later runs believe."""
    governance = MockGovernanceProvider()
    store = GovernedMemoryStore.for_workspace(tmp_path, governance=governance)
    store.write(MemoryCandidate(subject="s", attribute="a", value="v", source="srijith"))

    written = [e for e in governance.events if e.event_type == "memory.written"]
    assert len(written) == 1
    assert written[0].payload["op"] == "new"
    assert written[0].payload["key"] == "s/a"
    assert written[0].payload["source"] == "srijith"


def test_the_runtime_builds_its_store_with_governance(ws: Path) -> None:
    """The wiring assertion. A store built without it audits nothing — the shape of every orphan
    this codebase has turned up."""
    runtime = WorkspaceRuntime.from_workspace_path(ws)
    assert runtime.governed_memory is not None
    assert runtime.governed_memory._governance is not None


# ---- POST /memory ---------------------------------------------------------------------------


@pytest.fixture
def client(ws: Path):  # type: ignore[no-untyped-def]

    with TestClient(create_app(ws)) as c:
        yield c


def test_post_memory_matches_the_cli_for_identical_input(client: Any, ws: Path) -> None:
    """One contract, two surfaces. The run surfaces diverged once; these must not."""
    first = client.post("/memory", json={"subject": "s", "attribute": "a", "value": "v"})
    assert first.status_code == 200
    assert first.json()["op"] == "new"
    assert first.json()["key"] == "s/a"

    # The same input again, through the CLI — the same store, so the op continues the sequence.
    again = add(ws, "s", "a", "v")
    assert "reinforce" in again.stdout


def test_post_memory_defaults_source_to_the_caller(client: Any, ws: Path) -> None:
    client.post("/memory", json={"subject": "who", "attribute": "asked", "value": "v"})
    row = GovernedMemoryStore.for_workspace(ws).get("who", "asked")
    assert row is not None
    assert row.source  # never null


def test_post_memory_carries_type_and_confidence(client: Any, ws: Path) -> None:
    client.post(
        "/memory",
        json={
            "subject": "s2",
            "attribute": "a2",
            "value": "v",
            "type": "procedural",
            "confidence": 0.5,
        },
    )
    row = GovernedMemoryStore.for_workspace(ws).get("s2", "a2")
    assert row is not None
    assert row.type == "procedural"
    assert row.confidence == pytest.approx(0.5)


def test_a_workspace_without_governed_memory_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    (tmp_path / "workspace.yaml").write_text(WORKSPACE)
    with TestClient(create_app(tmp_path)) as c:
        resp = c.post("/memory", json={"subject": "a", "attribute": "b", "value": "c"})
    assert resp.status_code == 404
