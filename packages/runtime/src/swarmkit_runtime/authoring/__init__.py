"""Conversational authoring.

After #1045, the authoring agent is the bundled ``swarmkit:author:*`` topology under
``swarmkit_runtime/authoring_workspace`` — every entry point (CLI, serve, dots app)
reaches it through ``WorkspaceRuntime.run``. This package now holds only the
namespace resolver and the YAML prompt loader. The legacy custom tool-use loop
(``_agent.py``, ``_tools.py``) was deleted in that same tracker.
"""

from __future__ import annotations

from ._prompts import AuthoringMode, get_system_prompt
from ._resolver import (
    AUTHORING_MODES,
    AUTHORING_NAMESPACE,
    authoring_bare_name,
    authoring_public_ids,
    get_authoring_workspace_path,
    is_authoring_id,
    is_reserved_namespace,
    resolve_authoring_topology,
)

__all__ = [
    "AUTHORING_MODES",
    "AUTHORING_NAMESPACE",
    "AuthoringMode",
    "authoring_bare_name",
    "authoring_public_ids",
    "get_authoring_workspace_path",
    "get_system_prompt",
    "is_authoring_id",
    "is_reserved_namespace",
    "resolve_authoring_topology",
]
