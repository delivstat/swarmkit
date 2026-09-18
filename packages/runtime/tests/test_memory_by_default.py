"""Memory by default (design/details/memory-by-default.md).

A workspace has memory unless it says otherwise: the reader and writer are bound, the governed
memory skills are bundled, and one flag turns the automatic parts off.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.memory._defaults import (
    BUNDLED_SKILL_IDS,
    BUNDLED_SKILLS_DIR,
    apply_memory_defaults,
    memory_config,
)
from swarmkit_runtime.resolver import resolve_workspace

REPO = Path(__file__).resolve().parents[3]

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: m, name: M}
governance: {provider: mock}
"""


def _binding_list(ws: Any) -> list[Any]:
    gov = ws.raw.governance
    assert gov is not None
    return list(gov.decision_skills or [])


def _bindings(ws_dir: Path) -> dict[str, dict[str, object]]:
    return {b.id: b.model_dump() for b in _binding_list(resolve_workspace(ws_dir))}


def test_bundled_skills_are_the_reference_skills() -> None:
    """ "Bundled" and "reference" must never mean two different skills."""
    for skill_id in BUNDLED_SKILL_IDS:
        bundled = (BUNDLED_SKILLS_DIR / f"{skill_id}.yaml").read_text()
        reference = (REPO / "reference" / "skills" / f"{skill_id}.yaml").read_text()
        assert bundled == reference, f"{skill_id}: memory/skills/ differs from reference/skills/"


def test_a_bare_workspace_has_memory(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(_WS)
    ws = resolve_workspace(tmp_path)
    assert set(ws.skills) >= {"governed-memory", "memory-reconcile"}
    b = _bindings(tmp_path)
    assert b["memory-reader"]["trigger"] == "pre_input"
    assert b["memory-reader"]["required"] is False
    assert b["memory-reader"]["config"] == {
        "max_results": 5,
        "similarity_threshold": 0.15,
        "search_scope": "all",
    }
    assert b["memory-writer"]["trigger"] == "post_output"
    assert b["memory-writer"]["config"] == {"min_output_length": 100}
    assert ws.memory == {
        "enabled": True,
        "reader": {"max_results": 5, "similarity_threshold": 0.15, "search_scope": "all"},
        "writer": {"min_output_length": 100},
        "explicit": [],
    }


def test_the_block_tunes_the_auto_bindings(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(
        _WS + "memory:\n  reader: {max_results: 8, search_scope: both}\n"
        "  writer: {min_output_length: 40}\n"
    )
    b = _bindings(tmp_path)
    assert b["memory-reader"]["config"] == {
        "max_results": 8,
        "similarity_threshold": 0.15,
        "search_scope": "both",
    }
    assert b["memory-writer"]["config"] == {"min_output_length": 40}


def test_an_explicit_binding_is_kept_as_written(tmp_path: Path) -> None:
    """The workspace's own memory-reader — its trigger, scope, required and config — is what
    runs; only the writer it did not write is added."""
    (tmp_path / "workspace.yaml").write_text(
        _WS
        + "governance:\n  provider: mock\n  decision_skills:\n"
        + "    - {id: memory-reader, trigger: pre_input, scope: 'analyst', required: true,"
        + " config: {max_results: 2}}\n"
    )
    ws = resolve_workspace(tmp_path)
    b = _bindings(tmp_path)
    assert b["memory-reader"]["scope"] == "analyst"
    assert b["memory-reader"]["required"] is True
    assert b["memory-reader"]["config"] == {"max_results": 2}
    assert b["memory-writer"]["required"] is False
    assert ws.memory["explicit"] == ["memory-reader"]
    assert [x.id for x in _binding_list(ws)].count("memory-reader") == 1


def test_enabled_false_switches_the_automatic_parts_off(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(_WS + "memory: {enabled: false}\n")
    ws = resolve_workspace(tmp_path)
    assert "governed-memory" not in ws.skills
    assert "memory-reconcile" not in ws.skills
    assert _binding_list(ws) == []
    assert ws.memory["enabled"] is False


def test_enabled_false_with_an_explicit_binding_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(
        _WS
        + "governance:\n  provider: mock\n  decision_skills:\n"
        + "    - {id: memory-writer, trigger: post_output, scope: '*'}\n"
        + "memory: {enabled: false}\n"
    )
    with pytest.raises(ResolutionErrors) as exc:
        resolve_workspace(tmp_path)
    assert exc.value.errors[0].code == "memory.disabled-but-bound"
    assert "memory-writer" in exc.value.errors[0].message


def test_a_workspace_copy_of_a_bundled_skill_wins(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "skills").mkdir()
    own = (
        (REPO / "reference" / "skills" / "governed-memory.yaml")
        .read_text()
        .replace("name: Governed Memory", "name: Our Governed Memory")
    )
    (tmp_path / "skills" / "governed-memory.yaml").write_text(own)
    ws = resolve_workspace(tmp_path)
    assert ws.skills["governed-memory"].raw.metadata.name == "Our Governed Memory"
    assert "memory-reconcile" in ws.skills  # the other one is still bundled


def test_apply_is_pure_and_idempotent() -> None:
    raw = {"apiVersion": "swarmkit/v1", "kind": "Workspace", "metadata": {"id": "m", "name": "M"}}
    once = apply_memory_defaults(raw)
    assert "governance" not in raw  # the input is untouched
    assert once["governance"]["provider"] == "mock"  # the implicit allow-all default, made explicit
    twice = apply_memory_defaults(once)
    assert twice is once  # nothing left to add
    assert memory_config(once).explicit == ("memory-reader", "memory-writer")
