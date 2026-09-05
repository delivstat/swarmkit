"""OAuth for remote MCP servers — `design/details/mcp-oauth.md`.

A connection binds to the `mcp_servers` entry, and its credential has one owner fixed at setup.
The login happens once; the refresh token carries it headlessly from then on.
"""

from swarmkit_runtime.oauth._pkce import (
    PendingLogin,
    PendingLogins,
    authorization_url,
    challenge_for,
    discover_metadata,
    exchange_code,
    generate_verifier,
    refresh_token,
    register_client,
)
from swarmkit_runtime.oauth._refresh import (
    DEFAULT_RUN_WINDOW_S,
    ConsentRequired,
    RefreshOutcome,
    expiring_soon,
    refresh_credential,
    refresh_for_run,
)
from swarmkit_runtime.oauth._secret_box import KEY_ENV, SecretBox, SecretBoxError
from swarmkit_runtime.oauth._store import EXPIRY_SKEW_S, TokenMetadata, TokenStore

__all__ = [
    "DEFAULT_RUN_WINDOW_S",
    "EXPIRY_SKEW_S",
    "KEY_ENV",
    "ConsentRequired",
    "PendingLogin",
    "PendingLogins",
    "RefreshOutcome",
    "SecretBox",
    "SecretBoxError",
    "TokenMetadata",
    "TokenStore",
    "authorization_url",
    "challenge_for",
    "discover_metadata",
    "exchange_code",
    "expiring_soon",
    "generate_verifier",
    "refresh_credential",
    "refresh_for_run",
    "refresh_token",
    "register_client",
]
