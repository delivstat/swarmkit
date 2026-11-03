"""Eval sets are validated against ``eval-set.schema.json`` (design/details/eval-set-schema-v1.md).

The schema is what the CLI, serve and the portal all accept; the runtime model narrows it to plain
types. These tests keep the two from drifting apart, and pin the one rule only the runtime checks:
case ids are unique within a set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.eval import EvalSetInvalidError, load_eval_set
from swarmkit_runtime.eval._models import EvalCase, EvalSet, EvalSetMeta, ExpectSpec
from swarmkit_schema import get_schema

_REPO = Path(__file__).resolve().parents[3]
_FIXTURES = _REPO / "packages" / "schema" / "tests" / "fixtures"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "set.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "fixture", sorted((_FIXTURES / "eval-set").glob("*.yaml")), ids=lambda p: p.name
)
def test_every_valid_schema_fixture_loads(fixture: Path) -> None:
    assert load_eval_set(fixture.parent, str(fixture)).cases


@pytest.mark.parametrize(
    "fixture", sorted((_FIXTURES / "eval-set-invalid").glob("*.yaml")), ids=lambda p: p.name
)
def test_every_invalid_schema_fixture_is_refused(fixture: Path) -> None:
    with pytest.raises(EvalSetInvalidError):
        load_eval_set(fixture.parent, str(fixture))


@pytest.mark.parametrize(
    "path",
    sorted(_REPO.glob("examples/*/workspace/evals/*.yaml")),
    ids=lambda p: p.parent.parent.parent.name,
)
def test_the_shipped_example_eval_sets_load(path: Path) -> None:
    load_eval_set(path.parent.parent, str(path))


def test_a_schema_error_names_the_field(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "apiVersion: swarmkit/v1\nkind: EvalSet\nmetadata: {id: greeting}\ntarget: hello\n"
        "cases:\n  - id: a\n    input: hi\n    expect: {contain: [hello]}\n",
    )
    with pytest.raises(EvalSetInvalidError) as err:
        load_eval_set(tmp_path, str(path))
    assert "/cases/0/expect" in str(err.value)
    assert "contain" in str(err.value)


def test_a_duplicate_case_id_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "apiVersion: swarmkit/v1\nkind: EvalSet\nmetadata: {id: greeting}\ntarget: hello\n"
        "cases:\n  - {id: a, input: hi}\n  - {id: b, input: hello}\n  - {id: a, input: hey}\n",
    )
    with pytest.raises(EvalSetInvalidError, match="'a' is used twice"):
        load_eval_set(tmp_path, str(path))


def test_name_and_provenance_survive_the_load(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "apiVersion: swarmkit/v1\nkind: EvalSet\nmetadata: {id: greeting, name: Greeting}\n"
        "target: hello@1.2.0\ncases:\n  - {id: a, input: hi}\n"
        "provenance: {authored_by: human, version: 1.0.0}\n",
    )
    es = load_eval_set(tmp_path, str(path))
    assert es.metadata.name == "Greeting"
    assert es.target == "hello@1.2.0"
    assert es.provenance == {"authored_by": "human", "version": "1.0.0"}


@pytest.mark.parametrize(
    ("model", "schema_path"),
    [
        (EvalSet, ()),
        (EvalSetMeta, ("$defs", "metadata")),
        (EvalCase, ("$defs", "case")),
        (ExpectSpec, ("$defs", "expect")),
    ],
    ids=["set", "metadata", "case", "expect"],
)
def test_the_runtime_model_mirrors_the_schema(model: type, schema_path: tuple[str, ...]) -> None:
    """A field added to one and not the other is either refused by the schema or dropped by the
    model — both silent. Comparing the two field sets makes it a test failure instead."""
    node = get_schema("eval-set")
    for key in schema_path:
        node = node[key]
    assert set(model.model_fields) == set(node["properties"])  # type: ignore[attr-defined]
