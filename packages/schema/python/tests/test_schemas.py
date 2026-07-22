"""Validate the canonical schemas and round-trip every committed fixture."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError
from swarmkit_schema import SchemaName, get_schema, validate

ALL: tuple[SchemaName, ...] = (
    "topology",
    "skill",
    "archetype",
    "workspace",
    "trigger",
    "executor-adapter",
    "role-registry",
    "approval-policy",
    "funnel",
    "stage-graph",
    "contract",
)

FIXTURE_ROOT = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _fixtures(kind: str) -> list[Path]:
    return sorted((FIXTURE_ROOT / kind).glob("*.yaml"))


@pytest.mark.parametrize("name", ALL)
def test_schema_is_valid_json_schema(name: SchemaName) -> None:
    Draft202012Validator.check_schema(get_schema(name))


@pytest.mark.parametrize("fixture", _fixtures("topology"), ids=lambda p: p.name)
def test_topology_valid_fixtures(fixture: Path) -> None:
    validate("topology", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("topology-invalid"), ids=lambda p: p.name)
def test_topology_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("topology", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("skill"), ids=lambda p: p.name)
def test_skill_valid_fixtures(fixture: Path) -> None:
    validate("skill", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("skill-invalid"), ids=lambda p: p.name)
def test_skill_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("skill", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("archetype"), ids=lambda p: p.name)
def test_archetype_valid_fixtures(fixture: Path) -> None:
    validate("archetype", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("archetype-invalid"), ids=lambda p: p.name)
def test_archetype_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("archetype", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("workspace"), ids=lambda p: p.name)
def test_workspace_valid_fixtures(fixture: Path) -> None:
    validate("workspace", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("workspace-invalid"), ids=lambda p: p.name)
def test_workspace_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("workspace", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("trigger"), ids=lambda p: p.name)
def test_trigger_valid_fixtures(fixture: Path) -> None:
    validate("trigger", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("trigger-invalid"), ids=lambda p: p.name)
def test_trigger_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("trigger", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("role-registry"), ids=lambda p: p.name)
def test_role_registry_valid_fixtures(fixture: Path) -> None:
    validate("role-registry", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("role-registry-invalid"), ids=lambda p: p.name)
def test_role_registry_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("role-registry", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("executor-adapter"), ids=lambda p: p.name)
def test_executor_adapter_valid_fixtures(fixture: Path) -> None:
    validate("executor-adapter", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("executor-adapter-invalid"), ids=lambda p: p.name)
def test_executor_adapter_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("executor-adapter", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("approval-policy"), ids=lambda p: p.name)
def test_approval_policy_valid_fixtures(fixture: Path) -> None:
    validate("approval-policy", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("approval-policy-invalid"), ids=lambda p: p.name)
def test_approval_policy_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("approval-policy", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("funnel"), ids=lambda p: p.name)
def test_funnel_valid_fixtures(fixture: Path) -> None:
    validate("funnel", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("funnel-invalid"), ids=lambda p: p.name)
def test_funnel_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("funnel", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("stage-graph"), ids=lambda p: p.name)
def test_stage_graph_valid_fixtures(fixture: Path) -> None:
    validate("stage-graph", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("stage-graph-invalid"), ids=lambda p: p.name)
def test_stage_graph_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("stage-graph", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("contract"), ids=lambda p: p.name)
def test_contract_valid_fixtures(fixture: Path) -> None:
    validate("contract", _load_yaml(fixture))


@pytest.mark.parametrize("fixture", _fixtures("contract-invalid"), ids=lambda p: p.name)
def test_contract_invalid_fixtures_fail(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate("contract", _load_yaml(fixture))


def test_workspace_reference_fields_carry_x_swarmkit_ref() -> None:
    """Reference fields (agent→archetype, agent→skills, archetype→skills) are annotated with
    x-swarmkit-ref so a UI can render them as workspace-populated pickers instead of free text.
    A non-validating hint (ignored by validators + codegen); pinned so it isn't dropped."""
    topo = get_schema("topology")
    agent = topo["$defs"]["agent"]["properties"]
    assert agent["archetype"]["x-swarmkit-ref"] == "archetype"
    assert agent["skills"]["x-swarmkit-ref"] == "skill"
    assert agent["skills_additional"]["x-swarmkit-ref"] == "skill"

    archetype = get_schema("archetype")
    assert archetype["$defs"]["defaults"]["properties"]["skills"]["x-swarmkit-ref"] == "skill"


def test_archetype_has_optional_executor_block() -> None:
    """The executor block (executor-abstraction §4) is present + optional (backward-compatible):
    `kind` required within it, but the block itself is not in the archetype's required list."""
    schema = get_schema("archetype")
    executor = schema["$defs"]["executor"]
    assert "kind" in executor["properties"]
    assert executor["required"] == ["kind"]
    assert "executor" not in schema["required"]  # optional → existing archetypes unaffected
