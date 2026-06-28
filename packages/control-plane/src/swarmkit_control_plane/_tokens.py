"""Per-instance token minting (panel side).

The panel mints a per-instance, per-tier **serve API token** and shows the operator a ready-to-paste
``server.auth`` snippet — the token appears once and is never persisted. The panel stores only a
reference (env-var name) + a fingerprint + metadata. The minted secret is what the panel later sends
as a bearer to the instance's serve, and what the operator installs on that serve as a ``key_ref``.

The snippet shape mirrors the runtime's ``swarmkit auth token`` helper and the ``server.auth``
schema (doc 12 §5/§6); keep them in sync. See design/details/control-plane/12-auth.md and
13-connector-registry.md.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

_TIERS = ("read", "run", "admin")


def fingerprint(token: str) -> str:
    """Short, non-reversible identifier for a token (for rotation/audit, never the secret)."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def env_var_for(instance_id: str) -> str:
    """Suggested env-var name holding the secret, on both the instance and the panel."""
    return f"SWARMKIT_SERVE_TOKEN_{instance_id.upper()}"


@dataclass
class MintedToken:
    """A freshly minted token. ``token`` is shown once; only the rest is persisted."""

    token: str
    client_id: str
    client_name: str
    tier: str
    key_ref: str  # env:VAR — what both sides reference, never the literal
    fingerprint: str

    def server_auth_snippet(self) -> str:
        """The ``server.auth`` YAML to paste on the instance's workspace.yaml."""
        return (
            "server:\n"
            "  auth:\n"
            "    provider: api_key\n"
            "    config:\n"
            "      keys:\n"
            f"        - key_ref: {self.key_ref}\n"
            f"          client_id: {self.client_id}\n"
            f"          client_name: {self.client_name}\n"
            f"          tier: {self.tier}\n"
        )


def mint_token(instance_id: str, *, tier: str, client_name: str = "") -> MintedToken:
    """Generate a strong per-instance token bound to *tier*. Nothing is stored here."""
    if tier not in _TIERS:
        raise ValueError(f"invalid tier '{tier}' — use {' | '.join(_TIERS)}")
    token = secrets.token_urlsafe(32)
    client_id = f"control-plane-{instance_id}"
    return MintedToken(
        token=token,
        client_id=client_id,
        client_name=client_name or "Fleet control plane",
        tier=tier,
        key_ref=f"env:{env_var_for(instance_id)}",
        fingerprint=fingerprint(token),
    )
