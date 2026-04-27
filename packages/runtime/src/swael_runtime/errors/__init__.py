"""Structured errors emitted during workspace resolution.

Every error the resolver can emit is a :class:`ResolutionError` with a
machine-readable ``code``, the offending artifact path, a YAML pointer
(RFC 6901) into the file, optional rule citation, and an optional
one-line remediation suggestion. Multiple errors from a single
``resolve_workspace()`` call are collected into a
:class:`ResolutionErrors` aggregate; the resolver does not short-circuit
on the first validation failure (see
``design/details/topology-loader.md`` phase 2).

Human-readable rendering of these errors is task #23 — the CLI and the
authoring-swarm Review Leader both consume the structured form.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ResolutionError:
    """A single, structured resolution failure."""

    code: str
    """Machine-readable identifier, e.g. ``schema.required-field`` or
    ``skill.unknown-id``. Stable so tooling can match on it."""

    message: str
    """Short human-readable sentence stating what went wrong."""

    artifact_path: Path
    """The YAML file the error came from. Absolute or workspace-relative;
    the resolver emits absolute paths."""

    yaml_pointer: str = ""
    """JSON Pointer (RFC 6901) into the artifact, e.g.
    ``/agents/root/skills/2``. Empty string means the error applies to
    the whole artifact."""

    rule: str | None = None
    """Schema rule or section reference, e.g. ``required: [reasoning]`` or
    ``design §6.3``. Optional; present when the resolver can cite one."""

    suggestion: str | None = None
    """One-line remediation hint in the user's vocabulary. Empty for
    errors where no generic suggestion applies."""

    related: Sequence[ResolutionError] = field(default_factory=tuple)
    """Sibling errors that share context (e.g. a cross-reference that
    affects multiple agents). Task #23 can fold these into a single
    rendered block."""


class ResolutionErrors(Exception):
    """Aggregate exception carrying a list of :class:`ResolutionError`.

    Raised by ``resolve_workspace()`` when any resolution step produces
    at least one error. All errors across every artifact are collected
    before raising — the caller sees a complete picture, not just the
    first failure.
    """

    def __init__(self, errors: Sequence[ResolutionError]) -> None:
        if not errors:
            raise ValueError("ResolutionErrors must carry at least one ResolutionError")
        self.errors: tuple[ResolutionError, ...] = tuple(errors)
        # A short message for __str__; tooling should prefer ``.errors``.
        n = len(self.errors)
        super().__init__(f"Resolution failed with {n} error{'s' if n != 1 else ''}.")

    def __iter__(self) -> Iterator[ResolutionError]:
        return iter(self.errors)

    def __len__(self) -> int:
        return len(self.errors)


def yaml_pointer(parts: Sequence[object]) -> str:
    """Build a JSON Pointer (RFC 6901) from a sequence of path segments.

    jsonschema's ``ValidationError.absolute_path`` returns a deque of
    strings and ints; this helper escapes the reserved characters and
    joins with ``/``. Returns ``""`` for an empty path (the whole
    artifact).
    """
    if not parts:
        return ""
    escaped = ["/" + _escape(str(p)) for p in parts]
    return "".join(escaped)


def _escape(segment: str) -> str:
    # RFC 6901 §4: escape ~ as ~0 and / as ~1. Order matters.
    return segment.replace("~", "~0").replace("/", "~1")


__all__ = [
    "ResolutionError",
    "ResolutionErrors",
    "yaml_pointer",
]
