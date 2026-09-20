"""Per-user sessions are a bounded cache, and closing one actually frees it.

Keying a session by owner turns the population from *servers* into *users x servers*. Left
unbounded that does not degrade, it exhausts — an `http` session per user, and for `stdio` a
subprocess per user, until the host runs out of memory or file descriptors on the day adoption
succeeds.

The subtler half is that eviction has to *work*. Every session used to enter one shared
`AsyncExitStack` closed only at shutdown, so dropping a session forgot it while its transport
stayed open: reopening added a connection instead of replacing one. Invisible at one session per
server, fatal at one per user.

Per-session *stacks* were not the answer either — anyio cancel scopes unwind per task in LIFO
order, and closing the least-recently-used session is out of order by definition, which cancels
unrelated sessions. Each session therefore owns a **task** that exits its own contexts.
`test_closing_a_session_releases_its_transport` asserts that task actually finishes.

Closing is safe because it is recoverable: every consumer calls `get_session` per tool call, so a
closed session is reopened on next use, and a call already in flight holds its own session object.
See design/details/per-caller-credential-delegation.md.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest
from swarmkit_runtime.mcp._client import MCPClientManager

_SEP = "\x00"


def _manager(transport: str = "http") -> MCPClientManager:
    manager = MCPClientManager()
    configs: dict[str, Any] = {
        "calendar": SimpleNamespace(
            server_id="calendar", credentials_ref="cal", transport=transport
        ),
        "github": SimpleNamespace(server_id="github", credentials_ref="gh", transport="http"),
    }
    manager._configs = configs

    return manager


def _add(manager: MCPClientManager, key: str, *, last_used: float) -> asyncio.Task[None]:
    """A parked session task, shaped like the one `_spawn_session` leaves behind."""
    closing = asyncio.Event()

    async def _hold() -> None:
        await closing.wait()

    task = asyncio.get_event_loop().create_task(_hold())
    manager._sessions[key] = object()  # type: ignore[assignment]
    manager._session_closers[key] = (task, closing)
    manager._session_last_used[key] = last_used
    return task


@pytest.mark.asyncio
async def test_closing_a_session_releases_its_transport() -> None:
    """The whole point of per-session stacks: dropping a session must not leak its transport."""
    manager = _manager()
    task = _add(manager, f"calendar{_SEP}alice", last_used=0.0)

    await manager._close_key(f"calendar{_SEP}alice")

    assert task.done(), "eviction must end the session's task, not merely forget the session"
    assert f"calendar{_SEP}alice" not in manager._sessions
    assert f"calendar{_SEP}alice" not in manager._session_closers


@pytest.mark.asyncio
async def test_the_ceiling_closes_the_least_recently_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PER_USER_SESSION_MAX", "2")
    manager = _manager()
    oldest_task = _add(manager, f"calendar{_SEP}alice", last_used=1.0)
    newer_task = _add(manager, f"calendar{_SEP}bob", last_used=50.0)

    # Opening a third per-user session has to make room for one.
    await manager._enforce_ceiling(manager._configs["calendar"])

    assert oldest_task.done() is True
    assert newer_task.done() is False


@pytest.mark.asyncio
async def test_stdio_has_its_own_much_smaller_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-user stdio server means one subprocess per user — what actually exhausts a host."""
    monkeypatch.setenv("SWARMKIT_PER_USER_SESSION_MAX", "64")
    monkeypatch.setenv("SWARMKIT_PER_USER_STDIO_SESSION_MAX", "1")
    manager = _manager(transport="stdio")
    only_task = _add(manager, f"calendar{_SEP}alice", last_used=1.0)

    await manager._enforce_ceiling(manager._configs["calendar"])

    assert only_task.done(), "the generous http ceiling must not govern stdio"


@pytest.mark.asyncio
async def test_idle_sessions_are_swept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PER_USER_SESSION_IDLE_S", "60")
    manager = _manager()
    now = time.monotonic()
    stale_task = _add(manager, f"calendar{_SEP}alice", last_used=now - 600)
    fresh_task = _add(manager, f"calendar{_SEP}bob", last_used=now)

    await manager._evict_idle()

    assert stale_task.done() is True
    assert fresh_task.done() is False


@pytest.mark.asyncio
async def test_a_global_session_is_never_evicted_by_either_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`global` is one warm session per server and is meant to stay warm — today's behaviour."""
    monkeypatch.setenv("SWARMKIT_PER_USER_SESSION_IDLE_S", "1")
    monkeypatch.setenv("SWARMKIT_PER_USER_SESSION_MAX", "1")
    manager = _manager()
    shared_task = _add(manager, "github", last_used=0.0)  # no separator: a global session

    await manager._evict_idle()
    await manager._enforce_ceiling(manager._configs["github"])

    assert shared_task.done() is False


@pytest.mark.asyncio
async def test_a_ceiling_of_zero_disables_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator who means 'unbounded' should be able to say so explicitly."""
    monkeypatch.setenv("SWARMKIT_PER_USER_SESSION_MAX", "0")
    manager = _manager()
    task = _add(manager, f"calendar{_SEP}alice", last_used=0.0)

    await manager._enforce_ceiling(manager._configs["calendar"])

    assert task.done() is False
