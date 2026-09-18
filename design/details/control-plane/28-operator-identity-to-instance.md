# 28 — Operator identity to the instance: who approved, when the click came from the panel

**Status:** design · **Scope:** runtime + control-plane + fleet UI · **Follows:** [12](12-auth.md) (§3 two auth edges), [21](21-fleet-identity.md), [22](22-signed-pushes.md), [27](27-fleet-signals-and-kinds.md); `design/details/pipeline-gate-approval-ui.md` (the resolver is the authenticated caller)

## The gap

A multi-party approval is a *person's* act. The runtime counts `distinct_approvers` by the
authenticated caller, refuses non-members of the role, and records `resolved_by` in the audit.
Through the panel every call to an instance carries one credential — the key the instance was
enrolled with — so "who approved" is always that key's owner. Level 22 shows it: alice's key
casts alice's approval and nothing else; bob had to use his own key over HTTP. #897 made the
refusal visible; it did not make the panel able to act for the person who clicked.

The panel already knows who that is. With OIDC on (`--oidc-issuer`, `--oidc-audience`), a request
authenticates as an operator carrying a `subject` (`_auth.Principal.subject`). It is never sent
onward.

## Goal

An operator signed into the panel with OIDC resolves a role-task on an instance **as themselves**:
the instance's role registry decides whether that subject is a member of the role, the resolution
counts toward the quorum under that subject, and both audits record the person *and* the fleet
that relayed the click.

## Non-goals

- Forwarding identity for anything but gate resolution. Deploys, syncs, canary control remain the
  *panel's* acts under the membership scope a human issued.
- Token exchange (RFC 8693) to a per-instance audience. Cleaner trust, but it requires every
  instance to run JWT auth against the same IdP; the API-key majority (every tutorial workspace)
  would be excluded. Designed here as the future option for JWT fleets, not built.
- Mapping subjects to `client_id`s on the instance. The role registry already *is* that map: a
  subject is a member of a role or it is not. Instance owners list subjects (or emails, whatever
  the IdP's `sub` is) as role members; nothing new to configure.
- Open-mode or operator-token panels. Without OIDC there is no person to assert; the panel keeps
  resolving as its enrolment key and the card says so.

## Trust model

The instance must decide whether to believe the panel's word about who clicked. That is a new
grant, so it is a new **membership scope**: `approve-as`, above `manage`, minted like the others
with `swarmkit fleet enroll-token --scope approve-as` — human-issued on the instance, per fleet.
An instance that only granted `manage` ignores the assertion and resolves as the enrolment key,
exactly as today. A compromised panel with `approve-as` can approve as any member; that is the
grant's meaning and the reason it is separate from `manage`.

The assertion is **signed by the fleet identity** (design 21) the instance pinned at register —
the same Ed25519 key that signs deploys — so a stolen enrolment key alone cannot forge one.

## Wire shape

`POST /review/{item_id}/resolve` as today (transport-authenticated by the enrolment key, which
needs `run`), plus three headers when the panel has a subject and a fleet identity:

| Header | Value |
| --- | --- |
| `X-Fleet-Id` | the panel's fleet id (as pinned) |
| `X-Fleet-Actor` | the OIDC subject |
| `X-Fleet-Actor-Signature` | base64 Ed25519 over `actor_message(item_id, subject, issued_at)` |

`actor_message` is `actor:<item_id>:<subject>:<issued_at>` (`issued_at` = unix seconds), the
sibling of `deploy_message`. `X-Fleet-Actor-Issued` carries `issued_at`. Binding `item_id` means a
signature for one task cannot resolve another; `issued_at` gives a freshness window (300 s) so a
captured header cannot be replayed later. A replay inside the window re-resolves the same task as
the same person — idempotent.

### Runtime

`_resolve_role_task` receives `actor` from a new helper, `_resolving_actor(request)`:

1. No `X-Fleet-Actor` → `identity.client_id` (today's behaviour).
2. Header present: look up the membership for `X-Fleet-Id` (`MembershipStore.membership_for_fleet`),
   require scope `approve-as` and a pinned key; verify the signature over
   `actor_message(item_id, actor, issued_at)`; require `|now − issued_at| ≤ 300`. Any failure is a
   **401** naming the reason — an assertion that does not verify must not silently fall back to
   the enrolment key, or the panel could never tell the two cases apart.
3. `actor = subject`; the existing `membership_error` check against the role registry runs
   unchanged. The audit payload gains `"via_fleet": fleet_id`; `resolved_by` is the subject.

`Scope` gains `approve-as`; `_membership_authenticates` does not change (the assertion rides on a
normally authenticated request; the membership key itself still opens only the fleet-read and
deploy routes). `swarmkit fleet enroll-token --scope approve-as` and `POST /fleet/enroll-token`
accept it; `swarmkit fleet memberships` lists it.

### Control plane

`instance_gate_resolve` reads `request.state.principal.subject`. When it is set, the instance has a
membership credential (so a register happened and the instance pinned this fleet), and the panel
holds its identity: sign and send the headers. `resolve_gate` gains `actor: ActorAssertion | None`.
The response of `GET /instances/{id}/review` gains `resolves_as`: `{"kind": "subject", "subject":
"alice"}` or `{"kind": "instance-key"}` — so the UI can say which before the click. The panel's
own audit of the resolution (already recorded per doc 20) carries the subject.

A `_verbs`/contract-test addition: the scope vocabulary (`monitor`, `manage`, `approve-as`) is
asserted equal on both sides.

### Fleet UI

- Enrolment panel: `approve-as` in the requested-scope selector, with one line on what it grants.
- Gates card title: "Resolving as alice" / "Resolving as the enrolment key (sign in with OIDC to
  act as yourself)".

## Test plan

- runtime: `actor_message` is stable; a valid assertion under `approve-as` resolves as the subject
  (counted in `distinct_approvers`, `resolved_by` = subject, audit has `via_fleet`); under `manage`
  → 401 "fleet … is not granted approve-as"; bad signature → 401; stale `issued_at` → 401; unknown
  fleet → 401; no header → today's path. `enroll-token --scope approve-as` mints and registers.
- control-plane: with an OIDC principal and an identity, `resolve_gate` is called with an
  assertion whose signature verifies against the panel's public key; without a subject, none;
  `resolves_as` in the review envelope; contract test for the scope vocabulary.
- fleet UI: the card shows who it resolves as; the selector offers `approve-as`.

## Demo plan

Level 22 §5 addendum, live: the panel started with OIDC against a local JWKS (an RSA key served
from a one-file HTTP server — the runtime's own JWT tests do the same), a token minted for
`sub: alice`, the instance re-registered with `--scope approve-as`; the fleet UI (token injected)
approves the engineering-lead task and the instance's `/gates/{id}` shows `distinct_approvers:
["alice"]` with `resolved_by: alice` and `via_fleet` in `swarmkit logs`. Then the same click
without OIDC → resolved as the enrolment key, the card saying so.
