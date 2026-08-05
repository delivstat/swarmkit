"""`output_schema` may name a file, and a malformed schema fails at load.

Inline-only cost three things: a JSON Schema inline in YAML is hard to read at any real size, it
could not be shared between agents without duplicates that drift, and it was never validated as a
schema until an agent's output was measured against it — so a typo in a `required` list surfaced
mid-run as a conformance failure that read like the agent's fault.

The declaration was already `oneOf: [object, null]`; this is a third branch, not a new concept. One
type-discriminated key rather than a second `output_schema_ref` key, so "both declared" is
unrepresentable rather than resolved: any precedence rule silently ignores the loser, and an author
who then edits the referenced file sees no effect and gets no message.

Both forms normalise to the parsed object, so nothing downstream can tell which was written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from swarmkit_runtime.resolver._output_schema_ref import (
    OutputSchemaError,
    resolve_output_schema,
)

SCHEMA = {"type": "object", "required": ["screens"], "properties": {"screens": {"type": "array"}}}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "swarm").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "design.json").write_text(json.dumps(SCHEMA))
    return tmp_path


def _topology(workspace: Path) -> Path:
    return workspace / "swarm" / "design.yaml"


# ---- the two forms normalise to one ----------------------------------------------------------


def test_a_file_resolves_to_its_parsed_schema(workspace: Path) -> None:
    got = resolve_output_schema(
        "../schemas/design.json", declared_in=_topology(workspace), workspace_root=workspace
    )
    assert got == SCHEMA


def test_inline_and_file_produce_the_same_thing(workspace: Path) -> None:
    """The normalisation property, stated as an equality — this is what lets every consumer stay
    unchanged."""
    from_file = resolve_output_schema(
        "../schemas/design.json", declared_in=_topology(workspace), workspace_root=workspace
    )
    inline = resolve_output_schema(SCHEMA, declared_in=_topology(workspace))
    assert from_file == inline


def test_a_path_resolves_against_the_declaring_artifact(workspace: Path) -> None:
    """Not against the workspace root: a schema usually lives beside the topology that uses it."""
    (workspace / "swarm" / "local.json").write_text(json.dumps(SCHEMA))
    assert (
        resolve_output_schema(
            "local.json", declared_in=_topology(workspace), workspace_root=workspace
        )
        == SCHEMA
    )


def test_yaml_is_accepted(workspace: Path) -> None:
    """Every other artifact here is YAML; a schema author will reasonably reach for it."""
    (workspace / "schemas" / "design.yaml").write_text(
        "type: object\nrequired: [screens]\nproperties:\n  screens:\n    type: array\n"
    )
    got = resolve_output_schema(
        "../schemas/design.yaml", declared_in=_topology(workspace), workspace_root=workspace
    )
    assert got["required"] == ["screens"]


def test_two_agents_can_share_one_file(workspace: Path) -> None:
    """The point of the feature: one shape, one file, no duplicates to drift apart."""
    a = resolve_output_schema(
        "../schemas/design.json", declared_in=_topology(workspace), workspace_root=workspace
    )
    b = resolve_output_schema(
        "../schemas/design.json",
        declared_in=workspace / "swarm" / "other.yaml",
        workspace_root=workspace,
    )
    assert a == b


# ---- failures land at load time --------------------------------------------------------------


def test_a_missing_file_is_an_error_naming_where_it_was_declared(workspace: Path) -> None:
    with pytest.raises(OutputSchemaError, match="not found") as exc:
        resolve_output_schema(
            "../schemas/absent.json", declared_in=_topology(workspace), workspace_root=workspace
        )
    assert "design.yaml" in str(exc.value), "the declaring artifact must be named"


def test_an_unparseable_file_is_an_error(workspace: Path) -> None:
    (workspace / "schemas" / "broken.json").write_text("{not json")
    with pytest.raises(OutputSchemaError, match="does not parse"):
        resolve_output_schema(
            "../schemas/broken.json", declared_in=_topology(workspace), workspace_root=workspace
        )


def test_a_file_that_is_not_an_object_is_an_error(workspace: Path) -> None:
    (workspace / "schemas" / "list.json").write_text("[1, 2, 3]")
    with pytest.raises(OutputSchemaError, match="must contain a JSON Schema object"):
        resolve_output_schema(
            "../schemas/list.json", declared_in=_topology(workspace), workspace_root=workspace
        )


def test_a_file_that_is_not_a_valid_json_schema_is_an_error(workspace: Path) -> None:
    """Parsing is not enough — `required` must be a list of strings, and this catches it now
    rather than when an agent's output is measured against it."""
    (workspace / "schemas" / "bad.json").write_text(json.dumps({"type": "object", "required": 5}))
    with pytest.raises(OutputSchemaError, match="not a valid JSON Schema"):
        resolve_output_schema(
            "../schemas/bad.json", declared_in=_topology(workspace), workspace_root=workspace
        )


def test_an_inline_schema_is_validated_too(workspace: Path) -> None:
    """NEW behaviour, and arguably the larger win. A malformed inline schema used to fail mid-run
    and read like the agent's fault. It now fails the load that declared it."""
    with pytest.raises(OutputSchemaError, match="not a valid JSON Schema"):
        resolve_output_schema({"type": "object", "required": 5}, declared_in=_topology(workspace))


# ---- the guards ------------------------------------------------------------------------------


def test_a_path_escaping_the_workspace_is_refused(workspace: Path, tmp_path: Path) -> None:
    """An artifact must not be able to read outside its workspace — the same rule the docs-reader
    already enforces."""
    outside = tmp_path.parent / "outside.json"
    outside.write_text(json.dumps(SCHEMA))
    with pytest.raises(OutputSchemaError, match="outside the workspace"):
        resolve_output_schema(
            "../../outside.json", declared_in=_topology(workspace), workspace_root=workspace
        )


def test_a_url_is_refused(workspace: Path) -> None:
    """A schema fetched at resolve time would make a workspace's meaning depend on the network, and
    on whatever the other end serves that day."""
    with pytest.raises(OutputSchemaError, match="URL"):
        resolve_output_schema(
            "https://example.com/schema.json",
            declared_in=_topology(workspace),
            workspace_root=workspace,
        )


# ---- through the resolver --------------------------------------------------------------------


def test_the_schema_accepts_all_three_forms() -> None:
    from swarmkit_schema import validate  # noqa: PLC0415

    def topo(output_schema: object) -> dict[str, object]:
        return {
            "apiVersion": "swarmkit/v1",
            "kind": "Topology",
            "metadata": {"name": "minimal", "version": "0.1.0"},
            "agents": {
                "root": {
                    "id": "root",
                    "role": "root",
                    "archetype": "supervisor-root",
                    "output_schema": output_schema,
                }
            },
        }

    validate("topology", topo({"type": "object"}))
    validate("topology", topo("./schemas/design.json"))
    validate("topology", topo(None))


def test_the_schema_still_rejects_a_nonsense_form() -> None:
    from swarmkit_schema import validate  # noqa: PLC0415

    with pytest.raises(Exception, match=r"(?i)valid|schema|match"):
        validate(
            "topology",
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Topology",
                "metadata": {"name": "minimal", "version": "0.1.0"},
                "agents": {
                    "root": {
                        "id": "root",
                        "role": "root",
                        "archetype": "supervisor-root",
                        "output_schema": 42,
                    }
                },
            },
        )
