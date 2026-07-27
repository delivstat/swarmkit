"""Recurring expert-persona repo audit — slice 6 of gate-coverage-and-comprehension-debt.

Composition-only: a cron Trigger firing a read-only expert-reviewer panel topology. These tests
assert the artifacts resolve and wire up correctly (schema validity is covered by validate_library).
"""

from __future__ import annotations

from pathlib import Path

from swarmkit_runtime.resolver import resolve_workspace

_WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"

_EXPECTED_LENSES = {
    "security-lens",
    "maintainability-lens",
    "performance-lens",
    "api-consistency-lens",
    "test-coverage-lens",
}


def test_repo_audit_panel_resolves_read_only_reviewers() -> None:
    ws = resolve_workspace(_WORKSPACE)
    panel = ws.topologies["repo-audit-panel"]
    children = panel.root.children
    assert {c.id for c in children} == _EXPECTED_LENSES
    # Every reviewer is a read-only harness (find + cite, never modify).
    for c in children:
        assert c.executor.kind == "harness"
        assert c.source_archetype == "expert-reviewer"
        scopes = list((c.iam or {}).get("base_scope", []))
        assert "app:read" in scopes
        assert not any(s.endswith(":write") or s == "app:write" for s in scopes)


def test_fortnightly_audit_trigger_fires_the_panel() -> None:
    ws = resolve_workspace(_WORKSPACE)
    trigger = next(t for t in ws.triggers if t.id == "fortnightly-audit")
    assert trigger.raw.type.value == "cron"
    assert trigger.targets == ("repo-audit-panel",)
    config = trigger.raw.config.model_dump()
    assert config["expression"] == "0 6 1,15 * *"  # 1st + 15th ≈ every other week
