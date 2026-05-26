"""APIKeyAuthProvider — bearer-token authentication via static key registry.

Keys are configured in workspace.yaml under ``server.auth.config.keys``.
Each entry maps a secret (resolved from ``env:VARNAME`` references) to a
client identity and scope set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from swarmkit_runtime.auth._provider import (
    AuthError,
    AuthIdentity,
    AuthProvider,
    AuthRequest,
)


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

    def __init__(self, keys: list[dict[str, Any]]) -> None:
        self._keys: dict[str, _KeyEntry] = {}
        for cfg in keys:
            raw_ref: str = cfg["key_ref"]
            secret = self._resolve_ref(raw_ref)
            if secret:
                entry = _KeyEntry(
                    secret=secret,
                    client_id=cfg["client_id"],
                    client_name=cfg.get("client_name", cfg["client_id"]),
                    scopes=frozenset(cfg.get("scopes", [])),
                )
                self._keys[secret] = entry

    @staticmethod
    def _resolve_ref(ref: str) -> str | None:
        """Resolve a key reference to a concrete secret string."""
        if ref.startswith("env:"):
            var_name = ref[4:]
            return os.environ.get(var_name)
        return ref

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
