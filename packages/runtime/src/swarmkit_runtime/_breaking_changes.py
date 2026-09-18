"""The runtime's breaking changes, as data (upgrade-command.md).

Git tags are immutable history, so breaking-ness cannot be back-filled into the changelog. This
curated list is the source of truth `swarmkit upgrade` reads — bundled in the wheel so it works
offline — to warn before an upgrade crosses a breaking version. A new breaking release adds an entry
here in the same PR, and the tag convention going forward marks it too, so the two never drift.

Keep it newest-last (or any order — `changes_between` sorts). Each `migration` is a URL a person can
open.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BreakingChange:
    version: str
    summary: str
    migration: str


BREAKING_CHANGES: tuple[BreakingChange, ...] = (
    BreakingChange(
        "1.189.0",
        "The bundled pipeline layer was removed — `kind: StageGraph`, the saga controller, "
        "`swarmkit orchestrator`, `swarmkit pipeline`, `POST /pipelines/*`. Sequencing is the "
        "calling application's; `examples/pipeline-orchestrator/` is the reference.",
        "https://delivstat.github.io/swarmkit/design-notes/extracting-the-pipeline/",
    ),
    BreakingChange(
        "1.199.0",
        "`permission: readonly` now decides write-ness by declared `effects`, not by scanning the "
        "tool name. An MCP tool with an unknown effect under `readonly` is now DENIED. Declare "
        "`effects: {tool: read|write}` on the server.",
        "https://github.com/delivstat/swarmkit/blob/main/docs/notes/mcp-effects-migration.md",
    ),
)


def _key(version: str) -> tuple[int, ...]:
    """A comparable version tuple. A non-numeric part sorts as 0 rather than raising — the list is
    curated, but a typo should not take the upgrade command down."""
    out = []
    for part in version.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def changes_between(installed: str, target: str) -> list[BreakingChange]:
    """Breaking changes an upgrade from ``installed`` to ``target`` crosses — exclusive of the
    installed version (already past it), inclusive of the target. Empty when target ≤ installed."""
    lo, hi = _key(installed), _key(target)
    if hi <= lo:
        return []
    hits = [c for c in BREAKING_CHANGES if lo < _key(c.version) <= hi]
    return sorted(hits, key=lambda c: _key(c.version))
