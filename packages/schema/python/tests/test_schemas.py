"""Validate the canonical schemas and round-trip every committed fixture."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError
from swarmkit_schema import SchemaName, get_schema, validate

ALL: tuple[SchemaName, ...] = ("topology", "skill", "archetype", "workspace", "trigger")

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
