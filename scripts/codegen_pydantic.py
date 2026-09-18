"""Generate pydantic v2 models from the canonical JSON Schemas.

Writes one module per schema into
`packages/schema/python/src/swarmkit_schema/models/` plus an `__init__.py`
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
OUTPUT_DIR = REPO_ROOT / "packages" / "schema" / "python" / "src" / "swarmkit_schema" / "models"

# Map <artifact-name> -> <root class name exported from the generated module>.
# Must match `title` in each .schema.json (datamodel-code-generator defaults
# to that name).
ARTIFACTS: dict[str, str] = {
    "topology": "SwarmKitTopology",
    "skill": "SwarmKitSkill",
    "archetype": "SwarmKitArchetype",
    "workspace": "SwarmKitWorkspace",
    "trigger": "SwarmKitTrigger",
    "executor-adapter": "SwarmKitExecutorAdapter",
    "model-provider": "SwarmKitModelProvider",
    "role-registry": "SwarmKitRoleRegistry",
    "approval-policy": "SwarmKitApprovalPolicy",
    "funnel": "SwarmKitFunnel",
    "contract": "SwarmKitContract",
}


HEADER = """# ruff: noqa
# mypy: ignore-errors
# This file is generated from the canonical JSON Schema. Do not edit by hand.
# Regenerate with: just schema-codegen
"""


def _module_name(artifact: str) -> str:
    # Artifact keys are kebab-case (e.g. `executor-adapter`); Python module names must be
    # importable, so hyphens become underscores for the generated file + its import.
    return artifact.replace("-", "_")


def _generate_one(artifact: str) -> None:
    schema_path = SCHEMAS_DIR / f"{artifact}.schema.json"
    output_path = OUTPUT_DIR / f"{_module_name(artifact)}.py"
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
        # `class Trigger(str, Enum)`, not `class Trigger(Enum)`. A plain Enum member never equals
        # its own string, so every `binding.trigger == "pre_input"` in the runtime was False and no
        # decision skill fired at any trigger point — silently, because an empty selection is
        # indistinguishable from "none configured". Applies to every generated enum, since the next
        # one compared against a literal would have the same bug.
        "--use-subclass-enum",
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

    # Prepend the do-not-edit header, and make every string enum a StrEnum.
    #
    # `--use-subclass-enum` above only subclasses `str` for an enum whose schema ALSO says
    # `"type": "string"`. Most enums in the canonical schemas do not (they are bare `enum: [...]`),
    # so #781 fixed `Trigger` and left `Category`, `Permission`, `Role`, `Provider` and some sixty
    # others as plain `Enum` — and `skill.raw.category == "decision"` stayed False, which meant the
    # skill-backed governance provider was never built and no `governance.decision_skills` binding
    # ever ran its skill (the mock auto-passed every one). Rewriting the class line here is
    # deterministic and covers every enum the generator emits, whatever the schema said.
    current = output_path.read_text(encoding="utf-8")
    current = _str_enums(current)
    output_path.write_text(HEADER + current, encoding="utf-8")


def _str_enums(source: str) -> str:
    """`class X(Enum):` -> `class X(StrEnum):` for enums whose members are all strings."""
    import re  # noqa: PLC0415

    def _rewrite(match: re.Match[str]) -> str:
        name, body = match.group(1), match.group(2)
        # A member is `    name = "value"`. A docstring line can contain ` = ` too (Quorum's
        # does), but its right-hand side is prose, not a quoted string, so it does not count as
        # a member — and does not veto the rewrite.
        members = re.findall(r"^    \w+ = (.+)$", body, re.M)
        quoted = [m for m in members if m.strip().startswith(('"', "'"))]
        if quoted and all(m.strip().startswith(('"', "'")) or "=" in m for m in members):
            return f"class {name}(StrEnum):{body}"
        return match.group(0)

    rewritten = re.sub(r"^class (\w+)\(Enum\):((?:\n(?:    .*|))*)", _rewrite, source, flags=re.M)
    if "StrEnum" in rewritten and "from enum import Enum, StrEnum" not in rewritten:
        rewritten = rewritten.replace(
            "from enum import Enum\n", "from enum import Enum, StrEnum\n", 1
        )
    return rewritten


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
        body.append(f"from .{_module_name(artifact)} import {root}")
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
