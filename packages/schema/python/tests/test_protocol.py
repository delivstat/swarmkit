"""Validate the fleet-enrollment protocol schemas + round-trip every committed fixture.

The register/join + InstanceState wire contract (design details/control-plane/19-fleet-enrollment-
protocol.md) is published so any client validates against it. Cross-file `$ref`s (responses embed
the credential + instance-state schemas) resolve through the protocol registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from swarmkit_schema import (
    ProtocolSchemaName,
    get_protocol_schema,
    validate_protocol,
)

ALL: tuple[ProtocolSchemaName, ...] = (
    "credential",
    "instance-state",
    "register-request",
    "register-response",
    "join-request",
    "join-response",
)

FIXTURE_ROOT = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "protocol"


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixtures(kind: str) -> list[Path]:
    return sorted((FIXTURE_ROOT / kind).glob("*.json"))


def _valid_cases() -> list[tuple[str, Path]]:
    return [(name, f) for name in ALL for f in _fixtures(name)]


def _invalid_cases() -> list[tuple[str, Path]]:
    return [(name, f) for name in ALL for f in _fixtures(f"{name}-invalid")]


@pytest.mark.parametrize("name", ALL)
def test_schema_is_valid_json_schema(name: ProtocolSchemaName) -> None:
    Draft202012Validator.check_schema(get_protocol_schema(name))


@pytest.mark.parametrize(
    ("name", "fixture"), _valid_cases(), ids=lambda v: v.name if isinstance(v, Path) else str(v)
)
def test_valid_fixtures_pass(name: ProtocolSchemaName, fixture: Path) -> None:
    validate_protocol(name, _load(fixture))


@pytest.mark.parametrize(
    ("name", "fixture"), _invalid_cases(), ids=lambda v: v.name if isinstance(v, Path) else str(v)
)
def test_invalid_fixtures_fail(name: ProtocolSchemaName, fixture: Path) -> None:
    with pytest.raises(ValidationError):
        validate_protocol(name, _load(fixture))


def test_every_message_has_at_least_one_fixture_each_way() -> None:
    # Guard against a schema shipping without coverage.
    for name in ALL:
        assert _fixtures(name), f"no valid fixture for protocol schema '{name}'"
        assert _fixtures(f"{name}-invalid"), f"no invalid fixture for protocol schema '{name}'"


def test_cross_ref_resolves_embedded_instance_state() -> None:
    # A register-response with a structurally-broken embedded instance_state must fail — proving the
    # $ref to instance-state.schema.json actually resolves and is enforced (not skipped).
    resp = _load(FIXTURE_ROOT / "register-response" / "valid.json")
    assert isinstance(resp, dict)
    resp["instance_state"].pop("artifacts")
    with pytest.raises(ValidationError):
        validate_protocol("register-response", resp)
