"""A curated fact reaches the run that needs it.

Bug 21. There are two memory subsystems with different shapes — workspace memory is
`{topic, context, key_points}`, governed memory is `{subject, attribute, value}` — and the
`memory-reader` bound at `pre_input` only ever read the first. So a workspace could curate a fact
through the documented flow (reconcile-on-write, quarantine, a human gate on resolution, confidence
and decay), see it in `swarmkit memory search` and the `/memory` page, and watch every run behave as
though memory did not exist.

Nothing said so. The reader searched a store the facts were not in and reported finding nothing,
which is exactly what finding nothing looks like.

`sn8::carton-count-source` was curated from a human correction and is precisely the fact a later
design run needed. That run produced a 33 KB spec enumerating cartons the guessed way; `getTaskList`
and `PickInv` appear nowhere in it. The machinery exists to make a fact trustworthy enough to act
on, and nothing could act on it.

Two decisions worth stating:

**The read path does not require the write skill.** `_governed_memory` in the compiler is gated on
carrying `governed-memory`, which grants `kb:write`. Requiring it to READ would mean granting every
agent that should merely see curated facts the ability to write them — the exact workaround a
curated store exists to prevent, and the one the report warns "make memory reach the agent" reads
as.

**Curated facts come first.** They went through reconciliation and a human gate; workspace memory is
whatever a previous run happened to record. When the two disagree, the reviewed one should be read
first.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from swarmkit_runtime.governed_memory import GovernedMemoryStore
from swarmkit_runtime.governed_memory._models import MemoryCandidate
from swarmkit_runtime.memory._gate import memory_pre_input

FACT = (
    "Carton count and carton identity come from the TASK LIST, not from "
    "Shipment/Containers/Container. Call getTaskList with TaskType=PickInv, TaskStatus=9000."
)


class _Reader:
    """A `memory-reader` binding at `pre_input`, as a topology declares it."""

    id = "memory-reader"
    trigger = "pre_input"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def applies_to(self, _agent_id: str) -> bool:
        return True


def _governed(*facts: tuple[str, str, str]) -> GovernedMemoryStore:
    store = GovernedMemoryStore(
        create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'memory.sqlite'}")
    )
    for subject, attribute, value in facts:
        store.write(MemoryCandidate(subject=subject, attribute=attribute, value=value))
    return store


async def _inject(governed: Any, question: str, bindings: Any = None) -> str | None:
    return await memory_pre_input(
        agent_id="designer",
        user_input=question,
        bindings=cast("Any", bindings if bindings is not None else [_Reader()]),
        store=None,
        governed_store=governed,
    )


# ---- the curated fact arrives --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_curated_fact_reaches_the_agent() -> None:
    """The reported case: curated, visible in the CLI and the UI, and read by nothing."""
    governed = _governed(("sn8", "carton-count-source", FACT))

    context = await _inject(governed, "how do I count cartons for a shipment?")

    assert context is not None
    assert "getTaskList" in context


@pytest.mark.asyncio
async def test_the_subject_and_attribute_are_shown() -> None:
    """A value without its key is an assertion from nowhere. The subject is what tells an agent
    which solution the fact is about."""
    governed = _governed(("sn8", "carton-count-source", FACT))

    context = await _inject(governed, "carton count")

    assert "sn8" in str(context)
    assert "carton-count-source" in str(context)


@pytest.mark.asyncio
async def test_the_block_is_labelled_and_delimited() -> None:
    """Like every other injected block: an agent that cannot tell a curated fact from the user's
    own words is being asked to trust unattributed instructions."""
    governed = _governed(("sn8", "carton-count-source", FACT))

    context = str(await _inject(governed, "carton count"))

    assert context.startswith("<curated-memory>")
    assert context.rstrip().endswith("</curated-memory>")


@pytest.mark.asyncio
async def test_it_works_without_workspace_memory_configured() -> None:
    """The gate used to require a workspace-memory store to run at all, so a workspace that curated
    facts and configured nothing else was never even consulted."""
    governed = _governed(("sn8", "carton-count-source", FACT))

    assert await _inject(governed, "carton count") is not None


# ---- and does not arrive when it should not ------------------------------------------------------


@pytest.mark.asyncio
async def test_no_reader_binding_means_no_injection() -> None:
    """Reading is gated by the `memory-reader` binding — an agent that was not given one does not
    silently acquire curated context."""
    governed = _governed(("sn8", "carton-count-source", FACT))

    assert await _inject(governed, "carton count", bindings=[]) is None


@pytest.mark.asyncio
async def test_an_empty_store_injects_nothing() -> None:
    assert await _inject(_governed(), "carton count") is None


@pytest.mark.asyncio
async def test_no_governed_store_is_safe() -> None:
    """A workspace with neither store configured must not crash the node."""
    assert await _inject(None, "carton count") is None


@pytest.mark.asyncio
async def test_a_broken_store_costs_the_context_not_the_run() -> None:
    """Best-effort in one direction only. Silence was the original defect, so the failure is logged
    rather than swallowed — but the run still happens."""

    class _Broken:
        def search(self, *_a: Any, **_kw: Any) -> Any:
            raise OSError("the memory database went away")

    assert await _inject(_Broken(), "carton count") is None


# ---- reading does not require the ability to write -----------------------------------------------


def test_the_read_path_does_not_go_through_the_write_gate() -> None:
    """`_governed_memory` is gated on the `governed-memory` skill, which carries `kb:write`.
    Requiring it to read would mean granting write to every agent that should merely see curated
    facts — the workaround the report warns "make memory reach the agent" reads as."""
    from pathlib import Path as P  # noqa: PLC0415

    src = (
        P(__file__).resolve().parents[1] / "src/swarmkit_runtime/langgraph_compiler/_compiler.py"
    ).read_text()

    assert "governed_store=governed_memory_store," in src
    assert "governed_store=_governed_memory" not in src


@pytest.mark.asyncio
async def test_the_limit_is_configurable() -> None:
    """A prompt has a budget. Five facts is a default, not a law."""
    governed = _governed(
        ("sn8", "a", "carton fact one"),
        ("sn8", "b", "carton fact two"),
        ("sn8", "c", "carton fact three"),
    )

    context = str(await _inject(governed, "carton", bindings=[_Reader({"governed_limit": 1})]))

    assert context.count("- sn8 · ") == 1
