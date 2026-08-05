#!/usr/bin/env python
"""Demo: the audit API returns what an event was for, not just its header.

    uv run python packages/runtime/demos/audit_detail.py

Prints one event as the API used to serialize it and as it does now. The store has held every one
of the added fields since M6 — the API returned nine keys and dropped the rest, so the UI's audit
table rendered a list of event types with no content.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from swarmkit_runtime.audit._tables import audit_events
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.server._routes_introspection import _audit_event_to_dict

#: The nine keys the endpoint used to return.
HEADER_ONLY = {
    "event_id",
    "event_type",
    "agent_id",
    "agent_role",
    "timestamp",
    "topology_id",
    "skill_id",
    "run_id",
    "payload",
}

EVENT = AuditEvent(
    event_type="skill.executed",
    agent_id="triage",
    agent_role="worker",
    timestamp=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
    topology_id="wms-triage",
    skill_id="search-wms-tables",
    skill_category="capability",
    run_id="WMS-5:design",
    inputs={"query": "PGM hold", "limit": 40},
    outputs={"result": "3 tables: pgm_hold, pgm_hold_type, order_hold"},
    policy_decision="allow",
    policy_reason="granted by role wms-analyst",
    model_provider="anthropic",
    model_name="claude-opus-5",
    tokens_in=1200,
    tokens_out=340,
    cost_usd=0.0042,
    duration_ms=1250,
)


def main() -> None:
    full = _audit_event_to_dict(EVENT)
    before = {k: v for k, v in full.items() if k in HEADER_ONLY}

    print("what GET /audit returned before:\n")
    print(json.dumps(before, indent=2, default=str))

    print("\nwhat it returns now — the added keys:\n")
    added = {k: v for k, v in full.items() if k not in HEADER_ONLY}
    print(json.dumps(added, indent=2, default=str))

    persisted = {c.name for c in audit_events.columns}
    print(f"\ncolumns the store persists: {len(persisted)}")
    print(f"keys the API returned before: {len(before)}")
    print(f"keys the API returns now:     {len(full)}")
    print(f"persisted but still missing:  {sorted(persisted - set(full)) or 'none'}")


if __name__ == "__main__":
    main()
