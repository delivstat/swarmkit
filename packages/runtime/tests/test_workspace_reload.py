"""Re-reading the workspace from disk, and saying honestly what happened.

`swarmkit serve` resolves the workspace once at startup and holds it. Edits made through the UI
already trigger a reload, but a topology, skill or archetype changed on disk — by an editor, a git
pull, an authoring swarm writing files — was invisible until the server was restarted, with nothing
to say the running config and the files had diverged.

The property that matters is what happens when the reload FAILS. `reload()` returns None and
`_install` no-ops, so the PREVIOUS runtime keeps serving. That makes `valid: false` mean "the change
on disk is not live" — not "a broken config is now running". They are opposite situations, and the
wrong reading sends an operator looking for the wrong problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import copy_workspace
from swarmkit_runtime.server._routes_crud import _install
from swarmkit_runtime.server._services import ArtifactService

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    copy_workspace(EXAMPLE_WS, ws)
    return ws


class _State:
    """Stands in for `app.state`, which is all `_install` touches."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime


class _Request:
    def __init__(self, state: _State) -> None:
        self.app = type("_App", (), {"state": state})()


# ---- a failed reload does not swap in a broken runtime -------------------------------------------


def test_a_failed_reload_keeps_the_previous_runtime() -> None:
    """The property the UI's wording depends on. If a bad reload replaced the live runtime, the
    server would start serving a config it had just rejected."""
    state = _State("the runtime that was already serving")

    _install(_Request(state), None)  # type: ignore[arg-type]

    assert state.runtime == "the runtime that was already serving"


def test_a_successful_reload_swaps_it_in() -> None:
    state = _State("old")

    _install(_Request(state), "rebuilt")  # type: ignore[arg-type]

    assert state.runtime == "rebuilt"


# ---- a reload picks up what changed on disk ------------------------------------------------------


def test_reload_sees_an_artifact_added_on_disk(workspace: Path) -> None:
    """The reported gap: a file written outside the UI was invisible until a restart."""
    service = ArtifactService(workspace)
    before = service.validate_workspace()["topologies"]

    (workspace / "topologies" / "added-outside.yaml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: Topology\n"
        "metadata:\n"
        "  name: added-outside\n"
        "  version: 0.1.0\n"
        "agents:\n"
        "  root:\n"
        "    id: root\n"
        "    role: root\n"
        "    prompt:\n"
        "      system: Say hello.\n"
    )

    after = service.validate_workspace()["topologies"]

    assert "added-outside" not in before
    assert "added-outside" in after
    assert service.reload() is not None, "a valid workspace must rebuild"


def test_reload_returns_none_when_the_workspace_is_invalid(workspace: Path) -> None:
    """And it is reported rather than raised: the endpoint still answers, with the validation that
    tells an operator what to fix."""
    (workspace / "topologies" / "broken.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Topology\n"
    )

    service = ArtifactService(workspace)

    assert service.reload() is None
    assert service.validate_workspace()["valid"] is False


def test_a_valid_workspace_reloads(workspace: Path) -> None:
    assert ArtifactService(workspace).reload() is not None


# ---- the endpoint answers a POST, and only a POST ------------------------------------------------


def test_reload_is_a_post_and_a_browser_get_is_not_it(tmp_path: Path) -> None:
    """Typing `/api/reload` into a browser issues a GET and gets a 404, which reads as "the endpoint
    does not exist" — it was reported as exactly that. It exists; it mutates, so it is a POST, and
    the UI's button is what issues one.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from swarmkit_runtime.server._app import create_app  # noqa: PLC0415

    ws = tmp_path / "workspace"
    copy_workspace(EXAMPLE_WS, ws)

    with TestClient(create_app(ws)) as client:
        assert client.post("/api/reload").status_code == 200
        wrong_method = client.get("/api/reload")

    # Not a specific code: with the portal installed the SPA mount catches it, without it Starlette
    # answers 405. Asserting either made this test pass locally and fail in CI. What must hold is
    # that a GET does not reload, and that the answer EXPLAINS itself rather than reading as "no
    # such endpoint" — which is how the 404 was reported.
    assert wrong_method.status_code >= 400
    assert "POST-only" in wrong_method.text


def test_reload_returns_the_validation_so_the_caller_can_report_it(tmp_path: Path) -> None:
    """The button says what happened rather than "done" — including that an invalid workspace left
    the previous config serving."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from swarmkit_runtime.server._app import create_app  # noqa: PLC0415

    ws = tmp_path / "workspace"
    copy_workspace(EXAMPLE_WS, ws)

    with TestClient(create_app(ws)) as client:
        body = client.post("/api/reload").json()

    assert body["valid"] is True
    assert "topologies" in body
