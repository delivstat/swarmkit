"""``swarmkit fleet enroll-token`` / ``memberships`` — the owner's enrollment UX (design 19).

The CLI mints a token directly against the workspace's membership store; a serve loaded on the same
workspace then accepts it (single-use), and ``memberships`` lists the fleet that registered.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.cli import app
from typer.testing import CliRunner

runner = CliRunner()

EXAMPLE_WS = Path(__file__).resolve().parents[3] / "examples" / "hello-swarm" / "workspace"


def _workspace(tmp_path: Path) -> Path:
    """A real, writable copy of a workspace so the CLI and a serve share one .swarmkit/fleet.sqlite.
    Any runtime state (a stray .swarmkit from local runs) is stripped so each test starts clean."""
    ws = tmp_path / "workspace"
    shutil.copytree(EXAMPLE_WS, ws)
    shutil.rmtree(ws / ".swarmkit", ignore_errors=True)
    return ws


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


def _token(output: str) -> str:
    """The minted token — the first non-comment, non-empty line of the command output."""
    return next(line for line in output.splitlines() if line and not line.startswith("#"))


def test_enroll_token_mints_a_usable_single_use_token(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    result = runner.invoke(app, ["fleet", "enroll-token", str(ws), "--scope", "manage"])
    assert result.exit_code == 0
    assert "single-use" in result.output and "manage" in result.output
    token = _token(result.output)
    assert token

    # a serve loaded on the SAME workspace shares .swarmkit/fleet.sqlite → the token registers once.
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(ws)) as client:
        first = client.post(
            "/fleet/register",
            json={"fleet_id": "acme"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200
        # single-use: the token can't be replayed.
        replay = client.post(
            "/fleet/register",
            json={"fleet_id": "acme"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert replay.status_code == 401


def test_enroll_token_rejects_bad_scope(tmp_path: Path) -> None:
    result = runner.invoke(app, ["fleet", "enroll-token", str(tmp_path), "--scope", "root"])
    assert result.exit_code == 2
    assert "invalid scope" in result.output


def test_memberships_lists_registered_fleets(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    # empty first.
    empty = runner.invoke(app, ["fleet", "memberships", str(ws)])
    assert "No fleets" in empty.output

    token = _token(
        runner.invoke(app, ["fleet", "enroll-token", str(ws), "--scope", "monitor"]).output
    )
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(ws)) as client:
        client.post(
            "/fleet/register",
            json={"fleet_id": "acme-prod"},
            headers={"Authorization": f"Bearer {token}"},
        )

    listed = runner.invoke(app, ["fleet", "memberships", str(ws)])
    assert listed.exit_code == 0
    assert "acme-prod" in listed.output and "monitor" in listed.output
    assert "unpinned" in listed.output  # no fleet identity presented at register
