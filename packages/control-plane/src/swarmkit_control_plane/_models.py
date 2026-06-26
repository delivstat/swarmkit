"""Control-plane data model. See design/details/control-plane/11-architecture.md §5."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ConnectionMode = Literal["direct", "poll"]
Health = Literal["healthy", "stale", "unreachable", "unknown"]


@dataclass
class Instance:
    """A registered SwarmKit instance (a `swarmkit serve` deployment)."""

    id: str
    name: str
    endpoint: str
    connection: ConnectionMode = "direct"
    token_ref: str = ""  # how the panel authenticates to the instance (env:/file:/literal)
    schema_version: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    health: Health = "unknown"
    last_seen: str | None = None
    created_at: str = ""

    def public_dict(self) -> dict[str, Any]:
        """Serializable view — never exposes the token_ref value."""
        return {
            "id": self.id,
            "name": self.name,
            "endpoint": self.endpoint,
            "connection": self.connection,
            "schema_version": self.schema_version,
            "capabilities": self.capabilities,
            "health": self.health,
            "last_seen": self.last_seen,
            "created_at": self.created_at,
        }
