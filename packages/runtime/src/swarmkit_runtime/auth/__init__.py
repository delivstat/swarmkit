"""Authentication provider abstraction for the SwarmKit HTTP server.

Mirrors the GovernanceProvider pattern: a stable ABC with pluggable
implementations. The server depends on ``AuthProvider``; concrete
providers are selected by workspace config at startup.

Built-in providers:

- ``NoneAuthProvider`` — open access (default, local dev)
- ``APIKeyAuthProvider`` — bearer-token auth with static key registry
- ``JWTAuthProvider`` — JWT bearer tokens with JWKS auto-discovery
"""

from swarmkit_runtime.auth._api_key import APIKeyAuthProvider
from swarmkit_runtime.auth._jwt import JWTAuthProvider
from swarmkit_runtime.auth._none import NoneAuthProvider
from swarmkit_runtime.auth._provider import (
    AuthError,
    AuthIdentity,
    AuthProvider,
    AuthRequest,
)
from swarmkit_runtime.auth._registry import AuthProviderRegistry, default_registry
from swarmkit_runtime.auth._scopes import (
    RESERVED_SCOPES,
    SERVE_ADMIN,
    SERVE_READ,
    SERVE_RUN,
    expand_tier,
    reserved_violations,
)

__all__ = [
    "RESERVED_SCOPES",
    "SERVE_ADMIN",
    "SERVE_READ",
    "SERVE_RUN",
    "APIKeyAuthProvider",
    "AuthError",
    "AuthIdentity",
    "AuthProvider",
    "AuthProviderRegistry",
    "AuthRequest",
    "JWTAuthProvider",
    "NoneAuthProvider",
    "default_registry",
    "expand_tier",
    "reserved_violations",
]
