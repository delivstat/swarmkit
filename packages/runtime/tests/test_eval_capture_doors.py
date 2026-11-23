"""Eval capture through its front doors: `swarmkit eval-capture` and serve's capture routes.

Both doors call one `CaptureService` over the same store, so the same runs give the same file.
The preview writes nothing. The write goes through `ArtifactService`, the validated eval-set
write, and the two routes sit at the tiers the design asks for: a preview is a `read`, a write is
`admin`.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from swarmkit_runtime.cli import app as cli
from swarmkit_runtime.eval._capture import case_id_for
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.persistence import storage_for_workspace
from swarmkit_runtime.server import create_app
from swarmkit_runtime.server._helpers import _required_action
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, root, ignore=shutil.ignore_patterns(".swarmkit", "__pycache__"))
    shutil.rmtree(root / "evals", ignore_errors=True)
    (root / "evals").mkdir()
    return root


def _seed(ws: Path, inputs: list[str]) -> list[str]:
    """Completed `hello` runs in the workspace's store, each with one allowed skill call."""
    storage = storage_for_workspace(ws)
    store, audit = storage.store(), storage.audit_provider()
    ids = []
    for text in inputs:
        rid = str(uuid.uuid4())
        store.create_job(rid, "hello", text, None, "serve")
        store.update_job(rid, status="completed", output=f"Hello! You said: {text}")
        asyncio.run(
            audit.record(
                AuditEvent(
                    event_type="skill.executed",
                    agent_id="greeter",
                    timestamp=datetime.now(UTC),
                    skill_id="say-hello",
                    run_id=rid,
                    policy_decision="allow",
                )
            )
        )
        ids.append(rid)
    return ids


@pytest.fixture()
def client(ws: Path) -> Iterator[TestClient]:
    with TestClient(create_app(ws)) as c:
        yield c


def _without_timestamps(text: str) -> str:
    return re.sub(r"captured_at: .*", "captured_at: X", text)


# ---- serve ---------------------------------------------------------------------------------------


def test_the_preview_proposes_cases_and_writes_nothing(client: TestClient, ws: Path) -> None:
    ids = _seed(ws, ["greet the team", "greet the board"])
    resp = client.post("/evals/capture", json={"topology": "hello", "into": "greetings"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["added"]) == sorted(case_id_for(i) for i in ids)
    assert body["created"] is True and body["written"] is False
    cases = yaml.safe_load(body["yaml"])["cases"]
    assert {c["expect"]["used_skills"][0] for c in cases} == {"say-hello"}
    assert not (ws / "evals" / "greetings.yaml").exists()


def test_the_write_creates_the_set_and_a_second_capture_adds_nothing(
    client: TestClient, ws: Path
) -> None:
    _seed(ws, ["greet the team"])
    body = {"topology": "hello", "into": "greetings"}
    first = client.post("/api/evals/capture", json=body).json()
    assert first["written"] is True
    assert (ws / "evals" / "greetings.yaml").exists()
    # the set is a real set: serve lists and reads it
    assert client.get("/api/evals/greetings").status_code == 200

    again = client.post("/api/evals/capture", json=body).json()
    assert again["added"] == [] and again["written"] is False
    assert {s["reason"] for s in again["skipped"]} == {"duplicate"}


def test_a_capture_that_cannot_be_proposed_is_422_with_its_code(client: TestClient) -> None:
    resp = client.post("/evals/capture", json={"into": "greetings"})  # no runs, no topology
    assert resp.status_code == 422
    assert resp.json()["code"] == "selection.empty"
    bad = client.post("/evals/capture", json={"topology": "hello", "into": "Bad_Id"})
    assert bad.status_code == 422 and bad.json()["code"] == "set.id"


def test_a_preview_is_read_and_a_write_is_admin() -> None:
    assert _required_action("POST", "/evals/capture") == "read"
    assert _required_action("POST", "/api/evals/capture") == "admin"
    # the neighbours keep their tiers
    assert _required_action("POST", "/evals/greet/run") == "run"
    assert _required_action("POST", "/api/evals") == "admin"


# ---- the CLI, and both doors agreeing ------------------------------------------------------------


def test_the_cli_previews_then_writes(ws: Path) -> None:
    ids = _seed(ws, ["greet the team"])
    runner = CliRunner()
    preview = runner.invoke(cli, ["eval-capture", str(ws), "--run", ids[0], "--into", "greetings"])
    assert preview.exit_code == 0, preview.stdout
    assert "preview only" in preview.stdout
    assert not (ws / "evals" / "greetings.yaml").exists()

    written = runner.invoke(
        cli, ["eval-capture", str(ws), "--run", ids[0], "--into", "greetings", "--write"]
    )
    assert written.exit_code == 0, written.stdout
    assert "wrote evals/greetings.yaml" in written.stdout
    assert (ws / "evals" / "greetings.yaml").exists()


def test_the_cli_and_serve_propose_the_same_file(ws: Path) -> None:
    _seed(ws, ["greet the team", "greet the board"])
    runner = CliRunner()
    out = runner.invoke(
        cli, ["eval-capture", str(ws), "--topology", "hello", "--into", "greetings", "--json"]
    )
    assert out.exit_code == 0, out.stdout
    from_cli = json.loads(out.stdout)["yaml"]
    with TestClient(create_app(ws)) as client:
        from_serve = client.post(
            "/evals/capture", json={"topology": "hello", "into": "greetings"}
        ).json()["yaml"]
    assert _without_timestamps(from_cli) == _without_timestamps(from_serve)


def test_the_cli_refuses_a_bad_label_and_a_bad_pattern(ws: Path) -> None:
    runner = CliRunner()
    label = runner.invoke(
        cli, ["eval-capture", str(ws), "--topology", "hello", "--into", "x", "--label", "nokey"]
    )
    assert label.exit_code != 0
    _seed(ws, ["greet"])
    pattern = runner.invoke(
        cli, ["eval-capture", str(ws), "--topology", "hello", "--into", "x", "--redact", "("]
    )
    assert pattern.exit_code != 0
    assert "redact pattern" in (pattern.stdout + (pattern.stderr or ""))


def test_one_writer_still_owns_the_evals_directory() -> None:
    """Capture adds no second writer under evals/: it writes through ArtifactService."""
    src = (REPO_ROOT / "packages/runtime/src/swarmkit_runtime/eval/_capture.py").read_text()
    assert "write_text" not in src and "open(" not in src
    assert "create_eval_set" in src and "put_eval_set" in src
