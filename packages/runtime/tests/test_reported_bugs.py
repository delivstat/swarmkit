"""Regressions for the bugs reported against runtime 1.123.0 (wms-support pipeline, 2026-07-31).

Each test names the failure mode it locks down, because every one of these was expensive to find by
external observation: they all failed *silently*, and several produced output indistinguishable from
success.

Bug 1 (storage config never read) lives in test_store_factory.py, next to the tests that missed it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from fastapi import HTTPException
from swarmkit_runtime.executors._adapter_spec import parse_adapter_spec
from swarmkit_runtime.executors._declarative import DeclarativeExecutor
from swarmkit_runtime.executors._events import ExecResult
from swarmkit_runtime.executors._run import BudgetEnvelope, SandboxHandle, TaskSpec

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"

ADAPTER = {
    "apiVersion": "swarmkit/v1",
    "kind": "ExecutorAdapter",
    "metadata": {"id": "fake", "name": "Fake", "description": "test adapter"},
    "spec": {
        "launch": {"command": ["fake-harness", "{task.statement}"]},
        "event_map": [
            {
                "when": {"type": "result"},
                "emit": [{"event": "result", "with": {"status": "success"}}],
            }
        ],
    },
    "provenance": {"authored_by": "human", "version": "1.0.0"},
}


# ---- bug 2: the harness's stderr was captured and thrown away ----------------------------------


def _executor() -> DeclarativeExecutor:
    return DeclarativeExecutor(parse_adapter_spec(ADAPTER))


@pytest.mark.asyncio
async def test_stderr_is_drained_and_surfaced_on_an_early_exit(tmp_path: Path) -> None:
    """A harness that dies before emitting its terminal `result` event used to be recorded as the
    46-character string "no result event", with the reason sitting unread in a piped stderr nobody
    drained. Now the exit code + a bounded tail ride on the terminal event."""
    ex = _executor()

    async def fake_stream(argv: list[str], env: Any, cwd: Path, run_id: str) -> AsyncIterator[str]:
        # A real subprocess: writes to stderr and exits nonzero without any stdout result event.
        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            ">&2 echo 'error: unknown flag --foo'; >&2 echo 'usage: fake-harness'; exit 3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        ex._active[run_id] = proc
        tail: list[str] = []

        async def drain() -> None:
            assert proc.stderr is not None
            async for raw in proc.stderr:
                tail.append(raw.decode().rstrip("\n"))

        drainer = asyncio.create_task(drain())
        assert proc.stdout is not None
        async for line in proc.stdout:
            yield line.decode()
        await proc.wait()
        await drainer
        ex._exits[run_id] = {"returncode": proc.returncode, "stderr_tail": "\n".join(tail)}
        ex._active.pop(run_id, None)

    ex._open_stream = fake_stream  # type: ignore[method-assign]

    events = [
        e
        async for e in ex.run(
            TaskSpec(statement="do it"),
            SandboxHandle(root=tmp_path),
            BudgetEnvelope(),
        )
    ]
    terminal = next(e for e in events if isinstance(e, ExecResult))
    assert terminal.status == "failure"
    assert terminal.exit_metadata["returncode"] == 3
    assert "unknown flag --foo" in terminal.exit_metadata["stderr_tail"]


@pytest.mark.asyncio
async def test_real_stream_drains_stderr_past_the_pipe_buffer(tmp_path: Path) -> None:
    """The latent deadlock: a harness writing more than the ~64KB pipe buffer to stderr blocks on
    write forever when nothing drains it, so `proc.wait()` never returns. The real `_open_stream`
    must survive it."""
    ex = _executor()
    argv = ["sh", "-c", "yes 'noise line for the pipe buffer' | head -c 200000 >&2; exit 1"]

    async def consume() -> list[str]:
        return [line async for line in ex._open_stream(argv, {}, tmp_path, "run-1")]

    # The bug is a hang, so the assertion is that this completes at all.
    lines = await asyncio.wait_for(consume(), timeout=20)
    assert lines == []
    assert ex._exits["run-1"]["returncode"] == 1
    assert ex._exits["run-1"]["stderr_tail"]


@pytest.mark.asyncio
async def test_stderr_tail_is_bounded(tmp_path: Path) -> None:
    """Bounded so a chatty harness cannot grow the process."""
    ex = _executor()
    argv = ["sh", "-c", 'for i in $(seq 1 5000); do echo "line $i" >&2; done; exit 1']
    _ = [line async for line in ex._open_stream(argv, {}, tmp_path, "run-2")]
    tail = ex._exits["run-2"]["stderr_tail"].splitlines()
    assert len(tail) <= 200
    assert tail[-1] == "line 5000"  # the TAIL, which is the part that names the cause


# ---- bug 3: run-stage accepted stage.input and silently ignored it ------------------------------


def test_task_spec_does_not_fall_back_to_the_role_name() -> None:
    """The worst of the reported bugs: with no resolvable input the harness node used the agent's
    ROLE NAME as the prompt, so the node called the model, returned success and wrote a plausible
    artifact. A run that received nothing was indistinguishable from one that did its job."""
    from swarmkit_runtime.langgraph_compiler._harness_node import _task_spec  # noqa: PLC0415

    class _Agent:
        id = "root"
        role = "root"
        skills: ClassVar[list[Any]] = []
        executor = None

    def _state(text: str) -> Any:
        """A partial SwarmState — `_task_spec` reads only `input`, and spelling out every key of the
        TypedDict here would obscure the one field under test."""
        return cast("dict[str, Any]", {"input": text})

    spec = _task_spec(_Agent(), _state(""), None)  # type: ignore[arg-type]
    assert spec.statement == ""  # NOT "root"

    spec = _task_spec(_Agent(), _state("a real payload"), None)  # type: ignore[arg-type]
    assert spec.statement == "a real payload"


def test_harness_task_spec_has_no_role_fallback() -> None:
    """`statement = state.get("input") or agent.role` — running an agent with its own role name as
    the prompt is never intended, costs real money, and produces a plausible-looking artifact."""
    src = (
        REPO / "packages/runtime/src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()
    assert "or agent.role" not in src


# ---- operational traps: the webhook failed open -------------------------------------------------


def test_webhook_with_a_missing_secret_refuses_the_request() -> None:
    """It logged a warning, skipped signature validation and ACCEPTED the request. Neither serve nor
    the orchestrator loads .env, so an unexported variable was the default state — the declared
    protection was absent while looking configured."""
    from swarmkit_runtime.server._helpers import _check_pipeline_webhook_signature  # noqa: PLC0415

    class _Req:
        headers: ClassVar[dict[str, str]] = {}

    trigger = {
        "id": "ticket-hook",
        "config": {"auth": {"credentials_ref": "DEFINITELY_NOT_SET_ANYWHERE"}},
    }
    with pytest.raises(HTTPException) as exc:
        _check_pipeline_webhook_signature(_Req(), b"{}", trigger)  # type: ignore[arg-type]
    assert exc.value.status_code == 503
    assert "unsigned" in str(exc.value.detail)


def test_webhook_without_declared_auth_still_passes() -> None:
    """Fail-closed applies to a DECLARED secret that is absent, not to a trigger with no auth."""
    from swarmkit_runtime.server._helpers import _check_pipeline_webhook_signature  # noqa: PLC0415

    class _Req:
        headers: ClassVar[dict[str, str]] = {}

    _check_pipeline_webhook_signature(_Req(), b"{}", {"id": "open-hook", "config": {}})  # type: ignore[arg-type]


# ---- bug 4 / traps: the orchestrator resolved its store independently ---------------------------
