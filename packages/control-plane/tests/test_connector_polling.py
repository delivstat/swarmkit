"""The panel's run drivers (authoring turn, eval) poll /jobs/{id} to a terminal state.

A run that parks on a human gate is `deferred` — the gate is resolved on the instance and the run
resumes there, so the driver returns it rather than polling 180 s into "did not complete in time".
`stopped` and `interrupted` will never complete either; they are reported as what they are.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from swarmkit_control_plane import _connector
from swarmkit_control_plane._connector import ConnectorError, run_authoring, run_eval
from swarmkit_control_plane._serve_client import ServeClient


def _serve(status: str, error: str = "") -> Any:
    """A serve that starts any run and reports the job in *status* on the first poll."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/run/"):
            return httpx.Response(200, json={"job_id": "j1", "status": "pending"})
        if request.url.path == "/jobs/j1":
            return httpx.Response(
                200, json={"job_id": "j1", "status": status, "output": "", "error": error}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def factory(endpoint: str, token_ref: str, **kw: Any) -> ServeClient:
        kw.pop("transport", None)
        return ServeClient(endpoint, token_ref, transport=transport, **kw)

    return factory


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


@pytest.mark.asyncio
async def test_authoring_returns_a_deferred_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _connector, "ServeClient", _serve("deferred", "awaiting review: gate 'j1:drafter'")
    )
    out = await run_authoring("http://serve:8000", "", "skill-authoring", "a skill that…")
    assert out["status"] == "deferred"
    assert out["job_id"] == "j1"
    assert "awaiting review" in out["reply"]


@pytest.mark.parametrize("status", ["stopped", "interrupted", "failed"])
@pytest.mark.asyncio
async def test_authoring_reports_terminal_failures(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr(_connector, "ServeClient", _serve(status, "boom"))
    with pytest.raises(ConnectorError, match=f"{status}: boom"):
        await run_authoring("http://serve:8000", "", "skill-authoring", "…")


@pytest.mark.parametrize("status", ["deferred", "stopped", "interrupted"])
@pytest.mark.asyncio
async def test_eval_reports_the_status_it_saw(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    monkeypatch.setattr(_connector, "ServeClient", _serve(status, "why"))
    out = await run_eval("http://serve:8000", "", "eval-topology", json.dumps({"x": 1}))
    assert out == {"status": status, "error": "why"}


@pytest.mark.asyncio
async def test_a_403_on_resolve_is_the_instances_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Not a member of the role" is a 403 from the instance. It is a refusal to relay to the
    operator with its reason — not a token failure, and not an unreachable instance."""
    from swarmkit_control_plane._connector import GateRefused, resolve_gate  # noqa: PLC0415

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "alice is not a member of role product-lead"})

    transport = httpx.MockTransport(handler)

    def factory(endpoint: str, token_ref: str, **kw: Any) -> ServeClient:
        return ServeClient(endpoint, token_ref, transport=transport)

    monkeypatch.setattr(_connector, "ServeClient", factory)
    with pytest.raises(GateRefused) as exc:
        await resolve_gate("http://serve:8000", "", "mpa-1", "resolve", outcome="approve")
    assert exc.value.status_code == 403
    assert "not a member of role product-lead" in str(exc.value)
