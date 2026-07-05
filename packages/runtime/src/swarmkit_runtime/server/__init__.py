"""SwarmKit HTTP server — persistent mode wrapping WorkspaceRuntime.

A FastAPI application that loads a workspace at startup and exposes topology execution,
validation, and introspection over HTTP. The second interface over ``WorkspaceRuntime``
(the CLI is the first; the v1.1 web UI will be the third). See design §14.1.

This package was split out of a single 1433-line ``server.py`` (PR-I2): ``_config`` /
``_schemas`` / ``_jobs`` / ``_helpers`` / ``_mcp`` hold the value types + shared helpers,
``_routes_*`` register the endpoints, and ``_app`` is the factory. Import surface is
unchanged — ``create_app``, ``ServerCfg`` and ``_required_action`` stay importable here.
"""

from __future__ import annotations

from ._app import create_app
from ._config import ServerCfg
from ._helpers import _build_capabilities, _required_action
from ._jobs import Job, JobStore, execute_job

__all__ = [
    "Job",
    "JobStore",
    "ServerCfg",
    "_build_capabilities",
    "_required_action",
    "create_app",
    "execute_job",
]
