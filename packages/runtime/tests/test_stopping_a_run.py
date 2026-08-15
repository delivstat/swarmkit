"""A human can stop a run from another terminal, and resume it later.

`design/details/stopping-a-run.md`. `swarmkit stop` was a `_not_implemented` stub since M6 with a
docstring that already promised the semantics: *"Requests the runtime to checkpoint state and abort
the current run. The run can be resumed later with `swarmkit run --resume`."*

The design in one line: **a stop is a deferral without a gate.** The runtime already parks a run
mid-flight and resumes it (a funnel's `approve` layer raising `HITLDeferredError`), so `stop` reuses
that path rather than writing a second resumption mechanism — the cost of getting stop wrong is a
run that cannot be resumed, and the way to avoid that is to not have two implementations.

The transport is a **column**, not a signal or an in-memory token: `stop` exists to reach a run in
another process (the terminal that started one already has Ctrl-C), and the durable store is the
only channel that already connects every writer to every reader.

What these tests pin:

* the flag is read at a node boundary, so the work already paid for survives;
* `stopped` is its own status — not `deferred` (which means "waiting on a human decision that will
  arrive") and not `failed` (nothing went wrong);
* a stopped run resumes, and the resume **clears** the flag so it does not immediately re-stop;
* stopping a finished run is a no-op, not an error;
* the act reaches the audit log with who asked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._stop_requests import (
    reset_stop_checker,
    set_stop_checker,
    stop_requested,
    store_backed_checker,
)
from swarmkit_runtime.persistence._store import Store, make_engine
from swarmkit_runtime.review._hitl import HITLDeferredError, RunStoppedError
from swarmkit_runtime.stop import STOPPABLE, request_stop


def _store(tmp_path: Path) -> Store:
    return Store(make_engine(f"sqlite:///{tmp_path / 's.sqlite'}"))


def _running(store: Store, job_id: str = "job-1") -> None:
    store.create_job(job_id, "wms-design", "draft it")
    store.update_job(job_id, status="running")


# ---- the request ---------------------------------------------------------------------------------


def test_a_running_job_can_be_asked_to_stop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _running(store)

    outcome = request_stop(store, "job-1")

    assert outcome is not None
    assert outcome.requested is True
    row = store.get_job("job-1")
    assert row is not None and row.stop_requested_at == outcome.requested_at


def test_stopping_a_finished_job_is_a_no_op_not_an_error(tmp_path: Path) -> None:
    """An operator racing a run that just finished should not get a stack trace, and the honest
    answer is that there is nothing to stop."""
    store = _store(tmp_path)
    store.create_job("job-1", "t", "i")
    store.update_job("job-1", status="completed")

    outcome = request_stop(store, "job-1")

    assert outcome is not None
    assert outcome.requested is False
    assert outcome.status == "completed"
    row = store.get_job("job-1")
    assert row is not None and row.stop_requested_at is None


def test_an_unknown_run_is_reported_as_unknown(tmp_path: Path) -> None:
    assert request_stop(_store(tmp_path), "nope") is None


def test_asking_twice_reports_the_pending_request(tmp_path: Path) -> None:
    """Idempotent because an operator who cannot tell whether the first one landed presses again."""
    store = _store(tmp_path)
    _running(store)
    first = request_stop(store, "job-1")
    assert first is not None

    second = request_stop(store, "job-1")

    assert second is not None
    assert second.already_requested is True
    assert second.requested_at == first.requested_at


def test_only_a_live_run_is_stoppable() -> None:
    assert {"pending", "running"} == STOPPABLE


# ---- the checker ---------------------------------------------------------------------------------


def test_a_node_sees_the_request(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _running(store)
    token = set_stop_checker(store_backed_checker(store, "job-1"))
    try:
        assert stop_requested() is False

        request_stop(store, "job-1")

        assert stop_requested() is True
    finally:
        reset_stop_checker(token)


def test_the_check_is_not_cached(tmp_path: Path) -> None:
    """The first version cached a "no" for a second, to save round-trips. The demo showed what that
    buys: a run whose agents are fast blows past the request and finishes, so the stop is not late —
    it is MISSED. One indexed SELECT against a node that takes seconds is not a cost worth a feature
    that sometimes does nothing."""
    store = _store(tmp_path)
    _running(store)
    reads = {"n": 0}

    class _Counting:
        def get_job(self, job_id: str) -> Any:
            reads["n"] += 1
            return store.get_job(job_id)

    check = store_backed_checker(_Counting(), "job-1")

    assert check() is False
    assert check() is False
    assert reads["n"] == 2, "every boundary asks — a cached 'no' is how a stop gets missed"

    request_stop(store, "job-1")
    assert check() is True, "and the very next boundary sees it"


def test_a_seen_stop_latches(tmp_path: Path) -> None:
    """Once the answer is yes the run is about to raise; re-reading the row to confirm a decision
    already made is the query that looks free until a graph has thirty nodes."""
    store = _store(tmp_path)
    _running(store)
    request_stop(store, "job-1")
    reads = {"n": 0}

    class _Counting:
        def get_job(self, job_id: str) -> Any:
            reads["n"] += 1
            return store.get_job(job_id)

    check = store_backed_checker(_Counting(), "job-1")

    assert check() is True
    assert check() is True
    assert reads["n"] == 1


def test_no_checker_means_not_stopped() -> None:
    """A test, a compile, a dry run: outside a run there is nothing to stop."""
    assert stop_requested() is False


def test_an_unreadable_store_does_not_stop_the_run(tmp_path: Path) -> None:
    """A store that will not answer is a reason to keep running, never a reason to kill work in
    flight."""

    def _broken() -> bool:
        raise RuntimeError("database is gone")

    token = set_stop_checker(_broken)
    try:
        assert stop_requested() is False
    finally:
        reset_stop_checker(token)


# ---- the error is a deferral --------------------------------------------------------------------


def test_a_stop_is_a_deferral() -> None:
    """The subclass is the design: every caller that already knows how to checkpoint-and-exit on a
    deferral handles a stop unchanged, and there is no second resumption path to keep in step."""
    exc = RunStoppedError("run-1", "designer")

    assert isinstance(exc, HITLDeferredError)
    assert exc.run_id == "run-1"
    assert "designer" in str(exc)


def test_the_node_checks_before_it_works() -> None:
    """Checked at node ENTRY — after the previous super-step checkpointed, before this node spends
    anything. That placement is what makes "stop without losing what you already paid for" true."""
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/langgraph_compiler/_compiler.py"
    ).read_text()
    node = src[src.index("async def node_fn(") :][:1200]

    assert "if stop_requested():" in node
    assert "raise RunStoppedError" in node
    assert node.index("stop_requested()") < node.index("agent.started"), (
        "the check precedes the node's first act, or the work it was meant to save is already spent"
    )


# ---- the status ---------------------------------------------------------------------------------


def test_the_cli_records_stopped_not_deferred_or_failed() -> None:
    """`deferred` means "waiting on a human decision that will arrive"; counting a stop among those
    makes "how many runs are blocked on approvals" quietly wrong."""
    src = (Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/cli/_cmd_run.py").read_text()

    assert "if isinstance(exc, RunStoppedError):" in src
    assert '_finish_job(store, thread_id, "stopped"' in src
    assert src.index("RunStoppedError):") < src.index("HITLDeferredError):"), (
        "the subclass must be caught first, or a stop is recorded as a gate deferral"
    )


def test_serve_records_stopped_too() -> None:
    src = (Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_jobs.py").read_text()

    assert "except RunStoppedError as exc:" in src
    assert 'job.status = "stopped"' in src
    assert src.index("except RunStoppedError") < src.index("except HITLDeferredError")
    assert '"deferred", "stopped"' in src, "and the Job literal admits it"


# ---- resuming -----------------------------------------------------------------------------------


def test_a_resume_clears_the_request(tmp_path: Path) -> None:
    """Without this a stopped run resumes and immediately re-stops on the stale flag, which reads
    as a resume that does not work."""
    store = _store(tmp_path)
    _running(store)
    request_stop(store, "job-1")

    store.update_job("job-1", clear_stop_request=True)

    row = store.get_job("job-1")
    assert row is not None and row.stop_requested_at is None


def test_the_runtime_clears_it_on_resume() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/_workspace_runtime.py"
    ).read_text()
    resume = src[src.index("    async def resume(") :][:2000]

    assert "self._clear_stop_request(thread_id)" in resume
    assert "set_stop_checker" in resume, "a resumed run must be stoppable exactly like a first one"


def test_a_stopped_job_resumes_over_http() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_routes_jobs.py"
    ).read_text()

    assert '_RESUMABLE = frozenset({"deferred", "stopped"})' in src
    assert "if found.status not in _RESUMABLE:" in src


# ---- the surfaces -------------------------------------------------------------------------------


def test_the_stop_route_is_registered() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_routes_jobs.py"
    ).read_text()

    assert '@app.post("/jobs/{job_id}/stop")' in src
    assert "request_stop(store, job_id)" in src, "one mechanism, two front doors"


def test_the_cli_is_no_longer_a_stub() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/cli/_cmd_observability.py"
    ).read_text()
    stop = src[src.index("def stop(") :][:2400]

    assert "_not_implemented" not in stop
    assert "request_stop(store, run_id)" in stop
    assert "next agent boundary" in stop, "the latency is stated, not implied away"


def test_the_flag_reaches_a_reader(tmp_path: Path) -> None:
    """A column nobody can read is the defect this repo keeps finding. The bug-28 guard asserts
    every JobRow field is reachable through the merged view; this rides on it."""
    from swarmkit_runtime.server._jobs import Job  # noqa: PLC0415
    from swarmkit_runtime.server._routes_jobs import _resolve_job  # noqa: PLC0415

    store = _store(tmp_path)
    _running(store)
    request_stop(store, "job-1")

    view = _resolve_job(
        Job(id="job-1", topology="t", status="running", input="i"), store.get_job("job-1")
    )

    assert view is not None
    assert view.stop_requested_at


def test_the_column_is_added_to_an_existing_table(tmp_path: Path) -> None:
    from sqlalchemy import inspect, text  # noqa: PLC0415

    engine = make_engine(f"sqlite:///{tmp_path / 'old.sqlite'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, topology TEXT NOT NULL, "
                "status TEXT NOT NULL, input TEXT NOT NULL, created_at TEXT NOT NULL, "
                "events TEXT, version TEXT, output TEXT, error TEXT, completed_at TEXT, "
                "usage_input_tokens INTEGER, usage_output_tokens INTEGER, usage_cost_usd FLOAT)"
            )
        )

    store = Store(engine)

    assert "stop_requested_at" in {c["name"] for c in inspect(engine).get_columns("jobs")}
    _running(store)
    assert request_stop(store, "job-1") is not None


# ---- the audit ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_stop_is_audited_with_who_asked(tmp_path: Path) -> None:
    """ "Who stopped the release run" is exactly what the audit log exists for, and a stop that
    appeared only as a status change could not answer it."""
    from swarmkit_runtime.audit import audit_provider_for_path  # noqa: PLC0415
    from swarmkit_runtime.stop import record_stop_requested  # noqa: PLC0415

    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: w, name: w}\n"
    )
    store = Store(make_engine(f"sqlite:///{tmp_path / 'jobs.sqlite'}"))
    _running(store)
    outcome = request_stop(store, "job-1")
    assert outcome is not None

    await record_stop_requested(tmp_path, outcome, requested_by="cli:srijith", topology_id="t")

    stopped = [
        e
        async for e in audit_provider_for_path(tmp_path).query(limit=10)
        if e.event_type == "run.stopped"
    ]
    assert stopped, "the act is recorded"
    assert stopped[0].payload["requested_by"] == "cli:srijith"
    assert stopped[0].run_id == "job-1"


@pytest.mark.asyncio
async def test_a_no_op_stop_is_not_audited(tmp_path: Path) -> None:
    """Nothing happened, so nothing is recorded — an audit trail that logs non-events is one a
    reader learns to skim."""
    from swarmkit_runtime.audit import audit_provider_for_path  # noqa: PLC0415
    from swarmkit_runtime.stop import record_stop_requested  # noqa: PLC0415

    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: w, name: w}\n"
    )
    store = Store(make_engine(f"sqlite:///{tmp_path / 'jobs.sqlite'}"))
    store.create_job("job-1", "t", "i")
    store.update_job("job-1", status="completed")
    outcome = request_stop(store, "job-1")
    assert outcome is not None

    await record_stop_requested(tmp_path, outcome, requested_by="cli:srijith")

    stopped = [
        e
        async for e in audit_provider_for_path(tmp_path).query(limit=10)
        if e.event_type == "run.stopped"
    ]
    assert stopped == []


# ---- end to end: a real multi-agent graph stops between agents ----------------------------------


@pytest.mark.asyncio
async def test_a_real_graph_stops_between_agents() -> None:
    """The whole feature against a real compiled multi-agent graph.

    `researcher -> writer -> editor`, sequential by `depends_on`. The stop is requested from inside
    the researcher's model call — the realistic timing — and takes effect at the next node boundary:
    the writer and the editor never run, so nothing past the stop is spent, and the researcher's
    work is on the checkpoint the run would resume from.
    """
    from swarmkit_runtime.governance._mock import MockGovernanceProvider  # noqa: PLC0415
    from swarmkit_runtime.langgraph_compiler import compile_topology  # noqa: PLC0415
    from swarmkit_runtime.model_providers import (  # noqa: PLC0415
        CompletionResponse,
    )
    from swarmkit_runtime.resolver import ResolvedAgent, ResolvedTopology  # noqa: PLC0415

    # The DAG suite's provider, which knows how to make a root delegate — reused rather than
    # re-implemented, so this test exercises the same execution path that suite proves works.
    from test_dag_e2e import TrackingMockProvider  # noqa: PLC0415

    asked = {"stop": False}

    class _Provider(TrackingMockProvider):
        async def complete(self, request: Any) -> CompletionResponse:
            response = await super().complete(request)
            if self.calls and self.calls[-1] == "researcher":
                asked["stop"] = True
            return response

    provider = _Provider()
    ran = provider.calls

    def _agent(agent_id: str, **over: Any) -> ResolvedAgent:
        return ResolvedAgent(
            id=agent_id,
            role=over.pop("role", "worker"),
            model={"provider": "mock", "name": "mock"},
            prompt={"system": f"You are {agent_id}."},
            skills=(),
            iam=None,
            **over,
        )

    topology = ResolvedTopology(
        id="pipeline",
        raw=None,  # type: ignore[arg-type]
        source_path=None,  # type: ignore[arg-type]
        root=_agent(
            "root",
            role="root",
            children=(
                _agent("researcher"),
                _agent("writer", depends_on=("researcher",)),
                _agent("editor", depends_on=("writer",)),
            ),
        ),
    )
    graph = compile_topology(
        topology,
        model_provider=provider,  # type: ignore[arg-type]
        governance=MockGovernanceProvider(),
    )

    token = set_stop_checker(lambda: asked["stop"])
    try:
        with pytest.raises(RunStoppedError) as exc:
            await graph.ainvoke(
                {
                    "input": "go",
                    "messages": [],
                    "agent_results": {},
                    "current_agent": "root",
                    "output": "",
                }
            )
    finally:
        reset_stop_checker(token)

    assert "researcher" in ran, "the agent already running finished its call"
    assert "writer" not in ran and "editor" not in ran, "nothing past the stop was spent"
    assert exc.value.agent_id == "writer", "it stopped AT the next agent, before that agent worked"


@pytest.mark.asyncio
async def test_a_graph_with_no_request_runs_every_agent() -> None:
    """The control: a stop that could fire without being asked for would be the worse bug."""
    from swarmkit_runtime.governance._mock import MockGovernanceProvider  # noqa: PLC0415
    from swarmkit_runtime.langgraph_compiler import compile_topology  # noqa: PLC0415
    from swarmkit_runtime.resolver import ResolvedAgent, ResolvedTopology  # noqa: PLC0415
    from test_dag_e2e import TrackingMockProvider  # noqa: PLC0415

    provider = TrackingMockProvider()
    ran = provider.calls

    def _agent(agent_id: str, **over: Any) -> ResolvedAgent:
        return ResolvedAgent(
            id=agent_id,
            role=over.pop("role", "worker"),
            model={"provider": "mock", "name": "mock"},
            prompt={"system": f"You are {agent_id}."},
            skills=(),
            iam=None,
            **over,
        )

    topology = ResolvedTopology(
        id="pipeline",
        raw=None,  # type: ignore[arg-type]
        source_path=None,  # type: ignore[arg-type]
        root=_agent(
            "root",
            role="root",
            children=(_agent("researcher"), _agent("writer", depends_on=("researcher",))),
        ),
    )
    graph = compile_topology(
        topology,
        model_provider=provider,  # type: ignore[arg-type]
        governance=MockGovernanceProvider(),
    )

    await graph.ainvoke(
        {"input": "go", "messages": [], "agent_results": {}, "current_agent": "root", "output": ""}
    )

    assert "researcher" in ran and "writer" in ran


def test_the_runtime_installs_the_checker_for_every_run() -> None:
    """A node asks `stop_requested()`; nothing answers unless the runtime installed a checker, and
    a stop nothing reads is a button that does nothing."""
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/_workspace_runtime.py"
    ).read_text()
    begin = src[src.index("    def _begin_run(") :][:1400]

    assert "set_stop_checker(self._stop_checker(trace.run_id))" in begin
    assert "def _stop_checker(" in src
    assert "store_backed_checker(store, run_id)" in src
