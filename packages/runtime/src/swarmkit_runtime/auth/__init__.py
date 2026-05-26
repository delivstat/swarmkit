"""Authentication provider abstraction for the SwarmKit HTTP server.

Mirrors the GovernanceProvider pattern: a stable ABC with pluggable
implementations. The server depends on ``AuthProvider``; concrete
providers are selected by workspace config at startup.

Built-in providers:

- ``NoneAuthProvider`` — open access (default, local dev)
- ``APIKeyAuthProvider`` — bearer-token auth with static key registry
"""

from swarmkit_runtime.auth._api_key import APIKeyAuthProvider
from swarmkit_runtime.auth._none import NoneAuthProvider
from swarmkit_runtime.auth._provider import (
    AuthError,
    AuthIdentity,
    AuthProvider,
    AuthRequest,
)
from swarmkit_runtime.auth._registry import AuthProviderRegistry, default_registry

__all__ = [
    "APIKeyAuthProvider",
    "AuthError",
    "AuthIdentity",
    "AuthProvider",
    "AuthProviderRegistry",
    "AuthRequest",
    "NoneAuthProvider",
    "default_registry",
]
