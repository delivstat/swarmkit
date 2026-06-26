"""SwarmKit control plane — connect, observe, and manage multiple SwarmKit instances.

A separate, self-hostable application (design decision D1) that talks to each instance's
`swarmkit serve` over the authenticated REST contract. This package is the panel API +
instance registry; the fleet UI is a separate package.

See design/details/control-plane/ (11-architecture, 13-connector-registry).
"""

from swarmkit_control_plane._app import create_app
from swarmkit_control_plane._models import Instance
from swarmkit_control_plane._registry import SqliteRegistry

__all__ = ["Instance", "SqliteRegistry", "create_app"]
