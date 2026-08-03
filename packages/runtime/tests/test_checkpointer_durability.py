"""The checkpointer must be a saver, and it must be durable.

Three defects lived in one method (`StorageService.checkpointer`), all of them silent:

1. **postgres crashed.** `PostgresSaver.from_conn_string(url)` is a ``@contextmanager``; it yields
   the saver. Calling ``.setup()`` on the context manager raised
   ``AttributeError: '_GeneratorContextManager' object has no attribute 'setup'`` — so postgres
   checkpointing had never worked.
2. **sqlite returned the wrong object.** Same contextmanager, returned directly, so callers got a
   ``_GeneratorContextManager`` with no ``get_tuple``/``put`` instead of a saver — and the
   connection was closed on exit even if they had one.
3. **the fallback lied.** With `langgraph-checkpoint-sqlite` absent the runtime silently used an
   in-memory saver while ``swarmkit storage status`` still reported "checkpoints sqlite
   workspace-local". A run advertised as resumable could not be resumed, and nothing said so.

The durability tests write with one service instance and read with a **fresh** one, because the bug
class here is precisely "works until the process ends".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from swarmkit_runtime.persistence._service import StorageService, StoreKind

CFG: dict[str, Any] = {"configurable": {"thread_id": "run-1", "checkpoint_ns": ""}}
META: dict[str, Any] = {"source": "input", "step": 0, "parents": {}}


def _service(root: Path, raw: Any = None) -> StorageService:
    return StorageService.for_workspace(root, raw)


# ---- it is a saver, not a context manager -------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpointer_is_a_saver(tmp_path: Path) -> None:
    """The regression in one assertion: a `_GeneratorContextManager` has neither method."""
    svc = _service(tmp_path)
    try:
        saver = await svc.checkpointer()
        assert type(saver).__name__ != "_GeneratorContextManager"
        assert hasattr(saver, "aget_tuple") and hasattr(saver, "aput")
    finally:
        await svc.aclose()


@pytest.mark.asyncio
async def test_sqlite_checkpoints_survive_the_process(tmp_path: Path) -> None:
    """Write with one service, read with a fresh one. The old code closed the connection on the way
    out of `from_conn_string`, so anything written was unreachable."""
    writer = _service(tmp_path)
    try:
        await (await writer.checkpointer()).aput(CFG, empty_checkpoint(), META, {})
    finally:
        await writer.aclose()

    reader = _service(tmp_path)
    try:
        assert await (await reader.checkpointer()).aget_tuple(CFG) is not None
    finally:
        await reader.aclose()


@pytest.mark.asyncio
async def test_the_connection_outlives_the_call_that_made_it(tmp_path: Path) -> None:
    """`from_conn_string` closes the connection on context exit. Entering it on a stack held by the
    service is what keeps the saver usable after `checkpointer()` returns."""
    svc = _service(tmp_path)
    try:
        saver = await svc.checkpointer()
        await saver.aput(CFG, empty_checkpoint(), META, {})
        # Same saver object, used well after the call that built it returned.
        assert await saver.aget_tuple(CFG) is not None
    finally:
        await svc.aclose()


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    await svc.checkpointer()
    await svc.aclose()
    await svc.aclose()


# ---- the fallback is loud, and the report tells the truth ----------------------------------------


@pytest.mark.asyncio
async def test_a_missing_sqlite_saver_warns_and_is_reported_as_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The status line claiming durable storage over an in-memory saver is the failure mode; it is
    worse than the fallback itself, because it removes the operator's chance to notice."""
    import builtins  # noqa: PLC0415

    real_import = builtins.__import__

    def _no_sqlite_saver(name: str, *a: Any, **k: Any) -> Any:
        if name == "langgraph.checkpoint.sqlite.aio":
            raise ImportError("simulated: package not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_sqlite_saver)

    svc = _service(tmp_path)
    with caplog.at_level(logging.WARNING):
        saver = await svc.checkpointer()

    assert type(saver).__name__ in ("MemorySaver", "InMemorySaver")
    assert "IN MEMORY" in caplog.text
    assert "lost when this process exits" in caplog.text

    checkpoint_line = next(line for line in svc.report() if "checkpoints" in line)
    assert "memory" in checkpoint_line
    assert "NOT durable" in checkpoint_line
    assert "sqlite    workspace-local" not in checkpoint_line, "must not claim durable sqlite"


@pytest.mark.asyncio
async def test_the_report_says_sqlite_when_it_really_is_sqlite(tmp_path: Path) -> None:
    """The honest-reporting change must not swing the other way and cry wolf."""
    svc = _service(tmp_path)
    try:
        await svc.checkpointer()
        line = next(x for x in svc.report() if "checkpoints" in x)
        assert "sqlite" in line and "NOT durable" not in line
    finally:
        await svc.aclose()


# ---- the documented default is real --------------------------------------------------------------


def test_the_sqlite_saver_ships_by_default() -> None:
    """`storage.checkpoints` defaults to workspace-local sqlite, so the saver implementing that
    default is a base dependency — not an extra a user has to discover after their checkpoints
    turn out not to exist."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: PLC0415

    assert AsyncSqliteSaver is not None


def test_checkpoints_default_to_workspace_local_sqlite(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    target = svc.target(StoreKind.CHECKPOINTS)
    assert target.backend == "sqlite"
