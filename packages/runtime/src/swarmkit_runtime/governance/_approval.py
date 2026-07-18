"""Multi-party approval evaluation (design/details/multi-party-approval.md).

Pure logic — no I/O, no LangGraph, no GovernanceProvider. Given a role registry, a
per-gate approval policy, and the resolutions collected so far, it decides whether
the gate is satisfied and which role-tasks are still outstanding. The enforcement +
gate-wiring layer (a later slice) calls into this; keeping it pure makes the
quorum semantics exhaustively unit-testable.

Model (per the design):
  - A gate fans out into one task per (rule, role) — a *role-task*, filled by any
    member of that role.
  - Quorum is counted over completed role-tasks: ``all`` = every role in the rule;
    ``any`` = one; ``k-of N`` = N *distinct* identities.
  - ``min_distinct_approvers`` is an orthogonal four-eyes floor over distinct
    identities across the whole gate.
  - ``exclude_author`` bars the submitter from approving (segregation of duties).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

# ---- domain -----------------------------------------------------------------


@dataclass(frozen=True)
class Role:
    """A role: the identities that hold it and the scopes it confers (RBAC)."""

    id: str
    members: frozenset[str]
    scopes: frozenset[str]


@dataclass(frozen=True)
class RoleRegistry:
    """Workspace roles, keyed by id."""

    roles: Mapping[str, Role]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RoleRegistry:
        """Build from a schema-validated ``RoleRegistry`` artifact dict."""
        roles: dict[str, Role] = {}
        for r in data.get("roles", []):
            roles[r["id"]] = Role(
                id=r["id"],
                members=frozenset(r.get("members", [])),
                scopes=frozenset(r.get("scopes", [])),
            )
        return cls(roles=roles)

    def get(self, role_id: str) -> Role | None:
        return self.roles.get(role_id)

    def confers(self, role_id: str, scope: str) -> bool:
        role = self.roles.get(role_id)
        return role is not None and scope in role.scopes

    def is_member(self, role_id: str, identity: str) -> bool:
        role = self.roles.get(role_id)
        return role is not None and identity in role.members


@dataclass(frozen=True)
class KOf:
    """Quorum: any ``k`` *distinct* identities from the role group."""

    k: int


Quorum = Literal["all", "any"] | KOf


@dataclass(frozen=True)
class Rule:
    """One approval rule: a scope exercised by a group of roles, at a quorum."""

    scope: str
    roles: tuple[str, ...]
    quorum: Quorum


@dataclass(frozen=True)
class ApprovalPolicy:
    """A per-gate approval policy. The gate advances only when every rule is met."""

    rules: tuple[Rule, ...]
    exclude_author: bool = True
    on_revision: Literal["reset_all", "reconfirm_changed"] = "reset_all"
    min_distinct_approvers: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ApprovalPolicy:
        """Build from a schema-validated ``approval-policy`` dict."""
        rules = tuple(
            Rule(
                scope=r["scope"],
                roles=tuple(r["roles"]),
                quorum=_quorum_from_json(r["quorum"]),
            )
            for r in data["rules"]
        )
        return cls(
            rules=rules,
            exclude_author=data.get("exclude_author", True),
            on_revision=data.get("on_revision", "reset_all"),
            min_distinct_approvers=data.get("min_distinct_approvers"),
        )


def _quorum_from_json(value: Any) -> Quorum:
    if value == "all":
        return "all"
    if value == "any":
        return "any"
    if isinstance(value, Mapping) and "k-of" in value:
        return KOf(int(value["k-of"]))
    raise ValueError(f"malformed quorum: {value!r}")


Outcome = Literal["approve", "changes-requested", "reject"]


@dataclass(frozen=True)
class Resolution:
    """One role-task resolution by a human identity, in a named role, for a scope."""

    identity: str
    role: str
    scope: str
    outcome: Outcome = "approve"


@dataclass(frozen=True)
class RoleTask:
    """A single approval task: role ``role`` must approve rule ``rule_index`` (scope)."""

    rule_index: int
    scope: str
    role: str


class GateStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes-requested"
    REJECTED = "rejected"


@dataclass(frozen=True)
class GateEvaluation:
    status: GateStatus
    outstanding: tuple[RoleTask, ...]
    satisfied_rules: tuple[int, ...]
    distinct_approvers: frozenset[str]


# ---- evaluation -------------------------------------------------------------


def tasks(policy: ApprovalPolicy) -> tuple[RoleTask, ...]:
    """Decompose a policy into one role-task per (rule, role)."""
    return tuple(
        RoleTask(rule_index=i, scope=rule.scope, role=role)
        for i, rule in enumerate(policy.rules)
        for role in rule.roles
    )


def resolution_error(
    registry: RoleRegistry,
    policy: ApprovalPolicy,
    res: Resolution,
    author: str | None = None,
) -> str | None:
    """Return why ``res`` is not a structurally valid resolution, or None if it is.

    Valid means: the role exists and confers the rule's scope, the identity is a
    member of that role, there is a rule pairing this (scope, role), and — when
    ``exclude_author`` — the identity is not the author. (Human-vs-agent identity
    is enforced one layer up; this engine assumes an authenticated human.)
    """
    if registry.get(res.role) is None:
        return f"unknown role: {res.role}"
    if not any(res.scope == rule.scope and res.role in rule.roles for rule in policy.rules):
        return f"role {res.role} is not asked to approve scope {res.scope}"
    if not registry.confers(res.role, res.scope):
        return f"role {res.role} does not confer scope {res.scope}"
    if not registry.is_member(res.role, res.identity):
        return f"{res.identity} is not a member of role {res.role}"
    if policy.exclude_author and author is not None and res.identity == author:
        return f"author {res.identity} cannot approve their own artifact"
    return None


def _rule_satisfied(rule: Rule, completed: dict[str, str]) -> bool:
    """``completed`` maps role -> completing identity for roles in this rule."""
    if rule.quorum == "all":
        return all(role in completed for role in rule.roles)
    if rule.quorum == "any":
        return any(role in completed for role in rule.roles)
    if isinstance(rule.quorum, KOf):
        return len(set(completed.values())) >= rule.quorum.k
    raise ValueError(f"unknown quorum: {rule.quorum!r}")


def evaluate(
    policy: ApprovalPolicy,
    registry: RoleRegistry,
    resolutions: Iterable[Resolution],
    author: str | None = None,
) -> GateEvaluation:
    """Evaluate a gate's status given the resolutions collected so far.

    Only structurally valid resolutions count (invalid ones are ignored — the
    enforcement layer rejects them at submission). A reject is terminal; a
    changes-request routes to rework; otherwise the gate is APPROVED once every
    rule's quorum and any ``min_distinct_approvers`` floor are met, else PENDING.
    """
    valid = [r for r in resolutions if resolution_error(registry, policy, r, author) is None]

    if any(r.outcome == "reject" for r in valid):
        return GateEvaluation(GateStatus.REJECTED, (), (), frozenset())
    if any(r.outcome == "changes-requested" for r in valid):
        return GateEvaluation(GateStatus.CHANGES_REQUESTED, (), (), frozenset())

    approvals = [r for r in valid if r.outcome == "approve"]

    outstanding: list[RoleTask] = []
    satisfied_rules: list[int] = []
    approvers: set[str] = set()

    for i, rule in enumerate(policy.rules):
        # role -> first completing identity (any member completes a role-task)
        completed: dict[str, str] = {}
        for role in rule.roles:
            for a in approvals:
                if a.role == role and a.scope == rule.scope:
                    completed.setdefault(role, a.identity)
                    break
            if role not in completed:
                outstanding.append(RoleTask(rule_index=i, scope=rule.scope, role=role))
        approvers.update(completed.values())
        if _rule_satisfied(rule, completed):
            satisfied_rules.append(i)

    all_rules_met = len(satisfied_rules) == len(policy.rules)
    floor = policy.min_distinct_approvers
    floor_met = floor is None or len(approvers) >= floor

    if all_rules_met and floor_met:
        status = GateStatus.APPROVED
        outstanding = []
    else:
        status = GateStatus.PENDING

    return GateEvaluation(
        status=status,
        outstanding=tuple(outstanding),
        satisfied_rules=tuple(satisfied_rules),
        distinct_approvers=frozenset(approvers),
    )


def after_revision(
    resolutions: Iterable[Resolution],
    policy: ApprovalPolicy,
    changed_scopes: Iterable[str] = (),
) -> tuple[Resolution, ...]:
    """Apply ``on_revision`` to prior approvals when the artifact is revised.

    ``reset_all`` invalidates every prior approval; ``reconfirm_changed`` keeps
    approvals whose scope was not among ``changed_scopes``.
    """
    if policy.on_revision == "reset_all":
        return ()
    changed = set(changed_scopes)
    return tuple(r for r in resolutions if r.scope not in changed)
