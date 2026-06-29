"""Control-plane data model. See design/details/control-plane/11-architecture.md §5."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ConnectionMode = Literal["direct", "poll"]
Health = Literal["healthy", "stale", "unreachable", "unknown"]
CommandStatus = Literal["queued", "dispatched", "done", "error"]


@dataclass
class Instance:
    """A registered SwarmKit instance (a `swarmkit serve` deployment)."""

    id: str
    name: str
    endpoint: str
    connection: ConnectionMode = "direct"
    token_ref: str = ""  # how the panel authenticates to the instance (env:/file:/literal)
    tier: str = "read"  # granted transport tier — bounds which commands may be enqueued (Mode B)
    token_fingerprint: str = ""  # short display id of the active minted token (never the secret)
    # full SHA-256 of the connector→panel token; for auth lookup, never exposed via public_dict
    token_hash: str = ""
    token_minted_at: str | None = None
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
            "tier": self.tier,
            "token_fingerprint": self.token_fingerprint,
            "token_minted_at": self.token_minted_at,
            "schema_version": self.schema_version,
            "capabilities": self.capabilities,
            "health": self.health,
            "last_seen": self.last_seen,
            "created_at": self.created_at,
        }


@dataclass
class Command:
    """A queued command for a poll-connected (Mode B) instance to execute against local serve."""

    cmd_id: str
    instance_id: str
    verb: str
    args: dict[str, Any] = field(default_factory=dict)
    status: CommandStatus = "queued"
    output: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = ""
    dispatched_at: str | None = None
    result_at: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "cmd_id": self.cmd_id,
            "instance_id": self.instance_id,
            "verb": self.verb,
            "args": self.args,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "created_at": self.created_at,
            "dispatched_at": self.dispatched_at,
            "result_at": self.result_at,
        }

    def dispatch_dict(self) -> dict[str, Any]:
        """The minimal shape handed to the connector in a poll response."""
        return {"cmd_id": self.cmd_id, "verb": self.verb, "args": self.args}
