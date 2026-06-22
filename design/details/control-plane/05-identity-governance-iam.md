# 05 — Identity, governance & IAM

Scope: `governance/`, the IAM scope model, decision skills, circuit breakers, identity providers,
HITL. This is the source of truth for the **auth design** and the constraints it must respect.
Design refs: §8 (separation of powers), §8.7 (reserved scopes), §16.

## GovernanceProvider (`governance/__init__.py:279`)

Methods: `evaluate_action(agent_id, action, scopes_required, context) -> PolicyDecision`,
`verify_identity`, `record_event`, `get_trust_score`, `evaluate_decision_skill`. `PolicyDecision`:
`allowed`, `reason`, `tier` (1 deterministic / 2 single-judge / 3 panel), `scopes_granted/denied`.
Built-ins: **AGT** (`agt_provider.py` — AsyncPolicyEvaluator, AgentIdentity DIDs+Ed25519,
FlightRecorder append-only audit), **Mock** (`allow_all`), **SkillBacked** (wraps a base provider to
run decision skills). Config: `workspace.governance` (`provider`, `policy_language`
`yaml`/`rego`/`cedar`, `config`, `limits`, `decision_skills`).

## IAM scopes

- Format `namespace:action` (e.g. `repo:read`, `skills:activate`). Attached to agents via
  `iam.base_scope` + `iam.elevated_scopes` (topology schema); skills declare `iam.required_scopes`.
- Checked at the tool boundary: the compiler passes `scopes_required` to `evaluate_action`; AGT
  checks `identity.has_capability(scope)`.
- **Reserved-for-human (§8.7)** — must never be grantable to an agent or the panel:
  `skills:write_pending` (authoring swarm), `skills:activate` (human), `mcp_servers:scaffold`
  (swarm), `mcp_servers:deploy` (human + sandbox), `topologies:modify` (human), `iam:modify`
  (human); `audit:modify` **exists for no one** (append-only is structural). Enforcement today is
  by convention + AGT policy files + the schema not exposing them — not a hardcoded deny-list.

## Separation of powers (§8)

| Pillar | Role | Module |
|---|---|---|
| Legislative | rules: topology YAML, IAM policies | `workspace/`, artifact files (loaded, never modified by agents) |
| Executive | does work: agents invoke skills | `langgraph_compiler/` (routes every call through `evaluate_action`) |
| Judicial | evaluates: decision skills, validation gates, judges | `governance/`, decision skills |
| Media | surfaces: audit, review queue, gap log | `audit/`, `review/`, observability (append-only from executive) |

## Identity providers

`workspace.identity.provider` ∈ `builtin|auth0|okta|google|azure-ad|oidc` — **human identity** for
approval gates. Distinct from the serve **AuthProvider** (`auth/`, transport authn — see
[02](02-serve-api.md)) and from **agent identity** (AGT DIDs/Ed25519). `AuthIdentity`:
`client_id`, `client_name`, `provider`, `scopes`, `metadata`.

## Decision skills

`DecisionSkillBinding`: `id`, `trigger` ∈ `pre_input|post_output|checkpoint|pre_synthesis`, `scope`
(comma-sep agent ids or `*`), `required` (default true), `config`. Bound at workspace +/or topology
(topology overrides by id; `required:false` disables an inherited binding). Result:
`DecisionSkillResult` (`verdict` pass/fail/needs-revision, confidence, reasoning, flagged_items).
Built-in grounding skills: grounding-verifier, contradiction-detector, citation-checker.

## Circuit breakers (`governance/_limits.py`)

`GovernanceLimits`: `max_steps_per_agent`, `max_steps_per_run` (default 500), `max_cost_per_run_usd`
(inactive until providers emit cost). `CircuitBreakerTracker.check_agent_step()` raises
`CircuitBreakerError`. Config: `governance.limits`.

## HITL (`review/`)

`swarmkit review list|show|approve|reject` over `FileReviewQueue` (`.swarmkit/reviews/`). Inline
`prompt_human_review()` (approve/reject/show; Ctrl+C → `HITLDeferredError` → resumable). Full
interactive TUI deferred to the commercial UI.

## Control-plane implications

- **Two distinct auth edges** (do not conflate): panel↔serve = transport authn (the `auth/`
  AuthProvider — bearer/JWT, add tiers + schema); human↔panel = OIDC login (reuse the
  `identity.provider` enum's OIDC). Governance identity (whose approval counts) is a third concern,
  layered above transport auth.
- **The panel is executive/media — never legislative/judicial.** It must not be grantable
  `skills:activate` / `mcp_servers:deploy` / `topologies:modify` / `iam:modify`, must not bypass
  `evaluate_action`, must not modify audit. Recommend a hardcoded reserved-scope deny-list as part
  of the auth-hardening phase (today it's convention-enforced).
- **Approval at fleet scale:** the review queue is per-instance (`FileReviewQueue`); a fleet
  approval surface aggregates queues and routes approvals back — but the *decision* authority stays
  human, gated by identity.
- Every panel-triggered run must land in the (central) audit log with the acting identity.
