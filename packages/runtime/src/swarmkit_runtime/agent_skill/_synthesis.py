"""One ``agent`` skill per topology in the workspace — what ``pack:workspace`` grants.

Every topology `swarmkit serve` hosts is an A2A agent to the outside (it is on the card). Inside
the workspace nothing is reachable until granted — the rule MCP servers and command packs
already follow — so "this supervisor may run any topology here" has to be *sayable* in one line
without hand-writing a skill per topology and forgetting the next one. This synthesizes the
skills the way command packs synthesize theirs: in memory, at registry build time, so every
consumer downstream sees an ordinary skill and nothing has to know where it came from.

The synthetic id is ``topology-<name>``. A collision with a hand-authored skill is an error
naming both, as for a pack command. A hand-authored ``agent`` skill that *targets* the same
topology is untouched — that is where a better description, a stricter tier or a different
``on_unanswerable`` goes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from swarmkit_schema.models import SwarmKitSkill

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.skills import ResolvedSkill

#: The pack id `pack:workspace` names. Reserved: a command pack may not use it.
WORKSPACE_PACK = "workspace"

SYNTHETIC_SOURCE = Path("<workspace-topology>")


def synthetic_topology_skill_id(topology_id: str) -> str:
    return f"topology-{topology_id}"


def synthesize_topology_skills(
    topologies: Iterable[tuple[str, str, Path]],
    existing: Mapping[str, ResolvedSkill],
) -> tuple[dict[str, ResolvedSkill], list[ResolutionError]]:
    """Build one ``agent`` skill per ``(topology_id, description, path)``."""
    built: dict[str, ResolvedSkill] = {}
    errors: list[ResolutionError] = []
    for topology_id, description, path in topologies:
        skill_id = synthetic_topology_skill_id(topology_id)
        if skill_id in existing:
            errors.append(
                ResolutionError(
                    code="workspace-pack.id-collision",
                    message=(
                        f"topology '{topology_id}' is exposed to `pack:workspace` as skill "
                        f"'{skill_id}', but a skill with that id is already defined in this "
                        "workspace."
                    ),
                    artifact_path=path,
                    suggestion=(
                        "Rename the skill. If it was meant to be the topology's agent skill, "
                        "give it its own id and target the topology with `implementation.topology`."
                    ),
                )
            )
            continue
        raw: dict[str, Any] = {
            "apiVersion": "swarmkit/v1",
            "kind": "Skill",
            "metadata": {
                "id": skill_id,
                "name": f"Run {topology_id}",
                "description": ((description.strip() + " ") if description.strip() else "")
                + f"Runs the '{topology_id}' topology of this workspace as a child run and "
                "returns its output.",
            },
            "category": "capability",
            "implementation": {
                "type": "agent",
                "topology": topology_id,
                # A topology does whatever its agents do; nothing here can promise `read`.
                "effects": "unknown",
                "permission": "cautious",
            },
            "provenance": {"authored_by": "human", "version": "1.0.0"},
        }
        try:
            model = SwarmKitSkill.model_validate(raw)
        except PydanticValidationError as exc:
            errors.append(
                ResolutionError(
                    code="workspace-pack.synthetic-skill-invalid",
                    message=(
                        f"topology '{topology_id}' could not be exposed as a skill: "
                        f"{exc.errors()[0]['msg']}"
                    ),
                    artifact_path=path,
                )
            )
            continue
        built[skill_id] = ResolvedSkill(
            id=skill_id,
            raw=model,
            source_path=path,
            pack_origin=(WORKSPACE_PACK, topology_id, "unknown"),
        )
    return built, errors
