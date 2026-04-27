"""Tests for the generated pydantic models.

Three layers:
  1. Every root model imports cleanly.
  2. Every valid fixture loads through its root model (shape validation).
  3. `validate()` catches more than pydantic — specifically the allOf/if-then
     rules pydantic does not translate. This is the known, documented split
     (see design/details/pydantic-codegen.md). Tests assert both directions
     so regressions are caught either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from swael_schema import SchemaName, validate
from swael_schema.models import (
    SwarmKitArchetype,
    SwarmKitSkill,
    SwarmKitTopology,
    SwarmKitTrigger,
    SwarmKitWorkspace,
)

MODELS: dict[SchemaName, type[BaseModel]] = {
    "topology": SwarmKitTopology,
    "skill": SwarmKitSkill,
    "archetype": SwarmKitArchetype,
    "workspace": SwarmKitWorkspace,
    "trigger": SwarmKitTrigger,
}

FIXTURE_ROOT = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"


def _load(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_valid_fixtures() -> list[tuple[SchemaName, Path]]:
    pairs: list[tuple[SchemaName, Path]] = []
    for kind in MODELS:
        for fixture in sorted((FIXTURE_ROOT / kind).glob("*.yaml")):
            pairs.append((kind, fixture))
    return pairs


# Fixtures whose invalidity comes from an allOf / if-then rule — these
# *should* be accepted by the pydantic model (shape-only) and rejected by
# validate() (full jsonschema). Keeping them enumerated keeps the gap
# visible.
SHAPE_ONLY_INVALID: set[tuple[str, str]] = {
    ("skill-invalid", "decision-missing-reasoning.yaml"),
    ("skill-invalid", "decision-reasoning-wrong-type.yaml"),
    ("workspace-invalid", "credential-plugin-missing-provider-id.yaml"),
    ("workspace-invalid", "mcp-http-missing-endpoint.yaml"),
    ("workspace-invalid", "mcp-stdio-missing-command.yaml"),
    ("trigger-invalid", "cron-missing-config.yaml"),
    ("trigger-invalid", "plugin-missing-provider-id.yaml"),
}


def test_all_five_root_models_import() -> None:
    for model in MODELS.values():
        assert issubclass(model, BaseModel)


@pytest.mark.parametrize(
    ("kind", "fixture"),
    _all_valid_fixtures(),
    ids=lambda v: v.name if isinstance(v, Path) else str(v),
)
def test_valid_fixture_loads_into_pydantic_model(kind: SchemaName, fixture: Path) -> None:
    model = MODELS[kind]
    model.model_validate(_load(fixture))


@pytest.mark.parametrize(
    ("kind", "fixture"),
    [
        (k, p)
        for k, model in MODELS.items()
        for p in sorted((FIXTURE_ROOT / f"{k}-invalid").glob("*.yaml"))
    ],
    ids=lambda v: v.name if isinstance(v, Path) else str(v),
)
def test_invalid_fixture_either_rejected_or_shape_only(kind: SchemaName, fixture: Path) -> None:
    """For every invalid fixture, document which layer catches it.

    Pydantic must reject structural errors.
    Everything pydantic accepts must be on the enumerated allOf/if-then list
    (so if a new invalid fixture is added, this test forces us to either:
    (a) see pydantic reject it, or (b) add it to SHAPE_ONLY_INVALID with a
    clear understanding of why).
    """
    invalid_kind = f"{kind}-invalid"
    model = MODELS[kind]
    data = _load(fixture)
    try:
        model.model_validate(data)
        pydantic_accepted = True
    except PydanticValidationError:
        pydantic_accepted = False

    if pydantic_accepted:
        assert (invalid_kind, fixture.name) in SHAPE_ONLY_INVALID, (
            f"{invalid_kind}/{fixture.name} was accepted by pydantic but is not "
            "on the documented allOf/if-then list. Either pydantic should reject it "
            "(fix the codegen) or add this fixture to SHAPE_ONLY_INVALID with "
            "justification."
        )
    # Regardless of pydantic, the jsonschema validator must reject every
    # *-invalid fixture — that's the authoritative layer.
    with pytest.raises(JsonSchemaValidationError):
        validate(kind, data)


@pytest.mark.parametrize(
    ("kind", "fixture"),
    _all_valid_fixtures(),
    ids=lambda v: v.name if isinstance(v, Path) else str(v),
)
def test_model_round_trip_preserves_validity(kind: SchemaName, fixture: Path) -> None:
    """model_validate(data).model_dump(mode='json') still validates via jsonschema."""
    model = MODELS[kind]
    data = _load(fixture)
    obj = model.model_validate(data)
    dumped = obj.model_dump(mode="json", exclude_none=True, by_alias=True)
    validate(kind, dumped)
