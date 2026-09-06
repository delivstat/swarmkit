"""The push half of the event seam, and the promises it deliberately does not make.

`extracting-the-channels.md` chose best-effort push over a durable pull rather than at-least-once,
because at-least-once means a queue, a retry schedule and a dead-letter path inside the runtime —
the thing that decision exists to avoid. These tests hold that contract to its word: a sink never
fails a run, a 4xx is not retried, and a dropped event stays readable.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.audit._store import SqlAuditProvider
from swarmkit_runtime.events import StdoutSink, WebhookSink, build_sink, fan_out
from swarmkit_runtime.events._sink import BACKOFF_S
from swarmkit_runtime.governance import AuditEvent

posted: list[dict[str, Any]] = []
attempts = {"n": 0}


def _transport(status: int = 204) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        posted.append(
            {"body": request.content.decode(), "auth": request.headers.get("authorization", "")}
        )
        return httpx.Response(status)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    posted.clear()
    attempts["n"] = 0
    # No real waiting; the retry COUNT is what the contract promises, not the delay.
    monkeypatch.setattr("swarmkit_runtime.events._sink.BACKOFF_S", (0.0, 0.0))


def _patch(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real = httpx.AsyncClient

    def factory(*a: Any, **kw: Any) -> httpx.AsyncClient:
        kw["transport"] = transport
        return real(*a, **kw)

    monkeypatch.setattr("swarmkit_runtime.events._sink.httpx.AsyncClient", factory)


# ---- the sink --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_webhook_receives_the_event_and_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _transport())
    assert await WebhookSink("https://app/events", token="t0k").deliver({"event_type": "run.ended"})
    assert posted[0]["auth"] == "Bearer t0k"
    assert "run.ended" in posted[0]["body"]


@pytest.mark.asyncio
async def test_a_5xx_is_retried_a_bounded_number_of_times(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounded, because a long retry schedule is a queue with extra steps."""
    _patch(monkeypatch, _transport(503))
    assert await WebhookSink("https://app/events").deliver({"event_type": "x"}) is False
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_a_4xx_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """The application rejected the body. Sending the same body again changes nothing."""
    _patch(monkeypatch, _transport(400))
    assert await WebhookSink("https://app/events").deliver({"event_type": "x"}) is False
    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_a_sink_that_raises_never_escapes() -> None:
    """The application's outage is not the swarm's problem."""

    class Exploding:
        async def deliver(self, _event: dict[str, Any]) -> bool:
            msg = "boom"
            raise RuntimeError(msg)

    await fan_out([Exploding(), StdoutSink()], {"event_type": "run.ended"})


def test_a_webhook_without_a_url_is_refused_at_build() -> None:
    with pytest.raises(ValueError, match="needs a `url`"):
        build_sink({"sink": "webhook"})


def test_an_unknown_sink_lists_the_known_ones() -> None:
    with pytest.raises(ValueError, match="Available: webhook, stdout"):
        build_sink({"sink": "carrier-pigeon"})


def test_backoff_is_short_and_finite() -> None:
    assert len(BACKOFF_S) == 2


# ---- emission from the audit chokepoint --------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SqlAuditProvider:
    return SqlAuditProvider(create_engine(f"sqlite:///{tmp_path / 'a.db'}"))


def _event(event_type: str = "funnel.gate_opened") -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        agent_id="root",
        timestamp=datetime.now(tz=UTC),
        run_id="run-1",
        event_id=uuid.uuid4(),
        payload={"gate_id": "run-1:root"},
    )


@pytest.mark.asyncio
async def test_recording_an_event_pushes_it(
    store: SqlAuditProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, _transport())
    store._sinks = [(WebhookSink("https://app/events"), [])]
    await store.record(_event())
    assert len(posted) == 1
    assert "gate_id" in posted[0]["body"]


@pytest.mark.asyncio
async def test_the_type_filter_is_applied(
    store: SqlAuditProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, _transport())
    store._sinks = [(WebhookSink("https://app/events"), ["funnel.gate_opened"])]
    await store.record(_event("agent.started"))
    await store.record(_event("funnel.gate_opened"))
    assert len(posted) == 1


@pytest.mark.asyncio
async def test_a_failing_sink_does_not_prevent_the_event_being_stored(
    store: SqlAuditProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property that makes best-effort defensible: a dropped push is still recoverable."""
    _patch(monkeypatch, _transport(503))
    store._sinks = [(WebhookSink("https://app/events"), [])]
    event = _event()
    await store.record(event)
    stored = await store.since_cursor(limit=10)
    assert [str(e.event_id) for e in stored] == [str(event.event_id)]


@pytest.mark.asyncio
async def test_a_duplicate_is_not_pushed_twice(
    store: SqlAuditProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate insert means somebody already heard about this one."""
    _patch(monkeypatch, _transport())
    store._sinks = [(WebhookSink("https://app/events"), [])]
    event = _event()
    await store.record(event)
    await store.record(event)
    assert len(posted) == 1


@pytest.mark.asyncio
async def test_the_pushed_payload_carries_a_cursor(
    store: SqlAuditProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So a consumer can checkpoint from the push itself, not only from a poll."""
    _patch(monkeypatch, _transport())
    store._sinks = [(WebhookSink("https://app/events"), [])]
    await store.record(_event())
    assert json.loads(posted[0]["body"])["cursor"]


@pytest.mark.asyncio
async def test_no_sinks_is_the_common_case(store: SqlAuditProvider) -> None:
    await store.record(_event())
    assert len(await store.since_cursor(limit=10)) == 1


# ---- the workspace wiring ----------------------------------------------------------------------


def test_a_workspace_with_event_sinks_attaches_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The assertion that would have caught the sink being wired to nothing.

    Also covers the bug found by running it: the credential service was reached through the MCP
    manager, so a workspace with no MCP servers silently sent no bearer token — which is most
    workspaces that would use events at all.
    """
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("APP_TOKEN", "secret-abc")
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: t, name: T}\n"
        "governance: {provider: mock, policy_language: yaml}\n"
        "credentials:\n  app-token: {source: env, config: {env: APP_TOKEN}}\n"
        "events:\n"
        "  - sink: webhook\n"
        "    url: http://127.0.0.1:1/events\n"
        "    credentials_ref: app-token\n"
        "    types: [funnel.gate_opened]\n"
    )
    runtime = WorkspaceRuntime.from_workspace_path(tmp_path)
    provider: Any = runtime._audit_provider
    sinks = provider._sinks
    assert len(sinks) == 1
    sink, types = sinks[0]
    assert isinstance(sink, WebhookSink)
    assert types == ["funnel.gate_opened"]
    # No MCP servers in this workspace — the token must resolve anyway.
    assert sink.token == "secret-abc"


def test_a_workspace_without_sinks_attaches_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: t, name: T}\n"
        "governance: {provider: mock, policy_language: yaml}\n"
    )
    runtime = WorkspaceRuntime.from_workspace_path(tmp_path)
    provider: Any = runtime._audit_provider
    assert provider._sinks == []
