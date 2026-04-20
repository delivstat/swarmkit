"""Validate that all five canonical schemas load and are valid JSON Schema."""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from swarmkit_schema import SchemaName, get_schema, validate


ALL: tuple[SchemaName, ...] = ("topology", "skill", "archetype", "workspace", "trigger")


@pytest.mark.parametrize("name", ALL)
def test_schema_is_valid_json_schema(name: SchemaName) -> None:
    schema = get_schema(name)
    Draft202012Validator.check_schema(schema)


def test_topology_minimal_example() -> None:
    validate(
        "topology",
        {
            "apiVersion": "swarmkit/v1",
            "kind": "Topology",
            "metadata": {"name": "hello-swarm", "version": "0.1.0"},
            "agents": {
                "root": {"id": "root", "role": "root"},
            },
        },
    )
