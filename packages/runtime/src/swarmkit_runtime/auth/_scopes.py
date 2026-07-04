"""Transport scope tiers + the reserved-scope guard for serve auth.

Transport scopes (``serve:*``) gate which serve routes a caller may hit. They are a SEPARATE
namespace from governance/IAM scopes — a transport token must never carry a governance scope, so it
structurally cannot grant reserved-for-human capabilities. See
design/details/control-plane/12-auth.md and 05-identity-governance-iam.md (§8.7).
"""

from __future__ import annotations

SERVE_READ = "serve:read"
SERVE_RUN = "serve:run"
SERVE_ADMIN = "serve:admin"

# A tier expands to its cumulative serve:* scopes.
_TIER_SCOPES: dict[str, frozenset[str]] = {
    "read": frozenset({SERVE_READ}),
    "run": frozenset({SERVE_READ, SERVE_RUN}),
    "admin": frozenset({SERVE_READ, SERVE_RUN, SERVE_ADMIN}),
}

# Reserved-for-human governance scopes — never grantable via a transport token (design §8.7).
RESERVED_SCOPES = frozenset(
    {
        "skills:activate",
        "skills:write_pending",
        "mcp_servers:deploy",
        "mcp_servers:scaffold",
        "topologies:modify",
        "iam:modify",
        "audit:modify",
    }
)


def expand_tier(tier: str) -> frozenset[str]:
    """Expand a tier name (read|run|admin) to its serve:* scopes; empty for unknown."""
    return _TIER_SCOPES.get(tier.strip().lower(), frozenset())


def reserved_violations(scopes: frozenset[str]) -> frozenset[str]:
    """Return any reserved governance scopes present in *scopes* (incl. the audit:* family and
    the wildcard). A transport (api_key/JWT) token must never carry ``*`` — the authorize
    fast-path treats it as god-mode, bypassing the tier model + reserved-scope guard. Only the
    built-in NoneAuthProvider (which never flows through here) may hold ``*``."""
    bad = set(scopes) & RESERVED_SCOPES
    bad |= {s for s in scopes if s.startswith("audit:")}
    bad |= {s for s in scopes if s == "*"}
    return frozenset(bad)
