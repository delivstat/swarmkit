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


def parse_eval_set(path: Path) -> EvalSet:
    """Parse and validate one eval-set file. Raises ``EvalSetInvalidError``."""
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
    out: list[EvalSet] = []
    for f in _files(workspace_root):
        try:
            out.append(parse_eval_set(f))
        except EvalSetInvalidError:
            continue
    return out


def _files(workspace_root: Path) -> list[Path]:
    d = evals_dir(workspace_root)
    return sorted([*d.glob("*.yaml"), *d.glob("*.yml")]) if d.is_dir() else []


def find_eval_set_file(workspace_root: Path, set_id: str) -> Path | None:
    """The file under ``<workspace>/evals/`` holding set ``set_id``: the one whose stem is the id,
    else the first whose ``metadata.id`` is. Read leniently — a file that fails validation is still
    found, so an editor can replace a broken set rather than be told it does not exist."""
    files = _files(workspace_root)
    for f in files:
        if f.stem == set_id:
            return f
    for f in files:
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        meta = raw.get("metadata") if isinstance(raw, dict) else None
        if isinstance(meta, dict) and meta.get("id") == set_id:
            return f
    return None


def load_eval_set(workspace_root: Path, ref: str) -> EvalSet:
    """Resolve ``ref`` to an eval-set: a path to a YAML file, or an id / filename stem
    under ``<workspace>/evals/``."""
    as_path = Path(ref)
    if as_path.is_file():
        return parse_eval_set(as_path)

    found = find_eval_set_file(workspace_root, ref)
    if found is not None:
        return parse_eval_set(found)

    available = [es.metadata.id for es in list_eval_sets(workspace_root)]
    hint = f" Available: {', '.join(available)}" if available else " None found under evals/."
    raise EvalSetNotFoundError(f"No eval-set matching {ref!r}.{hint}")
