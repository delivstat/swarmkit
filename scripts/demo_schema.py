"""Demo: validate every committed fixture for a given schema and print pass/fail.

Used by `just demo-<artifact>-schema` and by the aggregate `just demo-schema`
(lands with Task #16). Exits non-zero if any fixture's actual result does not
match its directory (valid/ fixtures must validate; *-invalid/ fixtures must
fail).

Usage:
    python scripts/demo_schema.py <artifact>
  where <artifact> is one of: topology | skill | archetype | workspace | trigger
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

import yaml
from jsonschema import ValidationError
from swael_schema import SchemaName, validate

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "packages" / "schema" / "tests" / "fixtures"


def _load(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _run_case(schema: SchemaName, path: Path, expected: Literal["valid", "invalid"]) -> bool:
    try:
        validate(schema, _load(path))
        actually_passed = True
        reason = ""
    except ValidationError as exc:
        actually_passed = False
        reason = str(exc).splitlines()[0]

    ok = actually_passed == (expected == "valid")
    marker = "✓" if ok else "✗"
    tag = "✓" if expected == "valid" else "✗"
    if expected == "valid":
        note = "valid" if actually_passed else f"expected valid, got: {reason}"
    else:
        note = "rejected" if not actually_passed else "expected invalid, passed"
    print(f"  {marker} {tag} {path.name}  [{note}]")
    return ok


def demo(schema: SchemaName) -> int:
    valid_dir = FIXTURES / schema
    invalid_dir = FIXTURES / f"{schema}-invalid"
    if not valid_dir.is_dir():
        print(f"no fixtures at {valid_dir}", file=sys.stderr)
        return 1

    all_ok = True

    print(f"valid fixtures ({schema}):")
    for p in sorted(valid_dir.glob("*.yaml")):
        all_ok &= _run_case(schema, p, expected="valid")

    if invalid_dir.is_dir():
        print(f"invalid fixtures ({schema}) — should fail validation:")
        for p in sorted(invalid_dir.glob("*.yaml")):
            all_ok &= _run_case(schema, p, expected="invalid")

    print()
    print("all cases passed." if all_ok else "one or more cases failed.")
    return 0 if all_ok else 1


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: demo_schema.py <artifact>", file=sys.stderr)
        return 2
    schema = sys.argv[1]
    if schema not in ("topology", "skill", "archetype", "workspace", "trigger"):
        print(f"unknown schema: {schema}", file=sys.stderr)
        return 2
    return demo(schema)  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(main())
