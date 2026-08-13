"""Run a two-stage pipeline against a live `swarmkit serve`.

    swarmkit serve ./workspace &
    python run_pipeline.py WMS-35

Stages are declared here, in this application's own format — not in a SwarmKit artifact. That is the
point: the sequence is the application's business.
"""

from __future__ import annotations

import os
import sys

from client import ServeClient
from orchestrator import Stage, StageFailed, StageRejected, run_pipeline

PIPELINE = (
    Stage(id="triage", topology="wms-triage"),
    Stage(id="design", topology="wms-design", after=("triage",)),
)


def main(correlation_id: str) -> int:
    base = os.environ.get("SWARMKIT_URL", "http://127.0.0.1:8000")
    with ServeClient(base, token=os.environ.get("SWARMKIT_TOKEN", "")) as client:
        try:
            run = run_pipeline(client, correlation_id, PIPELINE)
        except StageRejected as exc:
            print(f"rejected: {exc}", file=sys.stderr)
            return 2
        except StageFailed as exc:
            print(f"failed: {exc}", file=sys.stderr)
            return 1

    for stage_id, artifact in run.artifacts.items():
        print(f"--- {stage_id} ({run.jobs[stage_id]}) ---")
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "demo-1"))
