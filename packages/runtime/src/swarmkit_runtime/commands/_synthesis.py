"""Turn each command in a pack into an ordinary skill.

The alternative was to teach every consumer about packs — the tool builder, `requires:`
validation, the archetype merge, the UI. Synthesizing instead means a command *is* a skill from
the moment the registry is built, and nothing downstream needs to know where it came from.

The synthetic id is ``<pack>-<command>``, which is a legal skill identifier and a legal tool name
in every provider's grammar. A collision with a hand-authored skill is a resolution error naming
both, rather than one silently shadowing the other.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError
from swarmkit_schema.models import SwarmKitSkill

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.skills import ResolvedSkill

if TYPE_CHECKING:
    from collections.abc import Mapping

    from swarmkit_runtime.commands._config import CommandPackConfig

#: Where a synthetic skill claims to come from. Not a real file — but every consumer that reports
#: an error wants a path, and pointing at the workspace is more useful than pointing at nothing.
SYNTHETIC_SOURCE = Path("<command-pack>")


def synthetic_skill_id(pack_id: str, command_id: str) -> str:
    return f"{pack_id}-{command_id}"


def synthesize_pack_skills(
    packs: Mapping[str, CommandPackConfig],
    existing: Mapping[str, ResolvedSkill],
    *,
    workspace_path: Path | None = None,
) -> tuple[dict[str, ResolvedSkill], list[ResolutionError]]:
    """Build one :class:`ResolvedSkill` per command across every pack.

    ``existing`` is the hand-authored registry; a synthetic id that collides with one is an error
    rather than an overwrite. Hand-authored skills that *target* a pack command are untouched —
    naming the same command twice is fine, and gives the author a place to put a better
    description and an input schema.
    """
    source = workspace_path or SYNTHETIC_SOURCE
    built: dict[str, ResolvedSkill] = {}
    errors: list[ResolutionError] = []

    for pack in packs.values():
        for spec in pack.commands.values():
            skill_id = synthetic_skill_id(pack.pack_id, spec.command_id)
            if skill_id in existing:
                errors.append(
                    ResolutionError(
                        code="command-pack.id-collision",
                        message=(
                            f"command '{spec.command_id}' in pack '{pack.pack_id}' would be "
                            f"exposed as skill '{skill_id}', but a skill with that id is already "
                            f"defined in this workspace."
                        ),
                        artifact_path=source,
                        suggestion=(
                            "Rename the skill, the pack, or the command. Shadowing one with the "
                            "other would make it ambiguous which the agent actually calls."
                        ),
                    )
                )
                continue
            if skill_id in built:
                continue

            raw: dict[str, Any] = {
                "apiVersion": "swarmkit/v1",
                "kind": "Skill",
                "metadata": {
                    "id": skill_id,
                    "name": f"{pack.pack_id}: {spec.command_id}",
                    "description": _describe(pack, spec.command_id, spec.description),
                },
                "category": "capability",
                "inputs": _inputs_for(spec.placeholders),
                "implementation": {
                    "type": "command",
                    "pack": pack.pack_id,
                    "command": spec.command_id,
                },
                # The command was declared by a person in workspace.yaml;
                # only the skill wrapper around it is derived.
                "provenance": {"authored_by": "human", "version": "1.0.0"},
            }
            try:
                model = SwarmKitSkill.model_validate(raw)
            except PydanticValidationError as exc:
                errors.append(
                    ResolutionError(
                        code="command-pack.synthetic-skill-invalid",
                        message=(
                            f"command '{spec.command_id}' in pack '{pack.pack_id}' could not be "
                            f"exposed as a skill: {exc.errors()[0]['msg']}"
                        ),
                        artifact_path=source,
                    )
                )
                continue
            built[skill_id] = ResolvedSkill(
                id=skill_id,
                raw=model,
                source_path=source,
                pack_origin=(pack.pack_id, spec.command_id, spec.effects),
            )
    return built, errors


def _describe(pack: CommandPackConfig, command_id: str, description: str) -> str:
    """A description a model can act on, stating the effect explicitly.

    The effect is named in the text as well as enforced by the tier, because a model choosing
    between two similar tools reads the description and never sees the permission config.
    """
    base = description.strip() or (
        f"Runs the '{command_id}' command from the '{pack.pack_id}' pack."
    )
    effect = pack.commands[command_id].effects
    suffix = (
        " Read-only: it does not modify anything."
        if effect == "read"
        else " This modifies data and may require approval."
    )
    return base + suffix


def _inputs_for(placeholders: frozenset[str]) -> dict[str, Any]:
    """A JSON-Schema object with one required string per argv placeholder.

    Every placeholder is required. A command missing an argument would run against the wrong
    thing and report success, so there is no useful 'optional' here — the runner rejects a missing
    one anyway, and declaring it required lets the model find that out before the call.
    """
    names = sorted(placeholders)
    return {
        "type": "object",
        "properties": {n: {"type": "string"} for n in names},
        "required": names,
    }
