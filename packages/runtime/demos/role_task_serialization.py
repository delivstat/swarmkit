"""Demo: a role-task is renderable, and a gate reports the status the engine decided (slice 2).

design/details/pipeline-gate-approval-ui.md. Two things a front-end could not do before:

  1. **Tell role-tasks apart.** They serialized as ``kind: "other"`` with gate/role/scope dropped,
     so sibling tasks of one gate were indistinguishable. Now they are ``kind: "role_task"`` and
     carry the fields, and ``/review`` filters by kind and by gate.
  2. **Trust the gate's reported status.** ``GET /pipelines/gate-status`` folded the items — it
     took *every* task approving as the bar, which is right only for ``quorum: all``. Under
     ``quorum: any`` the engine approves on the first resolution while the fold still said pending,
     so an orchestrator polling the endpoint waited for a gate that had already opened. The status
     is now evaluated through the same ``evaluate()`` the runtime gates on.

Run it:

    uv run python packages/runtime/demos/role_task_serialization.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from swarmkit_runtime.review import FileReviewQueue, ReviewItem
from swarmkit_runtime.server import create_app

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"

GATE = "run-42:greeter"

ROLES_YAML = """\
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata:
  id: demo-roles
  name: Demo roles
roles:
  - id: security-reviewer
    members: [alice]
    scopes: [greet:approve]
  - id: release-manager
    members: [bob]
    scopes: [greet:approve]
"""

FUNNEL_YAML = """\
apiVersion: swarmkit/v1
kind: Funnel
metadata:
  id: greet-funnel
  name: Greet funnel
  description: A demo gate whose quorum is `any` — one approval is enough.
approve:
  rules:
    - scope: greet:approve
      roles: [security-reviewer, release-manager]
      quorum: any
provenance:
  authored_by: human
  version: 1.0.0
"""


def _role_task(role: str) -> ReviewItem:
    return ReviewItem(
        id=f"mpa-{GATE}-0-{role}",
        topology_id="run-42",
        agent_id="greeter",
        skill_id="multi-party-approval",
        output={"gate_id": GATE, "role": role, "scope": "greet:approve", "rule_index": 0},
        verdict={},
        reason=f"role {role!r} must approve",
        timestamp=datetime.now(tz=UTC),
    )


def _build(tmp: str, *, with_funnel: bool) -> Path:
    ws = Path(tmp) / ("with-funnel" if with_funnel else "bare")
    shutil.copytree(EXAMPLE_WS, ws)
    (ws / "roles").mkdir(exist_ok=True)
    (ws / "roles" / "demo-roles.yaml").write_text(ROLES_YAML)
    if with_funnel:
        (ws / "funnels").mkdir(exist_ok=True)
        (ws / "funnels" / "greet-funnel.yaml").write_text(FUNNEL_YAML)
        topo = next((ws / "topologies").glob("*.yaml"))
        topo.write_text(
            topo.read_text().replace(
                "        archetype: greeter",
                "        archetype: greeter\n        funnel: greet-funnel",
            )
        )
    queue = FileReviewQueue(ws)
    queue.submit(_role_task("security-reviewer"))
    queue.submit(_role_task("release-manager"))
    return ws


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = _build(tmp, with_funnel=True)

        print("\n1. A role-task on the wire — the fourth kind, with the fields a UI needs")
        with TestClient(create_app(ws)) as c:
            item = c.get("/review", params={"kind": "role_task"}).json()[0]
        for k in ("id", "kind", "gate_id", "role", "scope", "rule_index", "status", "resolved_by"):
            print(f"     {k:<12} {json.dumps(item[k])}")

        print("\n2. Filters — by kind, and by gate")
        with TestClient(create_app(ws)) as c:
            by_kind = c.get("/review", params={"kind": "role_task"}).json()
            by_gate = c.get("/review", params={"gate_id": GATE}).json()
            other = c.get("/review", params={"gate_id": "run-99:elsewhere"}).json()
        print(f"     kind=role_task  → {len(by_kind)}  roles={[i['role'] for i in by_kind]}")
        print(f"     gate_id={GATE} → {len(by_gate)}")
        print(f"     gate_id=run-99:elsewhere → {len(other)}")

        print("\n3. One approval lands. The policy is `quorum: any`, so the gate is OPEN.")
        FileReviewQueue(ws).record_resolution(
            f"mpa-{GATE}-0-security-reviewer", "approved", "alice"
        )
        with TestClient(create_app(ws)) as c:
            body = c.get("/pipelines/gate-status/run-42/greeter").json()
        print(
            f"     status           {body['status']}"
            f"   (quorum_evaluated={body['quorum_evaluated']})"
        )
        for i in body["items"]:
            print(f"       {i['role']:<18} {i['status']:<9} by {i['resolved_by'] or '—'}")

        print("\n4. The same queue state, in a workspace where the policy is NOT reachable")
        bare = _build(tmp, with_funnel=False)
        FileReviewQueue(bare).record_resolution(
            f"mpa-{GATE}-0-security-reviewer", "approved", "alice"
        )
        with TestClient(create_app(bare)) as c:
            fallback = c.get("/pipelines/gate-status/run-42/greeter").json()
        print(
            f"     status           {fallback['status']}"
            f"   (quorum_evaluated={fallback['quorum_evaluated']})"
        )
        print("     ^ the fold, which needs EVERY task approved — it says so rather than guessing")
        print(
            f"\n   Same items, two answers: {body['status']!r} vs {fallback['status']!r}."
            " Before this slice, only the second existed.\n"
        )


if __name__ == "__main__":
    main()
