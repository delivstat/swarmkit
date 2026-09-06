"""A resolved gate continues its run.

The decision in `extracting-the-channels.md`: otherwise every application writes the same
`POST /jobs/{id}/resume`, and the one that forgets leaves a run parked *after* its gate is
satisfied — a stall with no visible cause, because everything looks resolved.

The path this replaces went through `app.state.pipeline_signal`, which nothing has set since the
bundled sequencer left in 1.189.0. `_resume_if_gate_resolved` returned at its first guard in every
deployment: an orphan, like the notification providers and `hitl_requested_event` before it.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.server import _routes_review as rr

WORKSPACE = (
    "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: t, name: T}\n"
    "governance: {provider: mock, policy_language: yaml}\n"
)


def test_the_dead_signal_no_longer_guards_gate_evaluation() -> None:
    """The regression that matters: nothing sets `pipeline_signal`, so requiring it meant the whole
    branch was unreachable. Evaluation must not depend on it."""
    src = inspect.getsource(rr._resume_if_gate_resolved)
    assert "or signal is None" not in src
    assert "if not gate_id or not funnel_id:" in src


def test_resume_is_skipped_when_there_is_no_job_machinery() -> None:
    """A CLI-driven workspace resolves gates through the same queue and has no job to resume."""

    class _State:
        pass

    class _Request:
        app = type("A", (), {"state": _State()})()

    assert rr._resume_from_state(_Request()) is None  # type: ignore[arg-type]


def test_auto_resume_can_be_turned_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An application that batches resumption opts out; the explicit endpoint still works."""

    class _Raw:
        gates = type("G", (), {"auto_resume": False})()

    class _Workspace:
        raw = _Raw()

    class _State:
        job_store = object()
        runtime = type("R", (), {"workspace": _Workspace()})()

    class _Request:
        app = type("A", (), {"state": _State()})()

    assert rr._resume_from_state(_Request()) is None  # type: ignore[arg-type]


def test_auto_resume_is_on_by_default() -> None:
    class _Raw:
        pass

    class _Workspace:
        raw = _Raw()

    class _State:
        job_store = object()
        runtime = type("R", (), {"workspace": _Workspace()})()

    class _Request:
        app = type("A", (), {"state": _State()})()

    assert rr._resume_from_state(_Request()) is not None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_failed_resume_never_makes_a_gate_unresolvable() -> None:
    """The gate resolving must not depend on the resume succeeding — an un-resumable run is not an
    un-resolvable gate. This is the property that let the `await`-on-a-sync-call bug stay contained
    to a log line while the approval still recorded."""

    async def _explode(_run_id: str) -> None:
        msg = "no"
        raise RuntimeError(msg)

    await rr._resume_parked_job(_explode, "run-1")


@pytest.mark.asyncio
async def test_no_run_id_is_a_no_op() -> None:
    called = {"n": 0}

    async def _resume(_run_id: str) -> None:
        called["n"] += 1

    await rr._resume_parked_job(_resume, "")
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_the_run_is_resumed_by_id(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _resume(run_id: str) -> None:
        seen["run_id"] = run_id

    await rr._resume_parked_job(_resume, "3c4441c0037c")
    assert seen["run_id"] == "3c4441c0037c"
