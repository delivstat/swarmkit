"""Tests for the Knowledge MCP Server (M5, task #25).

Tests run against the real repo files — no mocks. The corpus is
committed, so the tests verify real search results.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.knowledge._server import (
    _set_repo_root,
    get_design_note,
    get_error_reference,
    get_schema,
    list_design_notes,
    list_reference_skills,
    list_schemas,
    search_docs,
    validate_workspace,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
BROKEN_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace-broken"


@pytest.fixture(autouse=True)
def _set_root() -> None:
    _set_repo_root(REPO_ROOT)


# ---- search_docs --------------------------------------------------------


def test_search_governance_returns_results() -> None:
    results = search_docs("governance provider")
    assert len(results) > 0
    assert any(
        "governance" in r["snippet"].lower() or "governance" in r["heading"].lower()
        for r in results
    )


def test_search_empty_query_returns_empty() -> None:
    assert search_docs("") == []
    assert search_docs("   ") == []


def test_search_max_results_respected() -> None:
    results = search_docs("skill", max_results=2)
    assert len(results) <= 2


# ---- get_schema ---------------------------------------------------------


def test_get_schema_returns_valid_json_schema() -> None:
    schema = get_schema("skill")
    assert "$schema" in schema
    assert schema["title"] == "SwarmKit Skill"


def test_get_schema_unknown_type_returns_error() -> None:
    result = get_schema("nonexistent")
    assert "error" in result


# ---- list_schemas -------------------------------------------------------


def test_list_schemas_returns_five_types() -> None:
    schemas = list_schemas()
    assert set(schemas) >= {"topology", "skill", "archetype", "workspace", "trigger"}


# ---- get_design_note / list_design_notes --------------------------------


def test_get_design_note_returns_content() -> None:
    note = get_design_note("mcp-client")
    assert "content" in note
    assert "frontmatter" in note
    assert "MCP" in note["content"]


def test_get_design_note_unknown_slug_returns_error() -> None:
    result = get_design_note("nonexistent-slug")
    assert "error" in result


def test_list_design_notes_returns_entries() -> None:
    notes = list_design_notes()
    assert len(notes) > 5
    slugs = {n["slug"] for n in notes}
    assert "mcp-client" in slugs
    assert "governance-provider-interface" in slugs


def test_list_design_notes_filters_by_tag() -> None:
    all_notes = list_design_notes()
    mcp_notes = list_design_notes(tag="mcp")
    assert len(mcp_notes) < len(all_notes)
    assert all("mcp" in n["tags"] for n in mcp_notes)


# ---- list_reference_skills ----------------------------------------------


def test_list_reference_skills_returns_github_skills() -> None:
    skills = list_reference_skills()
    ids = {s["id"] for s in skills}
    assert "github-repo-read" in ids
    assert "github-pr-read" in ids


def test_reference_skills_have_server_and_tool() -> None:
    skills = list_reference_skills()
    for skill in skills:
        if skill["id"].startswith("github-"):
            assert skill["server"] == "github"
            assert skill["tool"] != ""


# ---- validate_workspace ------------------------------------------------


def test_validate_valid_workspace() -> None:
    result = validate_workspace(str(EXAMPLE_WS))
    assert result["valid"] is True
    assert "hello" in result["topologies"]


def test_validate_broken_workspace() -> None:
    result = validate_workspace(str(BROKEN_WS))
    assert result["valid"] is False
    assert len(result["errors"]) > 0


def test_validate_nonexistent_path() -> None:
    result = validate_workspace("/tmp/nonexistent-workspace-xyz")
    assert "error" in result


# ---- get_error_reference ------------------------------------------------


def test_get_error_reference_known_code() -> None:
    result = get_error_reference("agent.unknown-archetype")
    assert result["code"] == "agent.unknown-archetype"


def test_get_error_reference_unknown_code() -> None:
    result = get_error_reference("nonexistent.error.code")
    assert "not found" in result["description"]
