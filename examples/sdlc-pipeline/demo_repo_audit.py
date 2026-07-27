"""Demo: the recurring expert-persona repo audit (slice 6 of gate-coverage-and-comprehension-debt).

Composition only — a cron Trigger fires a read-only expert-reviewer panel. This prints the wiring
(schedule → panel → the five expert lenses) deterministically; no model calls, no server. On a real
firing each lens investigates the repo read-only and returns cited findings into the audit log.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit_runtime.resolver import resolve_workspace

_WORKSPACE = Path(__file__).parent / "workspace"


def main() -> None:
    ws = resolve_workspace(_WORKSPACE)
    panel = ws.topologies["repo-audit-panel"]
    trigger = next(t for t in ws.triggers if t.id == "fortnightly-audit")
    cfg = trigger.raw.config.model_dump()

    schedule = f"{cfg.get('expression')}  [{cfg.get('timezone', 'local')}]"
    print("Recurring expert-persona repo audit (slice 6)\n")
    print(f"  cron: {schedule}  →  topology '{panel.raw.metadata.name}'")
    print(f"  coordinator: {panel.root.id} (dispatches + collates; does not review)")
    print("  expert lenses (read-only claude-code harness):")
    for c in panel.root.children:
        focus = c.id.removesuffix("-lens").replace("-", " ")
        print(f"    - {c.id:<22} audits for {focus}")
    print("\nOn each firing every lens investigates read-only and returns cited findings to the")
    print("audit log — the every-other-week whole-repo pass that catches accumulated comprehension")
    print(
        "debt no per-change gate sees. The stale-audit signal tracks whether the cadence is kept."
    )
    print("\n✓ repo-audit-panel + fortnightly-audit trigger resolve and wire up.")


if __name__ == "__main__":
    main()
