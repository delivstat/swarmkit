"""A chat turn is recorded, and its audit trail is findable from the conversation.

Chat was the last topology run that recorded nothing. `POST /run/{topology}` always wrote a job,
`swarmkit run` since 1.150.0, a pipeline stage since 1.152.0 — a conversation turn wrote none. So a
conversation never appeared in `/jobs`, and its token cost was attributable to nobody.

The audit half was subtler, and worse. Audit events *were* written — measured, before any change:

    JOB ROWS:   0
    AUDIT ROWS: 2
       agent.completed  run_id=600c57ae-c390-420c-bcb4-8dd0c7bf6ae9
       agent.started    run_id=600c57ae-c390-420c-bcb4-8dd0c7bf6ae9

`ConversationManager.send` called `runtime.run` without a `thread_id`, so every turn's events —
and its trace file — landed under a fresh random UUID that nothing pointed at. The record existed
and could not be reached from the conversation that caused it, which is the same as not having it.

Both halves come from one change: give the turn an id (`<conversation>:<n>`) and write a job row
carrying the conversation as `correlation_id`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._conversation import Conversation, ConversationManager, turn_run_id


class _Store:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str, str, str]] = []
        self.updates: list[dict[str, Any]] = []

    def create_job(
        self,
        job_id: str,
        topology: str,
        user_input: str,
        correlation_id: str | None = None,
        source: str | None = None,
    ) -> None:
        self.created.append((job_id, topology, user_input, correlation_id or "", source or ""))

    def update_job(self, job_id: str, **fields: Any) -> None:
        self.updates.append({"job_id": job_id, **fields})


class _Usage:
    input_tokens = 1200
    output_tokens = 340
    cost_usd = 0.42


class _Result:
    def __init__(self, output: str = "the answer") -> None:
        self.output = output
        self.events: list[Any] = []
        self.usage = _Usage()


class _Runtime:
    def __init__(self, store: Any, raises: BaseException | None = None) -> None:
        self.store = store
        self._raises = raises
        self.thread_ids: list[str] = []

    async def run(self, _topology: str, _input: str, *, thread_id: str = "", **_kw: Any) -> Any:
        self.thread_ids.append(thread_id)
        if self._raises is not None:
            raise self._raises
        return _Result()


def _manager(store: Any, tmp_path: Path, raises: BaseException | None = None) -> Any:
    return ConversationManager(_Runtime(store, raises), tmp_path)  # type: ignore[arg-type]


def _conversation(tmp_path: Path, turns: int = 0) -> Conversation:
    from swarmkit_runtime._conversation import ConversationTurn  # noqa: PLC0415

    conv = Conversation(id="c1", workspace_path=str(tmp_path), topology_name="advisor")
    for i in range(turns):
        conv.turns.append(ConversationTurn(role="human", content=f"q{i}"))
        conv.turns.append(ConversationTurn(role="swarm", content=f"a{i}"))
    return conv


# ---- the turn is recorded ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_is_recorded_as_a_job(tmp_path: Path) -> None:
    """The bug: a conversation left no job row at all."""
    store = _Store()

    await _manager(store, tmp_path).send(_conversation(tmp_path), "hello")

    assert len(store.created) == 1
    assert store.created[0][1] == "advisor"
    assert store.created[0][2] == "hello"


@pytest.mark.asyncio
async def test_the_job_is_linked_to_the_conversation(tmp_path: Path) -> None:
    """So `/jobs/history?correlation_id=<conversation>` returns that chat and nothing else — the
    same link a pipeline run's stages get."""
    store = _Store()

    await _manager(store, tmp_path).send(_conversation(tmp_path), "hello")

    assert store.created[0][3] == "c1"


@pytest.mark.asyncio
async def test_the_run_now_has_a_thread_id(tmp_path: Path) -> None:
    """The audit half. Without one, every turn's events and its trace landed under a fresh random
    UUID that no conversation pointed at."""
    runtime = _Runtime(_Store())
    manager = ConversationManager(runtime, tmp_path)  # type: ignore[arg-type]

    await manager.send(_conversation(tmp_path), "hello")

    assert runtime.thread_ids == ["c1:1"]


@pytest.mark.asyncio
async def test_turns_are_numbered_by_exchange(tmp_path: Path) -> None:
    """Not by list position — turns hold both sides, so positions would run 1, 3, 5 and read as
    gaps in a record that has none."""
    store = _Store()

    await _manager(store, tmp_path).send(_conversation(tmp_path, turns=2), "third question")

    assert store.created[0][0] == "c1:3"


@pytest.mark.asyncio
async def test_each_turn_gets_its_own_id(tmp_path: Path) -> None:
    """Per-turn, because the id is also the trace filename — one id per conversation would make
    each turn overwrite the previous turn's trace."""
    store = _Store()
    manager = _manager(store, tmp_path)
    conv = _conversation(tmp_path)

    await manager.send(conv, "first")
    await manager.send(conv, "second")

    assert [row[0] for row in store.created] == ["c1:1", "c1:2"]


def test_the_run_id_is_readable() -> None:
    assert turn_run_id("c1", 3) == "c1:3"


# ---- the row closes ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_completed_turn_records_output_and_usage(tmp_path: Path) -> None:
    store = _Store()

    await _manager(store, tmp_path).send(_conversation(tmp_path), "hello")

    done = store.updates[-1]
    assert done["status"] == "completed"
    assert done["output"] == "the answer"
    assert done["usage_input_tokens"] == 1200
    assert done["usage_cost_usd"] == 0.42


@pytest.mark.asyncio
async def test_a_failed_turn_closes_its_row(tmp_path: Path) -> None:
    """And the failure still propagates — recording it must not swallow it."""
    store = _Store()

    with pytest.raises(RuntimeError):
        await _manager(store, tmp_path, raises=RuntimeError("boom")).send(
            _conversation(tmp_path), "hello"
        )

    assert store.updates[-1]["status"] == "failed"
    assert "boom" in store.updates[-1]["error"]
    assert store.updates[-1]["completed_at"]


@pytest.mark.asyncio
async def test_an_interrupted_turn_closes_its_row(tmp_path: Path) -> None:
    """`BaseException`, not `Exception` — a Ctrl-C mid-answer would otherwise leave the row at
    `running` forever, which is the stalled shape."""
    store = _Store()

    with pytest.raises(KeyboardInterrupt):
        await _manager(store, tmp_path, raises=KeyboardInterrupt()).send(
            _conversation(tmp_path), "hello"
        )

    assert store.updates[-1]["completed_at"]


# ---- recording never costs the conversation ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_broken_store_does_not_stop_the_conversation(tmp_path: Path) -> None:
    """The one-directional rule: losing the record is acceptable, losing the answer is not."""

    class _Broken(_Store):
        def create_job(self, *_a: Any, **_kw: Any) -> None:
            raise OSError("disk went away")

        def update_job(self, *_a: Any, **_kw: Any) -> None:
            raise OSError("disk went away")

    result = await _manager(_Broken(), tmp_path).send(_conversation(tmp_path), "hello")

    assert result.output == "the answer"


@pytest.mark.asyncio
async def test_no_store_does_not_stop_the_conversation(tmp_path: Path) -> None:
    class _NoStore:
        """A runtime whose store will not open — a workspace with no durable storage."""

        @property
        def store(self) -> Any:
            raise RuntimeError("no store configured")

        async def run(self, _t: str, _i: str, **_kw: Any) -> Any:
            return _Result()

    manager = ConversationManager(_NoStore(), tmp_path)  # type: ignore[arg-type]

    result = await manager.send(_conversation(tmp_path), "hello")

    assert result.output == "the answer"
