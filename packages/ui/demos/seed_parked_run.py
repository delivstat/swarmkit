"""Seed a throwaway workspace with a run parked on a multi-party gate, then serve it.

Backs the slice-3 UI demo (design/details/pipeline-gate-approval-ui.md): the /runs approval panel
and the /gates inbox row need a *parked* saga whose gate has open role-tasks, which nothing else in
the repo produces without running a real pipeline. No model call, no API budget.

    uv run python packages/ui/demos/seed_parked_run.py --serve
    # then, in another shell:
    NEXT_PUBLIC_SWARMKIT_API=http://127.0.0.1:8099 pnpm --filter @swarmkit/ui dev

The workspace path is printed so a screenshot script (or a human) can point at it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from swarmkit_runtime.artifacts import build_artifact_store
from swarmkit_runtime.orchestration import SagaState, SqlSagaStore
from swarmkit_runtime.review import FileReviewQueue, ReviewItem

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"

CORRELATION = "run-42"
STAGE = "greeter"
GATE = f"{CORRELATION}:{STAGE}"

ROLES_YAML = """\
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata:
  id: demo-roles
  name: Demo roles
roles:
  - id: security-reviewer
    members: [alice, anonymous]
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
  description: Both leads sign off before the run advances.
approve:
  rules:
    - scope: greet:approve
      roles: [security-reviewer, release-manager]
      quorum: all
provenance:
  authored_by: human
  version: 1.0.0
"""

STAGE_GRAPH_YAML = """\
apiVersion: swarmkit/v1
kind: StageGraph
metadata:
  id: greet-pipeline
  name: Greet pipeline
  description: A two-stage demo pipeline whose second stage carries a human gate.
stages:
  - id: draft
    topology: hello
    when: [greet.requested]
    success: draft.done
  - id: greeter
    topology: hello
    when: [draft.done]
    gate: greet-funnel
    success: greet.approved
provenance:
  authored_by: human
  version: 1.0.0
"""

ARTIFACT_INPUT = "Draft the EU launch greeting copy."

ARTIFACT = """\
# Greeting package

Proposed customer greeting copy for the EU launch.

- tone: warm, formal
- locales: en-IE, fr-FR, de-DE
- reviewed-for: brand voice, GDPR copy constraints
"""


def _role_task(role: str) -> ReviewItem:
    return ReviewItem(
        id=f"mpa-{GATE}-0-{role}",
        topology_id=CORRELATION,
        agent_id=STAGE,
        skill_id="multi-party-approval",
        output={"gate_id": GATE, "role": role, "scope": "greet:approve", "rule_index": 0},
        verdict={},
        reason=f"role {role!r} must approve 'greet:approve'",
        timestamp=datetime.now(tz=UTC),
    )


def build(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(EXAMPLE_WS, dest)
    # The example ships a .swarmkit dir; a stale saga store there has an older schema.
    shutil.rmtree(dest / ".swarmkit", ignore_errors=True)

    (dest / "roles").mkdir(exist_ok=True)
    (dest / "roles" / "demo-roles.yaml").write_text(ROLES_YAML)
    (dest / "funnels").mkdir(exist_ok=True)
    (dest / "funnels" / "greet-funnel.yaml").write_text(FUNNEL_YAML)
    (dest / "pipelines").mkdir(exist_ok=True)
    (dest / "pipelines" / "greet-pipeline.yaml").write_text(STAGE_GRAPH_YAML)

    topo = next((dest / "topologies").glob("*.yaml"))
    topo.write_text(
        topo.read_text().replace(
            "        archetype: greeter",
            "        archetype: greeter\n        funnel: greet-funnel",
        )
    )

    # The artifacts the inspector shows above the approval panel — the point of approving *here*
    # rather than from a context-free inbox. Written through the real store (default backend is
    # `database`, the same SQLite the saga store rides), not hand-placed files.
    store_dir = dest / ".swarmkit"
    store_dir.mkdir(exist_ok=True)
    db_url = f"sqlite:///{store_dir / 'store.sqlite'}"
    artifacts = build_artifact_store(None, workspace_root=dest, database_url=db_url)
    draft_ref = artifacts.put(CORRELATION, "draft", "Draft copy, first pass.")
    stage_ref = artifacts.put(CORRELATION, STAGE, ARTIFACT)
    artifacts.put(CORRELATION, STAGE, ARTIFACT_INPUT, name="input")

    store = SqlSagaStore.from_url(db_url)
    store.create(CORRELATION, graph_id="greet-pipeline", tag="eu-launch", input=ARTIFACT_INPUT)
    saga = SagaState(
        correlation_id=CORRELATION,
        graph_id="greet-pipeline",
        status="parked",
        passed_stages=["draft"],
        pending_gate_stage=STAGE,
        # The refs the store actually minted — a hand-built ref does not resolve.
        artifacts={"draft": draft_ref, STAGE: stage_ref},
        attempts={"draft": 1, STAGE: 1},
        tag="eu-launch",
        input=ARTIFACT_INPUT,
        created_at=datetime.now(tz=UTC).isoformat(),
        updated_at=datetime.now(tz=UTC).isoformat(),
    )
    saga.add("started", stage_id="draft")
    saga.add("completed", stage_id="draft")
    saga.add("started", stage_id=STAGE)
    saga.add("parked", stage_id=STAGE, detail="awaiting multi-party approval")
    store.save(saga)

    queue = FileReviewQueue(dest)
    queue.submit(_role_task("security-reviewer"))
    queue.submit(_role_task("release-manager"))
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="/tmp/swarmkit-parked-demo")
    ap.add_argument("--serve", action="store_true", help="Run `swarmkit serve` on the workspace.")
    ap.add_argument("--port", type=int, default=8099)
    args = ap.parse_args()

    ws = build(Path(args.dest))
    print(f"workspace: {ws}")
    print(f"run:       {CORRELATION}  parked on {STAGE}  (gate {GATE})")
    print("role-tasks: security-reviewer (alice, anonymous), release-manager (bob)")

    if args.serve:
        print(f"\nserving on http://127.0.0.1:{args.port} — ctrl-c to stop")
        sys.exit(
            subprocess.call(["swarmkit", "serve", str(ws), "--port", str(args.port), "--insecure"])
        )


if __name__ == "__main__":
    main()
