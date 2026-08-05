"""Recording a finished run's usage — the one implementation, for every way a run happens.

A run's usage has two sinks, and both matter:

* one ``run_usage`` row **per model**, which feeds ``/usage`` and ``/usage/{job_id}`` — the only
  source of the per-model cost breakdown;
* job-level totals on the ``jobs`` row, which feed ``/jobs/history`` and the fleet panel.

This lived inside ``server/_jobs.py`` and was therefore reachable only from
``POST /run/{topology}``. As other run paths gained job records — the CLI in 1.150.0, pipeline
stages in 1.152.0, chat in 1.155.0 — each hand-rolled the job-level half and wrote no ``run_usage``
rows at all. So ``/usage`` answered for one path out of four and reported the rest as nothing: a
dashboard reading it showed a workspace that had apparently stopped doing anything.

They also took ``usage.cost_usd`` at face value. Providers that report only tokens leave that at
zero, and the price-table estimate below is what turns those runs into a cost instead of a $0.00
that reads like a free run.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from ._store import UsageRow

logger = logging.getLogger("swarmkit.usage")


def record_run_usage(store: Any, job_id: str, usage: Any) -> float:
    """Write both usage sinks for a finished run. Returns the total cost it recorded.

    Best-effort by design: a bookkeeping failure must never fail an otherwise-successful run. The
    caller gets 0.0 back if nothing could be written, and the run stands either way.
    """
    if store is None or usage is None:
        return 0.0

    from swarmkit_runtime.model_providers._pricing import estimate_cost  # noqa: PLC0415

    total_cost = 0.0
    by_model: dict[str, dict[str, Any]] = getattr(usage, "by_model", None) or {}
    with contextlib.suppress(Exception):
        for model, tok in by_model.items():
            # Provider-reported cost is authoritative; when it is absent — token-only providers —
            # derive it from the price table rather than recording a run as free.
            cost = float(tok.get("cost", 0.0))
            if cost == 0.0:
                cost = estimate_cost(model, int(tok.get("input", 0)), int(tok.get("output", 0)))
            total_cost += cost
            store.record_usage(
                UsageRow(
                    agent_id="",
                    model=model,
                    input_tokens=int(tok.get("input", 0)),
                    output_tokens=int(tok.get("output", 0)),
                    cost_usd=cost,
                    job_id=job_id,
                )
            )

    # A run with no per-model breakdown still has totals worth keeping — an older result shape, or
    # a provider that reports only aggregates. Fall back rather than record the run as costless.
    if total_cost == 0.0:
        total_cost = float(getattr(usage, "cost_usd", 0.0) or 0.0)

    return total_cost


def usage_fields(usage: Any, job_id: str = "", store: Any = None) -> dict[str, Any]:
    """The job-row usage columns for a finished run, recording the per-model rows on the way.

    One call site per recorder, so a new run path cannot pick up the totals and silently skip the
    breakdown — which is exactly how ``/usage`` came to answer for one path in four.
    """
    if usage is None:
        return {}
    cost = record_run_usage(store, job_id, usage) if store is not None else 0.0
    if cost == 0.0:
        cost = float(getattr(usage, "cost_usd", 0.0) or 0.0)
    return {
        "usage_input_tokens": getattr(usage, "input_tokens", 0),
        "usage_output_tokens": getattr(usage, "output_tokens", 0),
        "usage_cost_usd": cost,
    }
