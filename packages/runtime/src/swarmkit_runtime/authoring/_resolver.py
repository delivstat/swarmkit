"""Namespace resolver for the bundled authoring workspace.

The bundled authoring workspace ships inside the ``swarmkit-runtime`` package as
``swarmkit_runtime.authoring_workspace`` and is reached from a user workspace via the
reserved ``swarmkit:author:*`` topology id namespace. See
``design/details/author-bundled-workspace.md`` (#1045).

This module is the one source of truth for:

* where the bundled workspace lives on disk at runtime,
* which topology ids resolve to it,
* which prefix is reserved (and therefore refused when a user workspace tries to
  register an id under it).

Nothing in this module imports the model provider, the compiler, or the CLI — it is
pure path + string resolution, safe to import from any layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import swarmkit_runtime.authoring_workspace as _bundled_workspace

#: The reserved namespace prefix. Any topology / skill / archetype id starting with this
#: string is routed to the bundled authoring workspace. User workspaces are rejected if
#: they try to register an id under this prefix.
AUTHORING_NAMESPACE: Final[str] = "swarmkit:author:"

#: The modes the bundled workspace ships. These map 1:1 to files under
#: ``authoring_workspace/topologies/<mode>.yaml``.
AUTHORING_MODES: Final[tuple[str, ...]] = (
    "topology",
    "skill",
    "archetype",
    "mcp-server",
    "init",
)


def get_authoring_workspace_path() -> Path:
    """Return the on-disk path of the bundled authoring workspace.

    Resolves the ``swarmkit_runtime.authoring_workspace`` package to its on-disk
    directory. Works for editable installs and standard wheel installs; zipapp installs
    (which never put the workspace on a filesystem) are not supported and would need
    the installer to materialise it first.
    """
    pkg_file = _bundled_workspace.__file__
    if pkg_file is None:
        msg = (
            "swarmkit_runtime.authoring_workspace has no __file__ — the bundled "
            "authoring workspace needs to be installed to a real filesystem path"
        )
        raise RuntimeError(msg)
    return Path(pkg_file).parent


def is_authoring_id(topology_id: str) -> bool:
    """True if ``topology_id`` is routed to the bundled authoring workspace."""
    return topology_id.startswith(AUTHORING_NAMESPACE)


def is_reserved_namespace(id_: str) -> bool:
    """True if ``id_`` falls under the reserved ``swarmkit:*`` namespace.

    Broader than :func:`is_authoring_id` on purpose — ``swarmkit:*`` as a whole is
    reserved for the runtime to carve out future bundled surfaces (authoring is the
    first). The id validator should refuse any user-defined id matching this.
    """
    return id_.startswith("swarmkit:")


def resolve_authoring_topology(topology_id: str) -> Path:
    """Return the path of the bundled topology file for ``topology_id``.

    Raises ``ValueError`` if the id is not under the authoring namespace or does not
    name a known mode. The returned path is not guaranteed to exist — the caller should
    treat a missing file as a packaging bug and surface it clearly.
    """
    return (
        get_authoring_workspace_path() / "topologies" / f"{authoring_bare_name(topology_id)}.yaml"
    )


def authoring_bare_name(topology_id: str) -> str:
    """Translate ``swarmkit:author:<mode>`` to its bare topology name.

    The schema's id pattern (``^[a-z][a-z0-9-]*$``) refuses colons on purpose — a user
    workspace cannot register an id under the ``swarmkit:`` namespace. The bundled YAMLs
    therefore use the bare mode name (``topology``, ``skill``, …) and this function
    bridges the external public id to the internal name the runtime looks up.
    """
    if not is_authoring_id(topology_id):
        msg = f"not an authoring topology id: {topology_id!r}"
        raise ValueError(msg)
    mode = topology_id[len(AUTHORING_NAMESPACE) :]
    if mode not in AUTHORING_MODES:
        modes = ", ".join(AUTHORING_MODES)
        msg = f"unknown authoring mode {mode!r}; expected one of: {modes}"
        raise ValueError(msg)
    return mode


def authoring_public_ids() -> tuple[str, ...]:
    """Return the public ``swarmkit:author:*`` ids clients see over the serve API."""
    return tuple(f"{AUTHORING_NAMESPACE}{mode}" for mode in AUTHORING_MODES)
