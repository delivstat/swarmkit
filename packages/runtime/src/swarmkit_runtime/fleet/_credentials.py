"""Credential primitives for fleet enrollment — opaque tokens + hashes (design 19).

Same construction the panel uses for per-instance tokens (``_tokens`` there): a high-entropy
url-safe secret, stored only as a full SHA-256 hash (the secret is high-entropy, so no salt is
needed), with a short fingerprint for display. Two credential kinds share these helpers:

* **enrollment token** — one-time, short-TTL, authorises a join;
* **membership credential** — the long-lived scoped API key a fleet uses to call this instance.

Nothing here persists — the store hashes and records; the secret is returned once.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Literal

#: Membership scope: observe-only vs may-deploy. ``manage`` is human-issued (enforced at the route).
Scope = Literal["monitor", "manage"]

SCOPES: tuple[Scope, ...] = ("monitor", "manage")


def mint_secret() -> str:
    """A fresh high-entropy url-safe secret (~256 bits). Shown once; only its hash is stored."""
    return secrets.token_urlsafe(32)


def secret_hash(secret: str) -> str:
    """Full SHA-256 hex of a secret — what the store keeps for authentication."""
    return hashlib.sha256(secret.encode()).hexdigest()


def fingerprint(secret: str) -> str:
    """Short display id for a secret (audit/rotation UI); never used to authenticate."""
    return secret_hash(secret)[:12]


@dataclass(frozen=True)
class Membership:
    """One fleet's binding to this instance — the panel-facing half of an enrollment."""

    membership_id: str
    fleet_id: str
    scope: Scope
    key_fingerprint: str
    created_at: str
    expires_at: str | None = None
