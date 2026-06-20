"""Eval-set loader tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.eval import EvalSetInvalidError, EvalSetNotFoundError, load_eval_set

_VALID = """\
apiVersion: swarmkit/v1
kind: EvalSet
metadata:
  id: greet-evals
target: hello
cases:
  - id: c1
    input: hi
    expect:
      not_empty: true
"""


def _ws(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "evals"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_load_by_file_stem(tmp_path: Path) -> None:
    ws = _ws(tmp_path, "greetset.yaml", _VALID)
    es = load_eval_set(ws, "greetset")
    assert es.metadata.id == "greet-evals" and es.target == "hello" and len(es.cases) == 1


def test_load_by_metadata_id(tmp_path: Path) -> None:
    ws = _ws(tmp_path, "anything.yaml", _VALID)
    es = load_eval_set(ws, "greet-evals")
    assert es.target == "hello"


def test_load_by_path(tmp_path: Path) -> None:
    ws = _ws(tmp_path, "greetset.yaml", _VALID)
    es = load_eval_set(ws, str(ws / "evals" / "greetset.yaml"))
    assert es.metadata.id == "greet-evals"


def test_not_found(tmp_path: Path) -> None:
    _ws(tmp_path, "greetset.yaml", _VALID)
    with pytest.raises(EvalSetNotFoundError):
        load_eval_set(tmp_path, "nope")


def test_invalid_schema(tmp_path: Path) -> None:
    ws = _ws(tmp_path, "bad.yaml", "apiVersion: swarmkit/v1\nkind: EvalSet\nmetadata: {id: b}\n")
    with pytest.raises(EvalSetInvalidError):  # missing target + cases
        load_eval_set(ws, "bad")
