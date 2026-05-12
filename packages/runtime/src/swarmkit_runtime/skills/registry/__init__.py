"""Skill registry — find, install, and import community skills.

Three-layer model:
  1. Community sources (remote repos: Agent Skills, MCP servers)
  2. Local registry (reference/skills/ bundled with swarmkit-runtime)
  3. Workspace skills (skills/ in the workspace directory)

See design/details/skill-registry.md.
"""

from swarmkit_runtime.skills.registry._registry import (
    SkillEntry,
    SkillRegistry,
    install_skill,
    list_available,
    list_installed,
    search_skills,
)

__all__ = [
    "SkillEntry",
    "SkillRegistry",
    "install_skill",
    "list_available",
    "list_installed",
    "search_skills",
]
