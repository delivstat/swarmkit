"""Load + validate an eval-set from a workspace.

Eval sets are YAML files under ``<workspace>/evals/`` (or any path). Each is validated against
``eval-set.schema.json`` — the same check the CLI, serve and the portal apply — then narrowed to the
runtime ``EvalSet`` model. The one rule the schema cannot express, unique case ids, is checked here
(design/details/eval-set-schema-v1.md).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from jsonschema import ValidationError as SchemaError
from pydantic import ValidationError
from swarmkit_schema import validate

from swarmkit_runtime.errors import yaml_pointer
from swarmkit_runtime.eval._errors import EvalSetInvalidError, EvalSetNotFoundError
from swarmkit_runtime.eval._models import EvalSet


def _parse(path: Path) -> EvalSet:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EvalSetInvalidError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvalSetInvalidError(f"{path}: expected a mapping at the top level")
    try:
        validate("eval-set", raw)
    except SchemaError as exc:
        where = yaml_pointer(list(exc.absolute_path)) or "/"
        raise EvalSetInvalidError(f"{path} @ {where}: {exc.message.splitlines()[0]}") from exc
    try:
        eval_set = EvalSet.model_validate(raw)
    except ValidationError as exc:
        raise EvalSetInvalidError(f"{path}: {exc}") from exc
    seen: set[str] = set()
    for case in eval_set.cases:
        if case.id in seen:
            raise EvalSetInvalidError(
                f"{path}: case id {case.id!r} is used twice — reports and comparisons between runs "
                "are keyed by case id, so each must be unique within the set"
            )
        seen.add(case.id)
    return eval_set


def evals_dir(workspace_root: Path) -> Path:
    return workspace_root / "evals"


def list_eval_sets(workspace_root: Path) -> list[EvalSet]:
    """Every valid eval-set under ``<workspace>/evals/`` (skips invalid files)."""
    d = evals_dir(workspace_root)
    if not d.is_dir():
        return []
    out: list[EvalSet] = []
    for f in sorted([*d.glob("*.yaml"), *d.glob("*.yml")]):
        try:
            out.append(_parse(f))
        except EvalSetInvalidError:
            continue
    return out


def load_eval_set(workspace_root: Path, ref: str) -> EvalSet:
    """Resolve ``ref`` to an eval-set: a path to a YAML file, or an id / filename stem
    under ``<workspace>/evals/``."""
    as_path = Path(ref)
    if as_path.is_file():
        return _parse(as_path)

    d = evals_dir(workspace_root)
    if d.is_dir():
        for f in sorted([*d.glob("*.yaml"), *d.glob("*.yml")]):
            if f.stem == ref:
                return _parse(f)
        for f in sorted([*d.glob("*.yaml"), *d.glob("*.yml")]):
            try:
                es = _parse(f)
            except EvalSetInvalidError:
                continue
            if es.metadata.id == ref:
                return es

    available = [es.metadata.id for es in list_eval_sets(workspace_root)]
    hint = f" Available: {', '.join(available)}" if available else " None found under evals/."
    raise EvalSetNotFoundError(f"No eval-set matching {ref!r}.{hint}")
