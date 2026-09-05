"""The one place a credential is resolved.

`design/details/credential-service.md`. Before this, three resolvers: `auth/_secrets.py` for
serve-auth key refs, `mcp/_credentials.py` for MCP servers, and a refresh hook bolted onto
`WorkspaceRuntime.run()`. Each entry point had to remember what to call, and the one nobody thought
of — `serve`, the long-lived process where tokens actually expire — remembered nothing.

**Refresh is not a step here. It is a property of resolution.** `resolve()` returns a *valid*
secret: for an `oauth` credential that means refreshing first when the stored token is inside its
expiry window. No caller asks for a refresh, so no caller can forget, and an entry point added
later inherits the behaviour by resolving a credential like everything else.
"""

from swarmkit_runtime.credentials._service import (
    ConsentRequired,
    CredentialError,
    CredentialService,
)

__all__ = ["ConsentRequired", "CredentialError", "CredentialService"]
