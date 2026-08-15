"""Demo: stopping a run, and resuming it (design/details/stopping-a-run.md).

`swarmkit stop <run-id>` was a stub for months. This shows what it does now — deterministic, no API
keys, no server: a three-agent pipeline is stopped from "another terminal" mid-flight, keeps
everything it had already done, and the run is resumable.

Run it:

    uv run python packages/runtime/demos/stop_a_run.py
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from typing import Any

from swarmkit_runtime._stop_requests import (
    reset_stop_checker,
    set_stop_checker,
    store_backed_checker,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers import CompletionResponse, ContentBlock, Usage
from swarmkit_runtime.persistence._store import Store, make_engine
from swarmkit_runtime.resolver import ResolvedAgent, ResolvedTopology
from swarmkit_runtime.review._hitl import RunStoppedError
from swarmkit_runtime.stop import request_stop

RUN = "demo-run"


class _Provider:
    """Delegates from the root, then answers as each worker. The operator's `swarmkit stop` lands
    while the researcher is mid-call — the realistic timing."""

    def __init__(self, store: Store) -> None:
        self.calls: list[str] = []
        self.worked: list[str] = []
        self.asked = False
        self._store = store

    async def complete(self, request: Any) -> CompletionResponse:
        system = request.system or ""
        match = re.search(r"You are (\S+)\.", system)
        who = match.group(1) if match else "root"
        messages = list(request.messages)
        last = str(messages[-1].content) if messages else ""
        delegating = request.tools and not (
            "workers have produced" in last or "Workers already completed" in last
        )
        if delegating:
            tools = [t.name for t in request.tools if t.name.startswith("delegate_to_")]
            if tools:
                self.calls.append(who)
                return CompletionResponse(
                    content=[
                        ContentBlock(
                            type="tool_use",
                            tool_name=tools[0],
                            tool_use_id="call_0",
                            tool_input={"task": "do the work"},
                        )
                    ],
                    stop_reason="tool_use",
                    usage=Usage(),
                )
        self.calls.append(who)
        if who not in self.worked:
            self.worked.append(who)
            print(f"   [{who}] worked")
        if who == "researcher" and not self.asked:
            self.asked = True
            print("\n   >>> meanwhile, in another terminal: swarmkit stop demo-run")
            outcome = request_stop(self._store, RUN)
            assert outcome is not None
            print(f"   >>> stop requested for {RUN} — it will stop at the next agent boundary\n")
        return CompletionResponse(
            content=[ContentBlock(type="text", text=f"output from {who}")],
            stop_reason="end_turn",
            usage=Usage(),
        )


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


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    store = Store(make_engine(f"sqlite:///{tmp / 'jobs.sqlite'}"))
    store.create_job(RUN, "pipeline", "go")
    store.update_job(RUN, status="running")

    provider = _Provider(store)
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

    print("=" * 78)
    print("A three-agent run: researcher -> writer -> editor")
    print("=" * 78)

    token = set_stop_checker(store_backed_checker(store, RUN))
    try:
        await graph.ainvoke(
            {
                "input": "go",
                "messages": [],
                "agent_results": {},
                "current_agent": "root",
                "output": "",
            }
        )
    except RunStoppedError as exc:
        print(f"⏹ stopped before {exc.agent_id!r} ran")
    finally:
        reset_stop_checker(token)

    print(f"\n   agents that ran:     {provider.worked}")
    print("   agents that did not: ['writer', 'editor'] — nothing past the stop was spent")

    print("\n" + "=" * 78)
    print("What the record says")
    print("=" * 78)
    store.update_job(RUN, status="stopped", error="stopped by request")
    row = store.get_job(RUN)
    assert row is not None
    print(f"   status:             {row.status}   (not 'failed' — nothing went wrong;")
    print("                                    not 'deferred' — it waits on nothing)")
    print(f"   stop_requested_at:  {row.stop_requested_at}")

    print("\n" + "=" * 78)
    print("Resuming clears the request")
    print("=" * 78)
    store.update_job(RUN, clear_stop_request=True)
    row = store.get_job(RUN)
    assert row is not None
    print(f"   stop_requested_at:  {row.stop_requested_at}")
    print("   without this the run would resume and immediately stop again on the stale flag —")
    print("   which reads as a resume that does not work.")
    print("\n   swarmkit run <workspace> pipeline --resume demo-run")
    print("\ndone.")


if __name__ == "__main__":
    asyncio.run(main())
