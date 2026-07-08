"""Demo: the published fleet-enrollment protocol schemas in action (design 19).

Validates every committed protocol fixture (valid/ must pass, *-invalid/ must fail), then shows the
cross-file `$ref` at work: a valid register-response passes, but the same response with a tampered
embedded InstanceState is rejected — proving a third-party client gets full-depth validation from a
single call.

Usage:  uv run python scripts/demo_protocol_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import ValidationError
from swarmkit_schema import ProtocolSchemaName, validate_protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "packages" / "schema" / "tests" / "fixtures" / "protocol"

MESSAGES: tuple[ProtocolSchemaName, ...] = (
    "credential",
    "instance-state",
    "register-request",
    "register-response",
    "join-request",
    "join-response",
)


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    failures = 0
    for name in MESSAGES:
        for fixture in sorted((FIXTURES / name).glob("*.json")):
            try:
                validate_protocol(name, _load(fixture))
                print(f"  ✔ {name}/{fixture.name} — valid")
            except ValidationError:
                print(f"  ✗ {name}/{fixture.name} — should be valid but FAILED")
                failures += 1
        for fixture in sorted((FIXTURES / f"{name}-invalid").glob("*.json")):
            try:
                validate_protocol(name, _load(fixture))
                print(f"  ✗ {name}-invalid/{fixture.name} — should have FAILED but passed")
                failures += 1
            except ValidationError:
                print(f"  ✔ {name}-invalid/{fixture.name} — rejected")

    print("\nCross-file $ref (a client gets full-depth validation from one call):")
    resp = _load(FIXTURES / "register-response" / "valid.json")
    assert isinstance(resp, dict)
    validate_protocol("register-response", resp)
    print("  ✔ register-response with a well-formed embedded InstanceState — valid")
    resp["instance_state"].pop("artifacts")  # tamper the nested InstanceState
    try:
        validate_protocol("register-response", resp)
        print("  ✗ tampered embedded InstanceState slipped through")
        failures += 1
    except ValidationError as exc:
        print(f"  ✔ tampered embedded InstanceState rejected: {exc.message}")

    print(f"\n{'FAILED' if failures else 'OK'} — {failures} unexpected result(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
