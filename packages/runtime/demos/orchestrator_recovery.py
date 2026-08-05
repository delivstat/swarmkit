"""Demo: a transient error no longer halts the pipeline permanently.

The bug: `run_drive_loop()` had no error handling around `handle_event`, so any exception exited the
process AFTER the event was claimed and BEFORE it was acked — and `claim()` only ever looked at
`queued` rows, so no restart could ever pick it up again. The saga sat `active` looking like a slow
stage; recovery took hand-written SQL.

    uv run python packages/runtime/demos/orchestrator_recovery.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, cast

import httpx
from sqlalchemy import text
from swarmkit_runtime.cli._cmd_orchestrator import _DEFAULT_MAX_ATTEMPTS, run_drive_loop
from swarmkit_runtime.orchestration._saga_store import SqlSagaStore
from swarmkit_runtime.persistence._store import make_engine


class _Controller:
    """Fails `failures` times — the VPN-down shape — then succeeds."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def handle_event(self, _cid: str, _event: str) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ConnectError("All connection attempts failed")


def _status(store: SqlSagaStore, event_id: int) -> str:
    with store.engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, attempts FROM pipeline_events WHERE id = :i"), {"i": event_id}
        ).first()
    return f"{row[0]:<8} attempts={row[1]}" if row else "gone"


def _store() -> SqlSagaStore:
    d = Path(tempfile.mkdtemp())
    return SqlSagaStore(make_engine(f"sqlite:///{d / 'saga.sqlite'}"))


async def main() -> None:
    print("\n  A transient failure — the reported case (HTTP_PROXY pointing at a dead port)")
    print("  " + "─" * 74)
    store = _store()
    event_id = store.enqueue("WMS-1", '{"kind": "start"}')
    controller = _Controller(failures=1)

    await run_drive_loop(cast("Any", controller), store, once=True)
    print(f"    after the failure:  {_status(store, event_id)}   <- back in the queue, not lost")
    handled = await run_drive_loop(cast("Any", controller), store, once=True)
    print(f"    after the retry:    {_status(store, event_id)}   handled={handled}")
    print("    BEFORE: the process exited here and the event was unreachable forever.\n")

    print("  A deterministic failure — bounded, then dead-lettered")
    print("  " + "─" * 74)
    store = _store()
    event_id = store.enqueue("WMS-2", '{"kind": "start"}')
    controller = _Controller(failures=99)
    for _ in range(_DEFAULT_MAX_ATTEMPTS + 1):
        await run_drive_loop(cast("Any", controller), store, once=True)
    print(
        f"    {_status(store, event_id)}   handler ran {controller.calls}x "
        f"(bound is {_DEFAULT_MAX_ATTEMPTS})"
    )
    for e in store.failed_events():
        print(f"    dead-lettered: event {e['id']} for {e['correlation_id']} — {e['last_error']}")
    print("    `swarmkit pipeline status WMS-2` shows this; `pipeline retry-event` re-queues it.\n")

    print("  A killed worker — what no `except` block can catch")
    print("  " + "─" * 74)
    store = _store()
    event_id = store.enqueue("WMS-3", '{"kind": "start"}')
    store.claim("worker-that-was-SIGKILLed")
    print(f"    claimed, then the worker died:  {_status(store, event_id)}")
    reclaimed = store.claim("worker-2", visibility_timeout=0.0)
    got = reclaimed[0] if reclaimed else "none"
    print(f"    another worker reclaims it:     {_status(store, event_id)}  (id={got})")
    print("    BEFORE: claim() looked only at `queued`, so this row was invisible forever.\n")


if __name__ == "__main__":
    asyncio.run(main())
