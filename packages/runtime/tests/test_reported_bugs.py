"""Regressions for the bugs reported against runtime 1.123.0 (wms-support pipeline, 2026-07-31).

Each test names the failure mode it locks down, because every one of these was expensive to find by
external observation: they all failed *silently*, and several produced output indistinguishable from
success.

Bug 1 (storage config never read) lives in test_store_factory.py, next to the tests that missed it.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from fastapi import HTTPException
from swarmkit_runtime.executors._adapter_spec import parse_adapter_spec
from swarmkit_runtime.executors._declarative import DeclarativeExecutor
from swarmkit_runtime.executors._events import ExecResult
from swarmkit_runtime.executors._run import BudgetEnvelope, SandboxHandle, TaskSpec
from swarmkit_runtime.orchestration import StageOutcome

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


def test_stage_input_precedence(tmp_path: Path) -> None:
    from swarmkit_runtime.server._pipeline_stage import _stage_input  # noqa: PLC0415

    class _Saga:
        input = "saga payload"
        passed_stages: ClassVar[list[str]] = []

    class _SagaStore:
        def get(self, _cid: str) -> Any:
            return _Saga()

    class _Artifacts:
        def list(self, _cid: str) -> list[str]:
            return []

        def get(self, _ref: str) -> str | None:
            return None

    store, artifacts = _SagaStore(), _Artifacts()
    # 1. an explicit stage.input wins — it used to be accepted and dropped
    assert _stage_input(store, artifacts, "c", {"input": "explicit"}) == "explicit"  # type: ignore[arg-type]
    # 2. else the saga's payload, for the first stage
    assert _stage_input(store, artifacts, "c", {}) == "saga payload"  # type: ignore[arg-type]
    assert _stage_input(store, artifacts, "c", None) == "saga payload"  # type: ignore[arg-type]


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


def test_orchestrator_resolves_the_same_store_as_serve(tmp_path: Path) -> None:
    """Masked while serve ignored storage.runtime (both landed on sqlite and agreed by accident).
    With bug 1 fixed an independent default is a live split brain: serve queues events into one
    database while the orchestrator polls another, and neither process warns."""
    from swarmkit_runtime.cli._cmd_orchestrator import _resolve_saga_store_url  # noqa: PLC0415

    ws = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, ws)
    (ws / "workspace.yaml").write_text(
        (ws / "workspace.yaml").read_text()
        + "\nstorage:\n  runtime:\n    backend: postgres\n    url: postgresql://db/app\n"
    )
    url, source = _resolve_saga_store_url(ws, None)
    assert url == "postgresql://db/app"
    assert source == "workspace.yaml"


def test_orchestrator_database_url_is_an_explicit_override(tmp_path: Path) -> None:
    from swarmkit_runtime.cli._cmd_orchestrator import _resolve_saga_store_url  # noqa: PLC0415

    url, source = _resolve_saga_store_url(tmp_path, "sqlite:///elsewhere.db")
    assert (url, source) == ("sqlite:///elsewhere.db", "--database-url")


# ---- operational trap: a parked stage's artifact size distinguishes failure from success ---------


def test_stage_outcome_reports_artifact_size() -> None:
    """A FAILED stage parks exactly like a successful one and the two render identically. A real
    record is kilobytes; a harness failure is a ~46-character error string."""
    assert StageOutcome(status="parked").artifact_bytes is None
    assert StageOutcome(status="parked", artifact_bytes=3421).artifact_bytes == 3421


@pytest.mark.asyncio
async def test_run_stage_populates_artifact_bytes(tmp_path: Path) -> None:
    from swarmkit_runtime.artifacts import build_artifact_store  # noqa: PLC0415
    from swarmkit_runtime.server._pipeline_stage import build_pipeline_run_stage  # noqa: PLC0415

    ws = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, ws)
    shutil.rmtree(ws / ".swarmkit", ignore_errors=True)
    (ws / ".swarmkit").mkdir()

    class _Saga:
        input = "a real payload"
        passed_stages: ClassVar[list[str]] = []

    class _SagaStore:
        def get(self, _cid: str) -> Any:
            return _Saga()

    class _Result:
        output = "x" * 3421

    class _Runtime:
        async def run(self, *a: Any, **k: Any) -> Any:
            return _Result()

    run_stage = build_pipeline_run_stage(
        _Runtime(),  # type: ignore[arg-type]
        build_artifact_store(
            None, workspace_root=ws, database_url=f"sqlite:///{ws / '.swarmkit' / 's.sqlite'}"
        ),
        _SagaStore(),  # type: ignore[arg-type]
    )
    outcome = await run_stage("c1", {"id": "s1", "topology": "hello", "gate": "g"})
    assert outcome.status == "parked"
    assert outcome.artifact_bytes == 3421
