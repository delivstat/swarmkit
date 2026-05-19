---
title: GovernanceProvider interface — method signatures, types, async semantics
description: Finalises the GovernanceProvider ABC for M2. Covers evaluate_action, verify_identity, record_event, get_trust_score — all async.
tags: [governance, abstraction, M2]
status: active
---

# GovernanceProvider interface

## Goal

Lock in the `GovernanceProvider` ABC method signatures so M2 features
can build against a stable contract. The M0 scaffold has the right
shape but needs three upgrades:

1. **Async.** Governance calls may involve I/O (AGT could be a sidecar
   or remote service). The runtime is async-first per the style guide.
2. **Explicit scopes in `evaluate_action`.** Design §16.3 says "an
   agent can only invoke a skill if the agent's scopes include all
   the scopes the skill requires." Scopes belong in the call signature,
   not buried in a generic `context` dict.
3. **Richer types.** `PolicyDecision` needs tier info and a scope
   breakdown for observability. `AuditEvent` needs `datetime` timestamps
   and optional topology/skill identifiers for swarm-specific events.

## Non-goals

- **AGT wiring.** `AGTGovernanceProvider` stays a stub until we pin
  AGT's Python SDK (design §21 open question). M2's real value is the
  mock + middleware pipeline, not AGT integration.
- **AuditProvider.** The storage backend for audit events (task #38) is
  a separate abstraction. `GovernanceProvider.record_event` is the
  *intake* side; `AuditProvider` is the *storage* side. For M2,
  `MockGovernanceProvider` appends to an in-memory list.
- **Tier 2/3 judges.** Tier 1 (deterministic policy checks) is M2.
  LLM judges land in M4.

## Finalised ABC

```python
class GovernanceProvider(ABC):

    @abstractmethod
    async def evaluate_action(
        self,
        *,
        agent_id: str,
        action: str,
        scopes_required: frozenset[str],
        context: dict[str, object] | None = None,
    ) -> PolicyDecision: ...

    @abstractmethod
    async def verify_identity(
        self,
        *,
        agent_id: str,
        credential: AgentCredential,
    ) -> IdentityVerification: ...

    @abstractmethod
    async def record_event(
        self,
        event: AuditEvent,
    ) -> None: ...

    @abstractmethod
    async def get_trust_score(
        self,
        *,
        agent_id: str,
    ) -> TrustScore: ...
```

All methods are keyword-only past `self` (prevents positional mix-ups
as the signature evolves). All are `async` — the mock returns
immediately; real implementations may do I/O.

## Types

```python
@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    tier: int                               # 1, 2, or 3 (§8.6)
    scopes_granted: frozenset[str]
    scopes_denied: frozenset[str]

@dataclass(frozen=True)
class AuditEvent:
    event_type: str                         # e.g. "skill.invoked", "policy.denied"
    agent_id: str
    timestamp: datetime                     # UTC
    payload: dict[str, object]
    topology_id: str | None = None
    skill_id: str | None = None

@dataclass(frozen=True)
class AgentCredential:
    credential_type: str                    # "ed25519", "did", "mock"
    value: str

@dataclass(frozen=True)
class IdentityVerification:
    verified: bool
    agent_id: str

@dataclass(frozen=True)
class TrustScore:
    score: float                            # 0.0–1.0 normalised
    tier: str                               # behavioral tier label
```

## MockGovernanceProvider

Deterministic, configurable, test-only. Ships in
`governance/_mock.py`. Core design:

- **Constructor takes `allowed_scopes`.** `evaluate_action` checks
  whether `scopes_required ⊆ allowed_scopes`. If yes → allowed. If
  no → denied with the missing scopes listed in `reason`.
- **Events are collected.** `record_event` appends to an internal
  list. `.events` property returns a copy. Tests assert against the
  list to verify audit flow.
- **Identity always verifies.** `verify_identity` returns `True`.
  Tests that need identity failure use a separate deny-all mock or
  parametrise.
- **Trust scores configurable.** Constructor takes an optional
  `trust_scores: dict[str, float]` mapping agent_id → score.
  Default 1.0 for unknown agents (fully trusted).

## Middleware pipeline (follow-up PR)

A second PR adds the runtime middleware that routes every skill
invocation through `evaluate_action` before execution. Not in this PR
because the middleware depends on the skill-invocation path that M3
(LangGraph compiler) will build. For M2, the exit demo uses the mock
directly in test code.

## Test plan

`packages/runtime/tests/test_governance_provider.py`:

1. **Mock allows when scopes match.** Agent with `{repo:read}` invoking
   a skill requiring `{repo:read}` → allowed, tier 1.
2. **Mock denies when scopes don't match.** Agent with `{repo:read}`
   invoking a skill requiring `{repo:write}` → denied, scopes_denied
   contains `repo:write`.
3. **Audit events collected.** `record_event` → `mock.events` has the
   event.
4. **Trust score returns configured value.** Agent with custom score →
   that score. Unknown agent → 1.0.
5. **Identity verification.** Default mock → verified.
6. **Separation-of-powers invariant.** The mock's events list is
   read-only from the provider's public API — there's no `clear_events`
   or `delete_event` method.

## Demo

The exit demo for M2 is a test, not a CLI command: "a unit-test swarm
where a worker tries to invoke a skill it lacks the scope for; policy
denies; audit records the attempt; test asserts both." This test ships
with this PR using MockGovernanceProvider. The AGT wiring PR adds
the same scenario against the real provider.
