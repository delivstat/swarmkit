"""Validate the SDLC archetype + skill library against the canonical schemas.

Runnable demo/test for slice 2 (design/details/sdlc-pipeline-example.md):

    uv run python examples/sdlc-pipeline/validate_library.py
"""

from __future__ import annotations

import pathlib

import yaml
from jsonschema import ValidationError
from swarmkit_schema import validate

BASE = pathlib.Path(__file__).parent / "workspace"


def main() -> int:
    ok = bad = 0
    for kind, sub in (
        ("archetype", "archetypes"),
        ("skill", "skills"),
        ("topology", "topologies"),
        ("funnel", "funnels"),
        ("contract", "contracts"),
        ("role-registry", "roles"),
        ("trigger", "triggers"),
        ("stage-graph", "pipelines"),
    ):
        for f in sorted((BASE / sub).glob("*.yaml")):
            try:
                validate(kind, yaml.safe_load(f.read_text(encoding="utf-8")))
                print(f"  ok  {sub}/{f.name}")
                ok += 1
            except ValidationError as e:
                loc = "/".join(str(p) for p in e.absolute_path)
                print(f"  FAIL {sub}/{f.name}: {e.message}  @ {loc}")
                bad += 1
    print(f"\n{ok} valid, {bad} invalid")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
