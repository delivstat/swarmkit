"""Generate pydantic v2 models from the canonical JSON Schemas.

Writes one module per schema into
`packages/schema/python/src/swael_schema/models/` plus an `__init__.py`
that re-exports the root model of each artifact type.

The canonical JSON Schemas are the source of truth (see
`docs/notes/schema-change-discipline.md`); these models are generated and
must not be hand-edited. Regenerate via `just schema-codegen` whenever a
schema changes.

CI runs `just schema-codegen` in a dirty-tree check — uncommitted
regenerated output fails the build.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "packages" / "schema" / "schemas"
OUTPUT_DIR = REPO_ROOT / "packages" / "schema" / "python" / "src" / "swael_schema" / "models"

# Map <artifact-name> -> <root class name exported from the generated module>.
# Must match `title` in each .schema.json (datamodel-code-generator defaults
# to that name).
ARTIFACTS: dict[str, str] = {
    "topology": "SwarmKitTopology",
    "skill": "SwarmKitSkill",
    "archetype": "SwarmKitArchetype",
    "workspace": "SwarmKitWorkspace",
    "trigger": "SwarmKitTrigger",
}


HEADER = """# ruff: noqa
# mypy: ignore-errors
# This file is generated from the canonical JSON Schema. Do not edit by hand.
# Regenerate with: just schema-codegen
"""


def _generate_one(artifact: str) -> None:
    schema_path = SCHEMAS_DIR / f"{artifact}.schema.json"
    output_path = OUTPUT_DIR / f"{artifact}.py"
    if not schema_path.is_file():
        raise FileNotFoundError(f"Schema missing: {schema_path}")

    cmd = [
        "datamodel-codegen",
        "--input",
        str(schema_path),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(output_path),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.11",
        "--use-schema-description",
        "--use-title-as-name",
        "--use-double-quotes",
        "--use-standard-collections",
        "--use-union-operator",
        "--collapse-root-models",
        "--disable-timestamp",
        "--field-constraints",
        "--enum-field-as-literal",
        "one",
        # Aliases keep Python-keyword fields (like `class:`) loadable from
        # the original JSON/YAML while presenting a Pythonic attribute name
        # (`class_`). `populate-by-field-name` lets code also construct the
        # model using the attribute name.
        "--allow-population-by-field-name",
    ]
    print(f"  ▶ {artifact}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"datamodel-codegen failed for {artifact}")

    # Prepend the do-not-edit header.
    current = output_path.read_text(encoding="utf-8")
    output_path.write_text(HEADER + current, encoding="utf-8")


def _write_init() -> None:
    body = [
        "# ruff: noqa",
        "# mypy: ignore-errors",
        "# Generated package — do not edit by hand. Regenerate with:",
        "#   just schema-codegen",
        "",
        '"""Pydantic v2 models generated from `packages/schema/schemas/`."""',
        "",
    ]
    for artifact, root in ARTIFACTS.items():
        body.append(f"from .{artifact} import {root}")
    body.append("")
    body.append("__all__ = [")
    for root in ARTIFACTS.values():
        body.append(f'    "{root}",')
    body.append("]")
    body.append("")
    (OUTPUT_DIR / "__init__.py").write_text("\n".join(body), encoding="utf-8")


def main() -> int:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    print(f"generating pydantic models into {OUTPUT_DIR.relative_to(REPO_ROOT)}")
    for artifact in ARTIFACTS:
        _generate_one(artifact)
    _write_init()
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
