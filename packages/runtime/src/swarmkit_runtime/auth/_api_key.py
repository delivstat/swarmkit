"""APIKeyAuthProvider — bearer-token authentication via static key registry.

Keys are configured in workspace.yaml under ``server.auth.config.keys``.
Each entry maps a secret (resolved from ``env:VARNAME`` references) to a
client identity and scope set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from swarmkit_runtime.auth._provider import (
    AuthError,
    AuthIdentity,
    AuthProvider,
    AuthRequest,
)
from swarmkit_runtime.auth._scopes import expand_tier, reserved_violations
from swarmkit_runtime.auth._secrets import resolve_secret_ref


@dataclass(frozen=True)
class _KeyEntry:
    """Resolved API key with its associated identity."""

    secret: str
    client_id: str
    client_name: str
    scopes: frozenset[str]


class APIKeyAuthProvider(AuthProvider):
    """Authenticate via ``Authorization: Bearer <key>`` header.

    Constructor takes a list of key config dicts::

        [
            {
                "key_ref": "env:MY_API_KEY",
                "client_id": "ci-bot",
                "client_name": "CI Bot",
                "scopes": ["topologies:run", "jobs:read"],
            }
        ]

    ``key_ref`` values prefixed with ``env:`` are resolved from environment
    variables at init time. Plain strings are used as-is.
    """

    def __init__(
        self, keys: list[dict[str, Any]], credentials: dict[str, Any] | None = None
    ) -> None:
        self._keys: dict[str, _KeyEntry] = {}
        for cfg in keys:
            raw_ref: str = cfg["key_ref"]
            # Scopes come from a tier (read|run|admin) or explicit scopes — not both.
            tier = cfg.get("tier")
            scopes = expand_tier(tier) if tier else frozenset(cfg.get("scopes", []))
            # A transport token must never carry a reserved-for-human governance scope (§8.7).
            violations = reserved_violations(scopes)
            if violations:
                raise ValueError(
                    f"API key '{cfg.get('client_id', '?')}' grants reserved governance "
                    f"scope(s) {sorted(violations)} — transport tokens hold only serve:* scopes."
                )
            secret = resolve_secret_ref(raw_ref, credentials)
            if secret:
                entry = _KeyEntry(
                    secret=secret,
                    client_id=cfg["client_id"],
                    client_name=cfg.get("client_name", cfg["client_id"]),
                    scopes=scopes,
                )
                self._keys[secret] = entry

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        token = self._extract_bearer(request.headers)
        if token is None:
            raise AuthError("Missing or invalid API key", 401)

        entry = self._keys.get(token)
        if entry is None:
            raise AuthError("Missing or invalid API key", 401)

        return AuthIdentity(
            client_id=entry.client_id,
            client_name=entry.client_name,
            provider="api_key",
            scopes=entry.scopes,
        )

    async def authorize(self, identity: AuthIdentity, resource: str, action: str) -> bool:
        if "*" in identity.scopes:
            return True
        required = f"{resource}:{action}"
        return required in identity.scopes

    @property
    def mode(self) -> str:
        return "api_key"

    @staticmethod
    def _extract_bearer(headers: dict[str, str]) -> str | None:
        """Extract bearer token from Authorization header."""
        auth = headers.get("authorization") or headers.get("Authorization")
        if auth is None:
            return None
        parts = auth.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1]
