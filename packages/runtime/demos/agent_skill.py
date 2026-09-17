"""Demo: another agent as a skill — local child run, then a remote agent over A2A.

Two workspaces, both hello-swarm under the mock provider. The *caller* has two `agent` skills:
`ask-hello` names its own `hello` topology (runs in-process as a child of the caller's run), and
`ask-remote` names the *remote* workspace's Agent Card (served by a second `swarmkit serve` app,
reached here through an ASGI transport instead of a network). Both are called the way the model
would call them; what comes back is the tool result the model would read.

    uv run python packages/runtime/demos/agent_skill.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[3]

_SKILL = """apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: {id}
  name: {id}
  description: Calls another agent.
category: capability
implementation:
  type: agent
{impl}
provenance:
  authored_by: human
  authored_date: "2026-09-17"
  version: 1.0.0
"""

_CALLER = """apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: caller
  version: 0.1.0
agents:
  root:
    id: root
    role: root
    model: {provider: anthropic, name: claude-opus-4-7}
    prompt: {system: You delegate to other agents.}
    skills: [ask-hello, ask-remote]
"""


def _copy(name: str, extra_manifest: str = "") -> Path:
    ws = Path(tempfile.mkdtemp()) / name
    shutil.copytree(
        REPO / "examples/hello-swarm/workspace", ws, ignore=shutil.ignore_patterns(".swarmkit")
    )
    if extra_manifest:
        m = ws / "workspace.yaml"
        m.write_text(m.read_text() + extra_manifest)
    return ws


async def main() -> None:
    os.environ.setdefault("SWARMKIT_PROVIDER", "mock")
    logging.disable(logging.INFO)

    from swarmkit_runtime._run_scope import set_current_run_id  # noqa: PLC0415
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415
    from swarmkit_runtime.agent_skill import AgentSkillContext, set_agent_context  # noqa: PLC0415
    from swarmkit_runtime.langgraph_compiler._skill_executor import execute_skill  # noqa: PLC0415
    from swarmkit_runtime.model_providers import MockModelProvider  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    remote_ws = _copy(
        "remote",
        "\nserver:\n  a2a:\n    enabled: true\n    identity:\n      name: Remote hello desk\n",
    )
    remote_app = create_app(remote_ws)

    caller_ws = _copy("caller")
    (caller_ws / "skills/ask-hello.yaml").write_text(
        _SKILL.format(id="ask-hello", impl="  topology: hello\n  effects: read")
    )
    (caller_ws / "skills/ask-remote.yaml").write_text(
        _SKILL.format(
            id="ask-remote",
            impl="  card_url: http://remote/.well-known/agent-card.json\n  skill_id: hello\n"
            "  on_unanswerable: relay\n  timeout_s: 30",
        )
    )
    (caller_ws / "topologies/caller.yaml").write_text(_CALLER)
    rt = WorkspaceRuntime.from_workspace_path(caller_ws)
    print(
        "caller workspace loaded; agent skills:",
        [
            s
            for s, sk in rt.workspace.skills.items()
            if getattr(sk.raw.implementation, "type", None) is not None
            and str(getattr(sk.raw.implementation, "type", "")).endswith("agent")
        ],
    )

    async def call(skill_id: str, payload: dict[str, Any], **ctx: Any) -> str:
        skill = rt.workspace.skills[skill_id]
        set_current_run_id("demo-parent-run")
        base = rt._agent_context("caller")
        set_agent_context(AgentSkillContext(**{**base.__dict__, **ctx}))
        result = await execute_skill(
            skill,
            input_text=json.dumps(payload),
            model_provider=MockModelProvider(),
            model_name="mock",
            governance=rt._governance,
            agent_id="root",
        )
        return result if isinstance(result, str) else result[0]

    print("\n== ask-hello (local: topology `hello` as a child run) ==")
    out = await call("ask-hello", {"input": "Greet engineers", "context": {"team": "core"}})
    print("tool result:", out)
    child = next(j for j in rt.store.list_jobs(limit=10) if j.topology == "hello")
    print(
        f"child job {child.id}: status={child.status} source={child.source} "
        f"parent_job_id={child.parent_job_id}"
    )

    print("\n== ask-remote (remote: the other instance's card, A2A task API) ==")
    async with remote_app.router.lifespan_context(remote_app):
        transport = httpx.ASGITransport(app=remote_app)
        card = (
            await httpx.AsyncClient(transport=transport).get(
                "http://remote/.well-known/agent-card.json"
            )
        ).json()
        print("remote card:", card["name"], "skills:", [s["id"] for s in card["skills"]])
        out = await call("ask-remote", {"input": "Greet engineers"}, transport=transport)
        print("tool result:", out)
        row = remote_app.state.store.list_jobs(limit=1)[0]
        print(f"remote job {row.id}: source={row.source} correlation_id={row.correlation_id}")

    print("\n== a target that does not exist fails the workspace load, not a run ==")
    bad_ws = _copy("bad")
    (bad_ws / "skills/ask-nowhere.yaml").write_text(
        _SKILL.format(id="ask-nowhere", impl="  topology: no-such-topology")
    )
    try:
        WorkspaceRuntime.from_workspace_path(bad_ws)
    except Exception as exc:
        print(type(exc).__name__ + ":", exc)


if __name__ == "__main__":
    asyncio.run(main())
