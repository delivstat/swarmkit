"""The System page's config report (`_env_registry`).

The point of the page is that invisible config becomes visible. The point of THESE tests is that
making it visible does not make credentials visible with it — the report is served over a
read-scope HTTP endpoint from a process whose environment holds every API key it uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime._env_registry import (
    REGISTRY,
    environment_report,
    workspace_properties,
)


def test_a_secret_value_is_never_in_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-do-not-leak-me")
    rows = {r["name"]: r for r in environment_report()}
    entry = rows["ANTHROPIC_API_KEY"]
    assert entry["set"] is True
    assert entry["value"] == "set"
    assert "do-not-leak-me" not in str(rows)


def test_a_connection_url_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The store URL is the one an operator most wants to SEE — and it carries a password."""
    monkeypatch.setenv("SWARMKIT_STORE_URL", "postgresql://swarm:hunter2@db:5432/swarmkit")
    rows = {r["name"]: r for r in environment_report()}
    value = str(rows["SWARMKIT_STORE_URL"]["value"])
    assert "hunter2" not in value
    assert "db:5432/swarmkit" in value  # everything else stays legible


def test_unset_variables_are_still_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The list of what you COULD set is most of the value when the question is 'why is this
    machine different'."""
    monkeypatch.delenv("SWARMKIT_MAX_TOOL_TURNS", raising=False)
    names = {r["name"] for r in environment_report()}
    assert "SWARMKIT_MAX_TOOL_TURNS" in names
    names_only_set = {r["name"] for r in environment_report(include_unset=False)}
    assert "SWARMKIT_MAX_TOOL_TURNS" not in names_only_set


def test_every_registry_entry_has_a_description() -> None:
    """A name with no explanation is not an infrastructure page, it is a list of strings."""
    for var in REGISTRY:
        assert var.description.strip(), var.name
        assert var.group.strip(), var.name


# ---- workspace properties: data-sourced, so new parameters need no registration ---------------


def test_properties_come_from_the_workspace_file(tmp_path: Path) -> None:
    """A parameter a new feature introduces must appear here without anyone adding code."""
    (tmp_path / "workspace.env.yaml").write_text(
        "some:\n  brand_new_feature_knob: 42\n", encoding="utf-8"
    )
    rows = {r["name"]: r["value"] for r in workspace_properties(tmp_path)}
    assert rows["some.brand_new_feature_knob"] == "42"


def test_a_property_that_looks_like_a_credential_is_masked(tmp_path: Path) -> None:
    """Property names are author-chosen and there is no registry to consult, so the name is the
    only signal available."""
    (tmp_path / "workspace.env.yaml").write_text(
        "openai:\n  api_key: sk-secret-value\nmodel:\n  name: gpt-5\n", encoding="utf-8"
    )
    rows = {r["name"]: r for r in workspace_properties(tmp_path)}
    assert rows["openai.api_key"]["value"] == "set"
    assert rows["openai.api_key"]["sensitive"] is True
    assert rows["model.name"]["value"] == "gpt-5"  # non-secrets stay readable


def test_env_refs_in_properties_are_shown_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Showing '${MY_REGION}' answers nothing — the question is what the run actually used."""
    monkeypatch.setenv("MY_REGION", "eu-west-1")
    (tmp_path / "workspace.env.yaml").write_text("aws:\n  region: ${MY_REGION}\n", encoding="utf-8")
    rows = {r["name"]: r["value"] for r in workspace_properties(tmp_path)}
    assert rows["aws.region"] == "eu-west-1"


def test_no_property_file_is_not_an_error(tmp_path: Path) -> None:
    assert workspace_properties(tmp_path) == []


def test_a_declared_secret_is_masked_whatever_it_is_called(tmp_path: Path) -> None:
    """The reason the declaration exists: `db.dsn` carries a password and no heuristic catches it.
    Guessing wrong here prints a credential to a terminal, a log file and a web page."""
    (tmp_path / "workspace.env.yaml").write_text(
        "secrets:\n  - db.dsn\ndb:\n  dsn: postgresql://u:p@h/db\n  pool: 5\n", encoding="utf-8"
    )
    rows = {r["name"]: r for r in workspace_properties(tmp_path)}
    assert rows["db.dsn"]["value"] == "set"
    assert rows["db.dsn"]["sensitive"] is True
    assert rows["db.pool"]["value"] == "5"


def test_the_secrets_list_is_not_itself_a_property(tmp_path: Path) -> None:
    """It is a declaration ABOUT the properties. Left in, it becomes phantom `secrets.0` rows."""
    (tmp_path / "workspace.env.yaml").write_text(
        "secrets:\n  - db.dsn\ndb:\n  dsn: x\n", encoding="utf-8"
    )
    assert [r["name"] for r in workspace_properties(tmp_path)] == ["db.dsn"]


def test_an_empty_secrets_list_cannot_unmask_an_api_key(tmp_path: Path) -> None:
    """Declaring adds to the masked set; it never removes from it."""
    (tmp_path / "workspace.env.yaml").write_text(
        "secrets: []\nopenai:\n  api_key: sk-live\n", encoding="utf-8"
    )
    rows = {r["name"]: r for r in workspace_properties(tmp_path)}
    assert rows["openai.api_key"]["value"] == "set"
