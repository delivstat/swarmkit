"""A skill may declare the minimum runtime it needs, and be refused when it is not met.

`provenance.requires_runtime` exists because a skill is portable data that can outlive the runtime
that installed it. A `command`-backed skill needs 1.197.0; `requires:` ordering needs 1.193.0;
declared `effects` under a `readonly` MCP server needs 1.199.0. Without a floor, such a skill
resolves cleanly into an older workspace and fails much later with an error naming nothing useful.

That is the `swarmkit-webui` failure in a different costume: a separately-versioned artifact with an
unbounded floor, resolvable and broken, discovered by a user rather than by a check. A floor that
refuses at load costs one clear message; the absence of one costs a debugging session.

**Refusing is the safe direction, but not knowing is not.** When the runtime version cannot be
read — a source checkout with no installed metadata — the floor is skipped rather than failed:
refusing every skill because we cannot introspect ourselves would break the development case to
protect the deployed one.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from swarmkit_runtime._versions import runtime_version

if TYPE_CHECKING:
    from collections.abc import Mapping

    from swarmkit_runtime.errors import ResolutionError
    from swarmkit_runtime.skills import ResolvedSkill

_FLOOR = re.compile(r"^>=\s*(\d+)\.(\d+)\.(\d+)$")


def _parse(spec: str) -> tuple[int, int, int] | None:
    m = _FLOOR.match(spec.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _version_tuple(raw: str) -> tuple[int, int, int] | None:
    """The numeric prefix of a version, or None.

    Tolerant of a suffix — `1.201.0rc1` and `1.201.0+local` compare as 1.201.0. A pre-release is
    not a reason to refuse a skill whose floor its final release will meet.
    """
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def unmet_floors(
    skills: Mapping[str, ResolvedSkill], *, current: str | None = None
) -> list[ResolutionError]:
    """Every skill whose declared floor this runtime does not meet.

    Returns structured errors naming both versions, because "incompatible" without the two numbers
    sends the reader to the source to find out what it wanted.
    """
    from swarmkit_runtime.errors import ResolutionError  # noqa: PLC0415

    version = current if current is not None else runtime_version()
    here = _version_tuple(version) if version else None
    if here is None:
        return []  # uninstalled source tree — see the module docstring

    out: list[ResolutionError] = []
    for skill_id, skill in sorted(skills.items()):
        provenance = getattr(skill.raw, "provenance", None)
        spec = getattr(provenance, "requires_runtime", None) if provenance else None
        if not spec:
            continue
        floor = _parse(str(spec))
        if floor is None:
            out.append(
                ResolutionError(
                    code="skill.bad-runtime-floor",
                    message=(
                        f"Skill {skill_id!r} declares requires_runtime {spec!r}, which is not a "
                        f"floor of the form '>=X.Y.Z'."
                    ),
                    artifact_path=skill.source_path,
                    yaml_pointer="/provenance/requires_runtime",
                    suggestion="Write it as '>=1.197.0'.",
                )
            )
            continue
        if here < floor:
            want = ".".join(str(n) for n in floor)
            out.append(
                ResolutionError(
                    code="skill.runtime-too-old",
                    message=(
                        f"Skill {skill_id!r} needs swarmkit-runtime >={want}; this runtime is "
                        f"{version}."
                    ),
                    artifact_path=skill.source_path,
                    yaml_pointer="/provenance/requires_runtime",
                    suggestion=(
                        f"Upgrade the runtime (`pip install -U 'swarmkit-runtime>={want}'`), or "
                        f"remove the skill from this workspace."
                    ),
                )
            )
    return out


__all__ = ["unmet_floors"]
