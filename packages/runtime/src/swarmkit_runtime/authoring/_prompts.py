"""System prompts for the authoring agent — loader for the bundled authoring workspace.

Each authoring mode (init, topology, skill, archetype, mcp-server) carries its charter
as the ``agents.root.prompt.system`` block on the bundled topology YAML under
``swarmkit_runtime.authoring_workspace.topologies.<mode>.yaml``. This module is a thin
loader that reads the YAML and returns the composed system prompt.

Keeping the prompts in YAML means operators can override them via workspace overlays
without a runtime rebuild, and the exact text that steers the author is reviewable in
version control as data rather than a 200-line Python string literal.

See ``design/details/author-bundled-workspace.md`` (#1045).
"""

from __future__ import annotations

from functools import cache
from typing import Literal

import yaml

from ._resolver import resolve_authoring_topology

AuthoringMode = Literal["init", "topology", "skill", "archetype", "mcp-server"]


@cache
def _load_mode_prompt(mode: AuthoringMode) -> str:
    """Load the raw ``prompt.system`` for ``mode`` from its bundled topology YAML."""
    topology_id = f"swarmkit:author:{mode}"
    topology_file = resolve_authoring_topology(topology_id)
    with topology_file.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    try:
        prompt = doc["agents"]["root"]["prompt"]["system"]
    except (KeyError, TypeError) as exc:  # pragma: no cover — packaging bug
        msg = (
            f"bundled authoring topology {topology_file} is missing "
            "agents.root.prompt.system; this is a packaging bug"
        )
        raise RuntimeError(msg) from exc
    if not isinstance(prompt, str) or not prompt.strip():
        msg = f"bundled authoring topology {topology_file} has an empty prompt"
        raise RuntimeError(msg)
    return prompt


def get_system_prompt(mode: AuthoringMode, workspace_context: str = "") -> str:
    """Build the system prompt for the given authoring mode.

    Reads the composed charter (core + mode-specific guidance + CLI facts) from the
    bundled topology YAML and appends the dynamic workspace snapshot if provided.
    """
    prompt = _load_mode_prompt(mode)
    if workspace_context:
        prompt = f"{prompt}\n\nExisting workspace state:\n{workspace_context}"
    return prompt
