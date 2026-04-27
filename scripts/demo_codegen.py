"""Demo: load every fixture through its generated pydantic model, report counts,
and print one round-trip so reviewers see typed access in action.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from swael_schema.models import (
    SwarmKitArchetype,
    SwarmKitSkill,
    SwarmKitTopology,
    SwarmKitTrigger,
    SwarmKitWorkspace,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "packages" / "schema" / "tests" / "fixtures"

MAP = {
    "topology": SwarmKitTopology,
    "skill": SwarmKitSkill,
    "archetype": SwarmKitArchetype,
    "workspace": SwarmKitWorkspace,
    "trigger": SwarmKitTrigger,
}


def main() -> int:
    print("loading every valid fixture through its generated pydantic model:")
    for kind, model in MAP.items():
        count = 0
        for fixture in sorted((FIXTURES / kind).glob("*.yaml")):
            data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
            model.model_validate(data)
            count += 1
        print(f"  ✓ {kind}: {count} fixtures loaded OK")

    # Show one typed round-trip.
    print()
    print("round-trip — topology/from-design-doc.yaml → SwarmKitTopology → JSON:")
    fixture = FIXTURES / "topology" / "from-design-doc.yaml"
    topology = SwarmKitTopology.model_validate(yaml.safe_load(fixture.read_text(encoding="utf-8")))
    # Show a few typed-access paths the IDE can now autocomplete.
    root = topology.agents.root
    print(f"  topology.metadata.name  = {topology.metadata.name!r}")
    print(f"  topology.agents.root.id = {root.id!r}")
    print(f"  topology.agents.root.role = {root.role!r}")

    print()
    print("all cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
