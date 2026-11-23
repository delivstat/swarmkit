"""`evals.capture.redact`: a workspace's redaction applies to every capture (eval-capture.md §4).

Inputs and outputs are stored as given, and the audit redaction policy does not cover them, so a
capture copies them verbatim into a file headed for git. A per-request `--redact` relies on every
caller remembering it. The workspace default is applied on every capture, from every door, with a
request's own patterns added on top. A pattern that does not compile fails at load, naming it.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from swarmkit_runtime.cli import app as cli
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.persistence import storage_for_workspace
from swarmkit_runtime.resolver import capture_redact_patterns, resolve_workspace
from swarmkit_runtime.server import create_app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


def _ws(tmp_path: Path, patterns: list[str]) -> Path:
    root = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, root, ignore=shutil.ignore_patterns(".swarmkit", "__pycache__"))
    shutil.rmtree(root / "evals", ignore_errors=True)
    (root / "evals").mkdir()
    doc = yaml.safe_load((root / "workspace.yaml").read_text())
    doc["evals"] = {"capture": {"redact": patterns}}
    (root / "workspace.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


def _seed(ws: Path, text: str, output: str) -> str:
    store = storage_for_workspace(ws).store()
    rid = str(uuid.uuid4())
    store.create_job(rid, "hello", text, None, "serve")
    store.update_job(rid, status="completed", output=output)
    return rid


def test_the_patterns_are_read_from_the_workspace(tmp_path: Path) -> None:
    ws = _ws(tmp_path, [r"DC-\d+"])
    assert capture_redact_patterns(resolve_workspace(ws).raw.model_dump(mode="json")) == [r"DC-\d+"]


def test_a_pattern_that_does_not_compile_fails_the_load_naming_it(tmp_path: Path) -> None:
    ws = _ws(tmp_path, [r"DC-\d+", "acct-(\\d+"])
    with pytest.raises(ResolutionErrors) as exc:
        resolve_workspace(ws)
    (err,) = [e for e in exc.value.errors if e.code == "evals.redact-invalid"]
    assert "acct-(\\d+" in err.message
    assert err.yaml_pointer == "/evals/capture/redact/1"


def test_the_cli_applies_the_workspace_patterns_without_being_asked(tmp_path: Path) -> None:
    ws = _ws(tmp_path, [r"DC-\d+"])
    rid = _seed(ws, "returns for DC-14", "Three returns at DC-14.")
    out = CliRunner().invoke(
        cli, ["eval-capture", str(ws), "--run", rid, "--into", "returns", "--write"]
    )
    assert out.exit_code == 0, out.stdout
    text = (ws / "evals" / "returns.yaml").read_text()
    assert "DC-14" not in text and "[REDACTED]" in text


def test_a_request_adds_patterns_and_cannot_remove_the_workspace_ones(tmp_path: Path) -> None:
    ws = _ws(tmp_path, [r"DC-\d+"])
    _seed(ws, "returns for DC-14, order 8812", "Order 8812 at DC-14 is open.")
    with TestClient(create_app(ws)) as client:
        body = client.post(
            "/evals/capture",
            json={"topology": "hello", "into": "returns", "redact": [r"\b\d{4}\b"]},
        ).json()
    assert "DC-14" not in body["yaml"]  # the workspace pattern still applied
    assert "8812" not in body["yaml"]  # and the request's was added
