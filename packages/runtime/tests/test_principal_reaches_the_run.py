"""The authenticated caller reaches the code that resolves credentials.

A per-user connection is only as good as the plumbing that says who is calling. The principal is
set once, in the auth middleware, and everything downstream inherits it — including a job, because
`asyncio.create_task` copies the current context, so a run started while handling a request keeps
its caller for the whole run, long after the response has gone.

The reset matters as much as the set: a task that served Alice must not carry her identity into the
next request it serves, which is the sort of bug that only appears under load and looks like a
permissions mystery.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from swarmkit_runtime._principal import current_principal
from swarmkit_runtime.auth import NoneAuthProvider
from swarmkit_runtime.server._app import create_app


def _workspace(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "principal-demo", "name": "Principal Demo"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "topologies").mkdir(exist_ok=True)
    return tmp_path


def _app_seeing_principal(tmp_path: Path, who: str) -> tuple[Any, dict[str, Any]]:
    app = create_app(_workspace(tmp_path), auth_provider=NoneAuthProvider(identity=who))
    seen: dict[str, Any] = {}

    @app.get("/_probe_principal")
    async def _probe() -> dict[str, str]:
        # What a credential resolution during this request would see.
        seen["in_request"] = current_principal()

        # And what a job started here would inherit, since create_task copies the context.
        async def _as_a_job() -> None:
            await asyncio.sleep(0)
            seen["in_task"] = current_principal()

        await asyncio.create_task(_as_a_job())
        return {"ok": "yes"}

    return app, seen


def test_the_caller_reaches_the_request_and_any_job_it_starts(tmp_path: Path) -> None:
    app, seen = _app_seeing_principal(tmp_path, "alice")

    with TestClient(app) as client:
        assert client.get("/_probe_principal").status_code == 200

    assert seen["in_request"] == "alice"
    assert seen["in_task"] == "alice", "a job must inherit the caller that started it"


def test_the_principal_does_not_outlive_the_request(tmp_path: Path) -> None:
    """Set in `try`, cleared in `finally` — so a reused task cannot serve Bob as Alice."""
    app, _ = _app_seeing_principal(tmp_path, "alice")

    with TestClient(app) as client:
        client.get("/_probe_principal")

    assert current_principal() is None


def test_two_identities_are_not_confused(tmp_path: Path) -> None:
    alice_app, alice_seen = _app_seeing_principal(tmp_path / "a", "alice")
    bob_app, bob_seen = _app_seeing_principal(tmp_path / "b", "bob")

    with TestClient(alice_app) as client:
        client.get("/_probe_principal")
    with TestClient(bob_app) as client:
        client.get("/_probe_principal")

    assert alice_seen["in_request"] == "alice"
    assert bob_seen["in_request"] == "bob"
