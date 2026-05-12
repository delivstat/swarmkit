"""Skill registry — core operations for finding and installing skills."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SkillEntry:
    """A skill in the registry or workspace."""

    id: str
    name: str
    description: str
    category: str
    source: str
    path: Path


class SkillRegistry:
    """Provides access to reference skills and workspace skills."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = workspace_root
        self._reference_dir = self._find_reference_dir()

    def _find_reference_dir(self) -> Path | None:
        """Find the reference/skills/ directory."""
        candidates = [
            Path(__file__).resolve().parents[5] / "reference" / "skills",
            Path.cwd() / "reference" / "skills",
        ]
        for c in candidates:
            if c.is_dir():
                return c
        return None

    def list_available(self) -> list[SkillEntry]:
        """List all skills in the reference registry."""
        if self._reference_dir is None:
            return []
        return _load_skills_from_dir(self._reference_dir, source="reference")

    def list_installed(self) -> list[SkillEntry]:
        """List skills installed in the workspace."""
        if self._workspace_root is None:
            return []
        skills_dir = self._workspace_root / "skills"
        if not skills_dir.is_dir():
            return []
        return _load_skills_from_dir(skills_dir, source="workspace")

    def search(self, query: str) -> list[SkillEntry]:
        """Search available skills by keyword."""
        query_lower = query.lower()
        results = []
        for entry in self.list_available():
            text = f"{entry.id} {entry.name} {entry.description} {entry.category}"
            if query_lower in text.lower():
                results.append(entry)
        return results

    def install(self, skill_id: str) -> Path | None:
        """Copy a skill from reference registry to workspace skills/."""
        if self._workspace_root is None or self._reference_dir is None:
            return None

        source_file = self._reference_dir / f"{skill_id}.yaml"
        if not source_file.is_file():
            return None

        target_dir = self._workspace_root / "skills"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{skill_id}.yaml"

        shutil.copy2(source_file, target_file)
        return target_file

    def get(self, skill_id: str) -> SkillEntry | None:
        """Get a specific skill by ID from available or installed."""
        for entry in self.list_available():
            if entry.id == skill_id:
                return entry
        for entry in self.list_installed():
            if entry.id == skill_id:
                return entry
        return None


def _load_skills_from_dir(skills_dir: Path, source: str) -> list[SkillEntry]:
    """Load skill entries from a directory of YAML files."""
    entries = []
    for f in sorted(skills_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            meta = data.get("metadata", {})
            entries.append(
                SkillEntry(
                    id=meta.get("id", f.stem),
                    name=meta.get("name", f.stem),
                    description=meta.get("description", ""),
                    category=data.get("category", "capability"),
                    source=source,
                    path=f,
                )
            )
        except (yaml.YAMLError, OSError):
            continue
    return entries


def list_available(workspace_root: Path | None = None) -> list[SkillEntry]:
    """List all skills in the reference registry."""
    return SkillRegistry(workspace_root).list_available()


def list_installed(workspace_root: Path) -> list[SkillEntry]:
    """List skills installed in the workspace."""
    return SkillRegistry(workspace_root).list_installed()


def search_skills(query: str, workspace_root: Path | None = None) -> list[SkillEntry]:
    """Search available skills by keyword."""
    return SkillRegistry(workspace_root).search(query)


def install_skill(skill_id: str, workspace_root: Path) -> Path | None:
    """Install a skill from the reference registry to the workspace."""
    return SkillRegistry(workspace_root).install(skill_id)
