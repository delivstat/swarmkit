"""Workspace directory discovery (M1.2).

Walks a SwarmKit workspace on disk, parses every YAML artifact, returns a
flat list of ``DiscoveredArtifact`` (plus the workspace.yaml). Pure I/O
and parsing — no schema validation, no resolution, no registry building.
Those are M1.3+ concerns.

Design reference: ``design/details/topology-loader.md`` phase 1.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

ArtifactKind = Literal["workspace", "topology", "skill", "archetype", "trigger"]

# Directory → expected artifact kind.
# `triggers/` and `schedules/` both hold ``kind: Trigger`` artifacts
# (schedules is a naming convention for cron triggers, per §5.4).
_KIND_DIRS: dict[str, ArtifactKind] = {
    "topologies": "topology",
    "archetypes": "archetype",
    "skills": "skill",
    "triggers": "trigger",
    "schedules": "trigger",
}

# What the artifact's ``kind:`` field should say for each internal kind.
_EXPECTED_KIND_STR: dict[ArtifactKind, str] = {
    "workspace": "Workspace",
    "topology": "Topology",
    "skill": "Skill",
    "archetype": "Archetype",
    "trigger": "Trigger",
}

_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


@dataclass(frozen=True)
class DiscoveredArtifact:
    """One artifact found on disk during discovery."""

    path: Path
    kind: ArtifactKind
    raw: Mapping[str, Any]


class DiscoveryError(Exception):
    """Base class for discovery failures."""


class WorkspaceNotFoundError(DiscoveryError):
    """The workspace root does not exist or is not a directory."""

    def __init__(self, root: Path) -> None:
        super().__init__(f"Workspace directory not found or not a directory: {root}")
        self.root = root


class MissingWorkspaceFileError(DiscoveryError):
    """No ``workspace.yaml`` (or ``.yml``) at the workspace root."""

    def __init__(self, root: Path) -> None:
        super().__init__(
            f"No workspace.yaml found at {root}. A SwarmKit workspace must "
            "declare itself with a workspace.yaml at the root."
        )
        self.root = root


class YAMLParseError(DiscoveryError):
    """A YAML file failed to parse."""

    def __init__(self, path: Path, original: yaml.YAMLError) -> None:
        mark = getattr(original, "problem_mark", None)
        line = getattr(mark, "line", None)
        suffix = f" (line {line + 1})" if line is not None else ""
        super().__init__(f"Failed to parse YAML in {path}{suffix}: {original}")
        self.path = path
        self.original = original
        self.line = line + 1 if line is not None else None


class ArtifactKindMismatchError(DiscoveryError):
    """The artifact's ``kind:`` field disagrees with its directory."""

    def __init__(self, path: Path, expected_kind: str, actual_kind: str | None) -> None:
        super().__init__(
            f"Artifact at {path} declares kind={actual_kind!r} but was found "
            f"under a directory that expects kind={expected_kind!r}."
        )
        self.path = path
        self.expected_kind = expected_kind
        self.actual_kind = actual_kind


class DeepNestingError(DiscoveryError):
    """YAML found more than one level below its category directory."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"Artifact at {path} is nested more than one level deep. "
            "Flat structure only — one level of subdirectory allowed for "
            "grouping, no deeper (design §9.3)."
        )
        self.path = path


class MalformedArtifactError(DiscoveryError):
    """The YAML parsed but the top level is not a mapping."""

    def __init__(self, path: Path, top_type: str) -> None:
        super().__init__(
            f"Artifact at {path} must be a YAML mapping at the top level, got {top_type}."
        )
        self.path = path
        self.top_type = top_type


def discover(root: str | Path) -> list[DiscoveredArtifact]:
    """Walk *root* as a SwarmKit workspace and return every discovered artifact.

    Order: ``workspace.yaml`` first, then artifacts in alphabetical path order
    grouped by directory. Sorting matters for determinism (see design note —
    byte-identical output on repeat runs).

    Raises a subclass of :class:`DiscoveryError` on any I/O, parse, or
    directory-shape issue. Schema validation happens later (M1.3); discovery
    only enforces "the files exist, parse, and live in the right directories."
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise WorkspaceNotFoundError(root_path)

    results: list[DiscoveredArtifact] = []

    # workspace.yaml / workspace.yml at the root (required, exactly one).
    ws_path = _find_workspace_file(root_path)
    ws_raw = _load_yaml(ws_path)
    _assert_kind(ws_path, "workspace", ws_raw)
    results.append(DiscoveredArtifact(path=ws_path, kind="workspace", raw=ws_raw))

    for dirname, kind in _KIND_DIRS.items():
        subdir = root_path / dirname
        if not subdir.is_dir():
            continue
        for yaml_path in _walk_yaml(subdir):
            raw = _load_yaml(yaml_path)
            _assert_kind(yaml_path, kind, raw)
            results.append(DiscoveredArtifact(path=yaml_path, kind=kind, raw=raw))

    return results


def _find_workspace_file(root: Path) -> Path:
    for name in ("workspace.yaml", "workspace.yml"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise MissingWorkspaceFileError(root)


def _walk_yaml(subdir: Path) -> Iterator[Path]:
    """Yield YAML files directly in *subdir* and in its direct subdirectories.

    Deeper nesting raises :class:`DeepNestingError`. Hidden files and
    directories (starting with ``.``) are ignored.
    """
    for entry in sorted(subdir.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("."):
            continue
        if entry.is_file():
            if entry.suffix in _YAML_SUFFIXES:
                yield entry
            continue
        if entry.is_dir():
            yield from _walk_subdir(entry)


def _walk_subdir(subdir: Path) -> Iterator[Path]:
    """Walk exactly one level deep into *subdir*; error if YAMLs are deeper."""
    for entry in sorted(subdir.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("."):
            continue
        if entry.is_file():
            if entry.suffix in _YAML_SUFFIXES:
                yield entry
            continue
        if entry.is_dir():
            # Look one more level — any YAML we find there is too deep.
            for deeper in entry.rglob("*"):
                if deeper.is_file() and deeper.suffix in _YAML_SUFFIXES:
                    raise DeepNestingError(deeper)


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise YAMLParseError(path, exc) from exc
    if not isinstance(data, dict):
        raise MalformedArtifactError(path, type(data).__name__)
    return data


def _assert_kind(path: Path, expected: ArtifactKind, raw: Mapping[str, Any]) -> None:
    expected_str = _EXPECTED_KIND_STR[expected]
    actual = raw.get("kind")
    if actual != expected_str:
        raise ArtifactKindMismatchError(path, expected_str, actual)


__all__ = [
    "ArtifactKind",
    "ArtifactKindMismatchError",
    "DeepNestingError",
    "DiscoveredArtifact",
    "DiscoveryError",
    "MalformedArtifactError",
    "MissingWorkspaceFileError",
    "WorkspaceNotFoundError",
    "YAMLParseError",
    "discover",
]
