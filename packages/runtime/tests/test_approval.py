"""Multi-party approval evaluation engine (design/details/multi-party-approval.md).

Exhaustive unit tests for the pure quorum semantics: all/any/k-of, per-role tasks,
distinct identities, overlapping roles, exclude_author, min_distinct_approvers,
on_revision, and structural validation. No runtime wiring is exercised here.
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.governance._approval import (
    ApprovalPolicy,
    GateStatus,
    KOf,
    Resolution,
    Role,
    RoleRegistry,
    Rule,
    after_revision,
    evaluate,
    resolution_error,
    tasks,
)

# ---- fixtures ----------------------------------------------------------------

REGISTRY = RoleRegistry(
    roles={
        "oms-lead": Role(
            "oms-lead", frozenset({"alice"}), frozenset({"design:approve", "code:approve"})
        ),
        "web-lead": Role("web-lead", frozenset({"bob"}), frozenset({"design:approve"})),
        "mobile-lead": Role("mobile-lead", frozenset({"carol"}), frozenset({"design:approve"})),
        "infosec-lead": Role("infosec-lead", frozenset({"dana"}), frozenset({"security:approve"})),
        # a reviewer pool + a person (frank) who holds two of them (overlap)
        "rev-a": Role("rev-a", frozenset({"erin"}), frozenset({"design:approve"})),
        "rev-b": Role("rev-b", frozenset({"frank"}), frozenset({"design:approve"})),
        "rev-c": Role("rev-c", frozenset({"frank"}), frozenset({"design:approve"})),
        # a two-member role
        "eng-manager": Role(
            "eng-manager", frozenset({"grace", "heidi"}), frozenset({"release:approve"})
        ),
    }
)


def approve(identity: str, role: str, scope: str) -> Resolution:
    return Resolution(identity=identity, role=role, scope=scope, outcome="approve")


# ---- quorum: all -------------------------------------------------------------


def test_all_needs_every_role() -> None:
    policy = ApprovalPolicy(
        rules=(Rule("design:approve", ("oms-lead", "web-lead", "mobile-lead"), "all"),)
    )
    # two of three -> pending, with mobile-lead outstanding
    ev = evaluate(
        policy,
        REGISTRY,
        [
            approve("alice", "oms-lead", "design:approve"),
            approve("bob", "web-lead", "design:approve"),
        ],
    )
    assert ev.status is GateStatus.PENDING
    assert [t.role for t in ev.outstanding] == ["mobile-lead"]
    # all three -> approved
    ev = evaluate(
        policy,
        REGISTRY,
        [
            approve("alice", "oms-lead", "design:approve"),
            approve("bob", "web-lead", "design:approve"),
            approve("carol", "mobile-lead", "design:approve"),
        ],
    )
    assert ev.status is GateStatus.APPROVED
    assert ev.outstanding == ()


def test_any_one_role_suffices() -> None:
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("oms-lead", "web-lead"), "any"),))
    ev = evaluate(policy, REGISTRY, [approve("bob", "web-lead", "design:approve")])
    assert ev.status is GateStatus.APPROVED


# ---- quorum: k-of (distinct identities) --------------------------------------


def test_k_of_needs_n_distinct_identities() -> None:
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("rev-a", "rev-b", "rev-c"), KOf(2)),))
    # one approver -> pending
    ev = evaluate(policy, REGISTRY, [approve("erin", "rev-a", "design:approve")])
    assert ev.status is GateStatus.PENDING
    # two distinct people -> approved
    ev = evaluate(
        policy,
        REGISTRY,
        [approve("erin", "rev-a", "design:approve"), approve("frank", "rev-b", "design:approve")],
    )
    assert ev.status is GateStatus.APPROVED


def test_k_of_not_met_by_one_person_holding_two_roles() -> None:
    # frank holds rev-b AND rev-c; completing both role-tasks is still ONE identity
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("rev-a", "rev-b", "rev-c"), KOf(2)),))
    ev = evaluate(
        policy,
        REGISTRY,
        [approve("frank", "rev-b", "design:approve"), approve("frank", "rev-c", "design:approve")],
    )
    assert ev.status is GateStatus.PENDING  # 2 role-tasks, but only 1 distinct identity


# ---- per-role tasks + overlapping roles --------------------------------------


def test_tasks_are_one_per_role() -> None:
    policy = ApprovalPolicy(
        rules=(
            Rule("design:approve", ("oms-lead", "web-lead"), "all"),
            Rule("security:approve", ("infosec-lead",), "all"),
        )
    )
    ts = tasks(policy)
    assert [(t.role, t.scope) for t in ts] == [
        ("oms-lead", "design:approve"),
        ("web-lead", "design:approve"),
        ("infosec-lead", "security:approve"),
    ]


def test_two_member_role_completed_by_any_member() -> None:
    policy = ApprovalPolicy(rules=(Rule("release:approve", ("eng-manager",), "all"),))
    ev = evaluate(policy, REGISTRY, [approve("heidi", "eng-manager", "release:approve")])
    assert ev.status is GateStatus.APPROVED


# ---- multi-scope gate --------------------------------------------------------


def test_gate_spans_two_scopes() -> None:
    policy = ApprovalPolicy(
        rules=(
            Rule("design:approve", ("oms-lead",), "all"),
            Rule("security:approve", ("infosec-lead",), "all"),
        )
    )
    # design approved but security not -> pending
    ev = evaluate(policy, REGISTRY, [approve("alice", "oms-lead", "design:approve")])
    assert ev.status is GateStatus.PENDING
    assert ev.satisfied_rules == (0,)
    ev = evaluate(
        policy,
        REGISTRY,
        [
            approve("alice", "oms-lead", "design:approve"),
            approve("dana", "infosec-lead", "security:approve"),
        ],
    )
    assert ev.status is GateStatus.APPROVED


# ---- min_distinct_approvers (four-eyes floor) --------------------------------


def test_min_distinct_approvers_blocks_single_dual_role_person() -> None:
    # alice holds design:approve; a contrived gate asking her for two design roles
    reg = RoleRegistry(
        roles={
            "lead-a": Role("lead-a", frozenset({"alice"}), frozenset({"design:approve"})),
            "lead-b": Role("lead-b", frozenset({"alice"}), frozenset({"design:approve"})),
        }
    )
    policy = ApprovalPolicy(
        rules=(Rule("design:approve", ("lead-a", "lead-b"), "all"),),
        min_distinct_approvers=2,
    )
    # alice completes both role-tasks -> rules met, but only 1 distinct identity
    ev = evaluate(
        policy,
        reg,
        [
            approve("alice", "lead-a", "design:approve"),
            approve("alice", "lead-b", "design:approve"),
        ],
    )
    assert ev.status is GateStatus.PENDING
    assert ev.distinct_approvers == frozenset({"alice"})


# ---- exclude_author ----------------------------------------------------------


def test_exclude_author_bars_self_approval() -> None:
    policy = ApprovalPolicy(
        rules=(Rule("design:approve", ("oms-lead",), "all"),), exclude_author=True
    )
    ev = evaluate(
        policy, REGISTRY, [approve("alice", "oms-lead", "design:approve")], author="alice"
    )
    assert ev.status is GateStatus.PENDING  # alice's approval doesn't count — she authored it


def test_exclude_author_false_allows_self_approval() -> None:
    policy = ApprovalPolicy(
        rules=(Rule("design:approve", ("oms-lead",), "all"),), exclude_author=False
    )
    ev = evaluate(
        policy, REGISTRY, [approve("alice", "oms-lead", "design:approve")], author="alice"
    )
    assert ev.status is GateStatus.APPROVED


# ---- terminal outcomes -------------------------------------------------------


def test_reject_is_terminal() -> None:
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("oms-lead", "web-lead"), "all"),))
    ev = evaluate(
        policy,
        REGISTRY,
        [
            approve("alice", "oms-lead", "design:approve"),
            Resolution("bob", "web-lead", "design:approve", "reject"),
        ],
    )
    assert ev.status is GateStatus.REJECTED


def test_changes_requested_routes_to_rework() -> None:
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("oms-lead",), "all"),))
    ev = evaluate(
        policy, REGISTRY, [Resolution("alice", "oms-lead", "design:approve", "changes-requested")]
    )
    assert ev.status is GateStatus.CHANGES_REQUESTED


# ---- structural validation ---------------------------------------------------


def test_resolution_error_cases() -> None:
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("oms-lead",), "all"),))
    # unknown role
    assert resolution_error(REGISTRY, policy, approve("x", "ghost", "design:approve")) is not None
    # role not asked for this scope in any rule
    assert (
        resolution_error(REGISTRY, policy, approve("dana", "infosec-lead", "security:approve"))
        is not None
    )
    # role doesn't confer the scope (web-lead has no code:approve, and no rule anyway)
    assert (
        resolution_error(REGISTRY, policy, approve("bob", "web-lead", "code:approve")) is not None
    )
    # non-member of the role
    assert (
        resolution_error(REGISTRY, policy, approve("bob", "oms-lead", "design:approve")) is not None
    )
    # valid
    assert (
        resolution_error(REGISTRY, policy, approve("alice", "oms-lead", "design:approve")) is None
    )


def test_invalid_resolutions_are_ignored_by_evaluate() -> None:
    policy = ApprovalPolicy(rules=(Rule("design:approve", ("oms-lead",), "all"),))
    # bob is not a member of oms-lead — his approval must not satisfy the gate
    ev = evaluate(policy, REGISTRY, [approve("bob", "oms-lead", "design:approve")])
    assert ev.status is GateStatus.PENDING


# ---- on_revision -------------------------------------------------------------


def test_on_revision_reset_all_clears_prior_approvals() -> None:
    policy = ApprovalPolicy(
        rules=(Rule("design:approve", ("oms-lead",), "all"),), on_revision="reset_all"
    )
    prior = [approve("alice", "oms-lead", "design:approve")]
    assert after_revision(prior, policy, changed_scopes=["design:approve"]) == ()


def test_on_revision_reconfirm_changed_keeps_unaffected() -> None:
    policy = ApprovalPolicy(
        rules=(
            Rule("design:approve", ("oms-lead",), "all"),
            Rule("security:approve", ("infosec-lead",), "all"),
        ),
        on_revision="reconfirm_changed",
    )
    prior = [
        approve("alice", "oms-lead", "design:approve"),
        approve("dana", "infosec-lead", "security:approve"),
    ]
    # only the design section changed -> security approval carries
    kept = after_revision(prior, policy, changed_scopes=["design:approve"])
    assert [r.scope for r in kept] == ["security:approve"]


# ---- builders from schema dicts ----------------------------------------------


def test_from_dict_builders() -> None:
    reg = RoleRegistry.from_dict(
        {
            "apiVersion": "swarmkit/v1",
            "kind": "RoleRegistry",
            "metadata": {"id": "r", "name": "r"},
            "roles": [{"id": "oms-lead", "members": ["alice"], "scopes": ["design:approve"]}],
        }
    )
    assert reg.confers("oms-lead", "design:approve")
    assert reg.is_member("oms-lead", "alice")

    policy = ApprovalPolicy.from_dict(
        {
            "rules": [
                {"scope": "design:approve", "roles": ["oms-lead", "web-lead"], "quorum": "all"},
                {"scope": "design:approve", "roles": ["rev-a", "rev-b"], "quorum": {"k-of": 2}},
            ],
            "min_distinct_approvers": 2,
        }
    )
    assert policy.rules[0].quorum == "all"
    assert policy.rules[1].quorum == KOf(2)
    assert policy.exclude_author is True  # default
    assert policy.min_distinct_approvers == 2


def test_from_dict_rejects_bad_quorum() -> None:
    with pytest.raises(ValueError):
        ApprovalPolicy.from_dict(
            {"rules": [{"scope": "a:b", "roles": ["r"], "quorum": "majority"}]}
        )
