"""Demo: validate every committed topology fixture and print pass/fail.

Used by `just demo-topology-schema`. Exits non-zero if any fixture fails the
validation the fixture name claims (valid/ fixtures must validate;
topology-invalid/ fixtures must fail).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from jsonschema import ValidationError
from swarmkit_schema import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "packages" / "schema" / "tests" / "fixtures"


def _load(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _run_case(path: Path, should_pass: bool) -> bool:
    tag = "✓" if should_pass else "✗"
    try:
        validate("topology", _load(path))
        actually_passed = True
    except ValidationError as exc:
        actually_passed = False
        reason = str(exc).splitlines()[0]

    ok = actually_passed == should_pass
    marker = "✓" if ok else "✗"
    if should_pass:
        note = "valid" if actually_passed else f"expected valid, got: {reason}"
    else:
        note = "rejected" if not actually_passed else "expected invalid, passed"
    print(f"  {marker} {tag} {path.name}  [{note}]")
    return ok


def main() -> int:
    valid_dir = FIXTURES / "topology"
    invalid_dir = FIXTURES / "topology-invalid"
    all_ok = True

    print("valid fixtures:")
    for p in sorted(valid_dir.glob("*.yaml")):
        all_ok &= _run_case(p, should_pass=True)

    print("invalid fixtures (should fail validation):")
    for p in sorted(invalid_dir.glob("*.yaml")):
        all_ok &= _run_case(p, should_pass=False)

    print()
    print("all cases passed." if all_ok else "one or more cases failed.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
