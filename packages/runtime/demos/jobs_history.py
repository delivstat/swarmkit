"""Demo: what the two jobs endpoints return, and why the page needs both.

The bug: the /jobs page read only `/jobs` — the in-memory JobStore, this process only, gone on
restart. `/jobs/history` existed server-side the whole time and nothing called it, so a restart
erased the visible record of every run and the durable usage/cost was never shown.

    uv run python packages/runtime/demos/jobs_history.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from swarmkit_runtime.persistence._store import Store, make_engine

REPO = Path(__file__).resolve().parents[3]


def _fmt_cost(usd: float | None) -> str:
    if usd is None:
        return "-"
    return "<$0.01" if 0 < usd < 0.01 else f"${usd:.2f}"


def main() -> None:
    ws = Path(tempfile.mkdtemp()) / "ws"
    shutil.copytree(REPO / "examples/hello-swarm/workspace", ws)
    shutil.rmtree(ws / ".swarmkit", ignore_errors=True)
    (ws / ".swarmkit").mkdir()

    store = Store(make_engine(f"sqlite:///{ws / '.swarmkit' / 'store.sqlite'}"))

    # Two finished runs from an earlier serve process, and one still going.
    store.create_job("aa11", "wms-design", "design the RF screens")
    store.update_job(
        "aa11",
        status="completed",
        version="1.0.0",
        completed_at="2026-08-04T10:05:00Z",
        usage_input_tokens=12400,
        usage_output_tokens=3120,
        usage_cost_usd=0.42,
    )
    store.create_job("bb22", "wms-triage", "triage WMS-1")
    store.update_job(
        "bb22", status="failed", completed_at="2026-08-04T10:09:00Z", usage_cost_usd=0.004
    )
    store.create_job("cc33", "wms-design", "design the pick-confirm flow")
    store.update_job("cc33", status="running")

    # `/jobs` — the in-memory store. After a restart it holds only what THIS process started.
    in_memory: list[dict[str, Any]] = [
        {"job_id": "cc33", "topology": "wms-design", "status": "running"}
    ]

    print("\n  BEFORE — the page read only /jobs (in-memory, this process):")
    print("  " + "─" * 74)
    for j in in_memory:
        print(f"    {j['job_id']}  {j['topology']:<12} {j['status']}")
    print("    (a restart empties this; the two finished runs were invisible)\n")

    print("  AFTER — Running now (from /jobs), then History (from /jobs/history):")
    print("  " + "─" * 74)
    print("    Running now")
    for j in in_memory:
        print(f"      {j['job_id']}  {j['topology']:<12} {j['status']}")

    live_ids = {j["job_id"] for j in in_memory}
    print("\n    History                             tokens in/out        cost")
    for r in store.list_jobs(limit=100):
        if r.id in live_ids:
            continue  # written to BOTH stores at creation — do not print it twice
        toks = f"{r.usage_input_tokens or 0:,} / {r.usage_output_tokens or 0:,}"
        cost = _fmt_cost(r.usage_cost_usd)
        print(f"      {r.id}  {r.topology:<12} {r.status:<10} {toks:<18} {cost}")
    print("  " + "─" * 74)
    print(
        "\n  cc33 is running, so it is in BOTH stores — the history table excludes it rather\n"
        "  than showing the same job twice. bb22 cost $0.004, shown as <$0.01 rather than\n"
        "  rounded to $0.00; a job with no recorded cost shows '-', not $0.00.\n"
    )


if __name__ == "__main__":
    main()
