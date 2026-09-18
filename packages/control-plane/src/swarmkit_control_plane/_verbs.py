"""Command verbs the panel may enqueue for a poll-connected (Mode B) instance.

Each verb maps to a serve REST action and carries the transport tier it requires — the panel
authorizes an enqueue only when the verb's tier is within the instance's granted tier, and the
connector re-validates the same on the instance (defense in depth). The verb names and tiers are
mirrored by the runtime connector (`swarmkit_runtime.connect._VERB_ROUTES`); keep the two in sync.

Tiers match serve's own route→action map (`server._required_action`) and the `serve:*` scope tiers
in `auth/_scopes.py`. See design/details/control-plane/13-connector-registry.md §"Mode B".
"""

from __future__ import annotations

from typing import Any

# verb -> required serve tier (read | run | admin)
#: Membership scopes an instance may grant a fleet, lowest to highest — mirrors the runtime's
#: ``fleet.SCOPES`` (asserted equal by the contract test). ``approve-as`` (design 28) lets the
#: fleet resolve multi-party approvals as the signed-in operator it asserts.
MEMBERSHIP_SCOPES: tuple[str, ...] = ("monitor", "manage", "approve-as")

VERB_TIERS: dict[str, str] = {
    "capabilities": "read",
    "usage": "read",
    "job-status": "read",
    "validate": "read",
    "run": "run",
    "reload": "admin",
    "deploy": "admin",  # push an artifact version to local serve (Mode B governed deploy)
}

#: The args each verb needs before it is worth queueing — the connector's route template and body
#: shape (runtime ``connect.VERB_ROUTES``). Checked at enqueue so a malformed command is a 400 to
#: the operator now, not an error result reported back by the instance a poll later.
VERB_ARGS: dict[str, tuple[str, ...]] = {
    "capabilities": (),
    "usage": (),
    "job-status": ("job_id",),
    "validate": (),
    "run": ("topology_name", "body"),
    "reload": (),
    "deploy": ("kind", "id", "body"),
}


def missing_args(verb: str, args: dict[str, Any]) -> list[str]:
    """The required args *verb* lacks in *args* (``body`` must be a mapping)."""
    missing = [k for k in VERB_ARGS.get(verb, ()) if k not in args]
    if "body" in VERB_ARGS.get(verb, ()) and "body" in args and not isinstance(args["body"], dict):
        missing.append("body (must be an object)")
    return missing


_TIER_RANK: dict[str, int] = {"read": 0, "run": 1, "admin": 2}


def tier_rank(tier: str) -> int:
    """Rank of a tier (read<run<admin); unknown tiers rank -1 (deny everything)."""
    return _TIER_RANK.get(tier.strip().lower(), -1)


def is_known_verb(verb: str) -> bool:
    return verb in VERB_TIERS


def verb_within_tier(verb: str, granted_tier: str) -> bool:
    """True if *verb* is a known verb whose required tier is within *granted_tier*."""
    required = VERB_TIERS.get(verb)
    if required is None:
        return False
    return tier_rank(granted_tier) >= tier_rank(required)
