# 19 — Fleet enrollment protocol + API-key credentials

Refines the connection model (D2, [11](11-architecture.md)), the token model ([12](12-auth.md) §4/§6),
and the enrollment flow ([13](13-connector-registry.md) §Enrollment). Turns enrollment from a
panel-specific, one-sided token install into a **standard, client-agnostic handshake** that
establishes mutual membership, exchanges credentials, and syncs the instance's **full state** — with
**monitor-only** and **offline-resilient observation** as first-class outcomes.

Status: design (proposed) — review before implementation. Supersedes [13](13-connector-registry.md)
§"Enrollment flow" and refines [12](12-auth.md) §6 as noted inline.

## Goal

Make "an instance joins a fleet" a documented protocol that **any client** can implement (the fleet
panel is client #1), where:

1. the instance is told it now belongs to a fleet and gets a stable **membership id**;
2. credentials for future calls are **issued during the handshake** (not hand-installed);
3. the fleet receives the instance's **full current state** (content, not just capability names);
4. the fleet can **re-fetch and cache** that state on demand — so an instance maintained *outside*
   the fleet, and an instance that later goes **offline**, are both observable;
5. **monitor-only** membership (observe, never deploy) is supported, distinct from **manage**.

## Non-goals

- Replacing serve's existing auth seam ([12](12-auth.md)) — this builds *on* `AuthProvider`
  (APIKey/JWT), the scope tiers, and the `token_hash` pattern; it does not add parallel auth.
- Deploy/push mechanics (fleet → instance) — unchanged, still governed + human-gated
  ([15](15-artifact-registry.md), [17](17-growth-loop.md)). This doc is about *joining* and
  *observing*.
- mTLS between panel and instance (a stronger Mode A option) — noted as a future edge in
  [13](13-connector-registry.md); orthogonal.

## Why (the gaps this closes)

| Gap (today) | Consequence |
|---|---|
| `GET /capabilities` advertises **names only** (topology ids, providers) | The fleet can never see or cache what an instance actually contains; the Artifacts/inventory view can't be populated from an instance. |
| Enrollment installs a **panel-minted token by hand**; the instance isn't an active party | No mutual membership; the instance doesn't "know" it joined; multi-fleet is awkward; every client would reinvent the flow. |
| No **full-state fetch** + no **cache** | An instance maintained outside the fleet (YAML edited + redeployed directly) can't be reflected; an **offline** instance shows nothing. |
| No **monitor vs manage** distinction | "I only want to watch these swarms" isn't expressible; observe and deploy are conflated. |

## Principles

1. **Standard protocol, not a UI feature.** The handshake + credential + state schemas are a
   versioned part of the **serve connector contract** (D1: the panel depends on that contract, it
   doesn't own it). A monitoring dashboard, a CLI, or a third-party app implements the same thing.
2. **The resource owner issues access to its own API.** The **instance** issues the credential a
   fleet uses to call *it*; the **fleet** issues the credential a connector uses to call *the fleet*.
   Two directions ⇒ two credentials ⇒ each side revokes its own. *(This refines [12](12-auth.md) §6,
   which had the panel mint the panel→instance token and the operator hand-install it. Moving
   issuance to the instance is what makes multi-fleet + "maintained outside the fleet" + independent
   revocation coherent.)*
3. **Reuse the auth seam.** Issued credentials are opaque, hashed at rest, scoped — the existing
   `APIKeyAuthProvider` + `token_hash` pattern, generalized. Transport scopes stay a separate
   namespace from governance scopes ([12](12-auth.md) §2) — a membership credential structurally
   cannot carry a reserved-for-human capability.
4. **Separation of powers.** `manage` grant is **human-issued**; a machine cannot self-promote
   `monitor → manage` ([05](05-identity-governance-iam.md), [12](12-auth.md) §7).

## The membership model

Enrollment creates a **membership** — the binding between one fleet and one instance:

```
Membership {
  membership_id   # stable id/hash for this (fleet, instance) pair — the binding
  fleet_id        # who joined (fleet's self-identifier / public key id)
  instance_id     # the instance (as the fleet knows it)
  scope           # "monitor" | "manage"
  issued_at, expires_at
}
```

- **Multi-fleet falls out.** An instance keeps a list of memberships (one per fleet it joined); each
  join mints a fresh `membership_id`. A swarm can be `monitor` in fleet A and `manage` in fleet B.
- Both sides persist their half: the fleet in its registry (extends [13](13-connector-registry.md)
  `Instance`); the instance in a small local **membership file** so `serve` knows who is asking and
  what they may see/do.

## The two-token credential flow (device-enrollment shape)

Three distinct artifacts, in sequence — **not** interchangeable:

1. **Enrollment token** — short-lived, one-time, *authorizes the join*. The bootstrap secret
   (compare a `kubeadm` join token or a Tailscale auth key). Issued by the party that owns the
   resource being joined (see handshake). **Required off-loopback, optional on loopback**
   (mirrors default-secure, [12](12-auth.md) §8). An enrollment token that grants `manage` scope
   must be **human-issued**.
2. **Issued credential** — long-lived, scoped, opaque **API key**; *used for all subsequent calls*.
   Shown once; the issuer stores only its hash.
3. **Refresh token** — rotates the issued credential without re-bootstrapping.

### Decision: opaque API keys, not JWT, for the machine credential

| | Opaque API key (recommended) | JWT |
|---|---|---|
| Revocation | **O(1) — delete the row** (instance leaves fleet; fleet decommissioned) | denylist or short-expiry churn |
| Infra | reuses `token_hash`; nothing new | JWKS + signing-key rotation to operate |
| Verify | per-call hash lookup (stateful) | stateless |
| Fit | matches existing mint-token ([12](12-auth.md) §6) | already used for **operator OIDC** |

Long-lived machine memberships need cheap revocation more than they need stateless verify, so lead
with **opaque, hashed, scoped API keys**. Keep **JWT for the operator → panel OIDC path** where it
already lives ([12](12-auth.md) §3). A JWT layer can be added on top later if call volume demands
stateless verification; don't lead with it.

## The handshake

Initiated by **whichever side can reach the other** — same logical outcome (mutual membership +
credentials + full state), transport differs by mode ([13](13-connector-registry.md) §Connection
modes).

### Mode A — reachable instance (fleet → instance)

```
operator: "enroll <endpoint>" in the fleet UI  (+ enrollment token from the instance owner)
panel  →  POST {endpoint}/fleet/register
          Authorization: Bearer <enrollment-token>
          { fleet_id, fleet_callback_url, requested_scope: "monitor"|"manage" }
serve  →  201
          { membership_id,
            credential: { type:"api_key", value:"<once>", scope, expires_at },
            refresh:    { token:"<once>", expires_at },
            instance_state: { …full export… } }          # ← identity + state in one round trip
panel  stores: Instance{…}, membership, credential (hash of the fleet-side connector cred if Mode B),
               and the instance_state snapshot (+ last_synced).
```

The instance created a membership, issued the fleet a scoped key to call it, and returned its full
state — the fleet is enrolled *and* has a cached snapshot in a single call.

### Mode B — NAT'd instance (instance → fleet)

The panel can't reach the instance, so the handshake inverts (the deferred "instance-initiated"
option in [13](13-connector-registry.md) §Enrollment, now the Mode-B norm):

```
operator: "enroll (poll)" in the fleet UI  →  panel issues a one-time JOIN CODE (enrollment token
          for the *fleet*), shown to the operator.
edge:     swarmkit connect <panel-url> --join-code <code>
connector → POST {panel}/fleet/join
            { join_code, instance_identity, instance_state: { …full export… } }
panel   →  201 { membership_id,
                 credential: { type:"api_key", value:"<once>", scope, expires_at },  # connector→panel cred
                 refresh:    { token:"<once>", expires_at } }
connector stores the credential; polls thereafter ([13](13-connector-registry.md) §Mode B).
```

Same result: fleet-side `Instance` + membership + cached `instance_state`; instance-side membership
+ the credential it uses to reach the fleet.

## Endpoint spec (the versioned contract)

All new endpoints carry `apiVersion: swarmkit/v1`. Scopes per [12](12-auth.md) §4.

**On `serve` (new — the instance side):**

| Method + path | Auth | Purpose |
|---|---|---|
| `POST /fleet/register` | enrollment token | Mode A join: create membership, issue fleet-facing key, return `instance_state`. |
| `GET  /fleet/state` | membership key (`monitor`+) | Re-fetch the full `InstanceState` (the pull-and-cache primitive). |
| `POST /fleet/refresh` | refresh token | Rotate the issued fleet-facing key. |
| `DELETE /fleet/membership/{id}` | membership key or local admin | Instance ejects a fleet (revokes that fleet's key). |
| `POST /fleet/enroll-token` | `serve:admin` / local CLI | Mint a one-time enrollment token (human action). |

**On the panel (new/changed):**

| Method + path | Purpose |
|---|---|
| `POST /fleet/join` | Mode B join (instance-initiated): validate join code, create membership, issue connector→panel key, store `instance_state`. |
| `POST /instances` (enroll) | Mode A: now *calls* `serve` `/fleet/register` under the hood (was: mint token + pull `/capabilities`). |
| `GET  /instances/{id}/state` | Return the **cached** `InstanceState` (+ `last_synced`), even when the instance is offline. |
| `POST /instances/{id}/sync` | Force a re-pull (Mode A) or await next connector upload (Mode B). |

## `InstanceState` — the full export schema

The new content-bearing primitive (vs `/capabilities`, which stays as the cheap names-only liveness
probe). Idempotent sync keys off `content_hash` (same hash as the artifact registry,
[15](15-artifact-registry.md)) so unchanged artifacts are no-ops.

```yaml
apiVersion: swarmkit/v1
kind: InstanceState
workspace_id: sterling-oms
schema_version: "1.7.0"
generated_at: "…"
artifacts:
  topologies: [{ id, version, content_hash, content }, …]
  skills:     [{ id, version, content_hash, content }, …]
  archetypes: [{ id, version, content_hash, content }, …]
  triggers:   [{ id, version, content_hash, content }, …]
providers: [anthropic, google, ollama, …]
governance_provider: agt
health: { status, uptime_s, … }
```

## Observed state: sync + offline cache

- The panel persists the **last-fetched `InstanceState` per instance**, timestamped (`last_synced`) —
  a new **observed-state snapshot** store (separate from the deployable registry; see below).
- **Cadence:** Mode A — panel pulls `GET /fleet/state` on enroll + on a schedule + on demand
  (`/sync`). Mode B — connector uploads it on join + periodically in the poll body.
- **Offline:** the instance page shows the **cached snapshot** with "last synced at …" — so a swarm
  the fleet currently can't reach is still fully inspectable. This is the observed-state-cache
  pattern; the instance (its YAML) is the source of truth, the fleet is a cache.

## Monitor vs manage (membership scope)

- **`monitor`** — the fleet may `GET /fleet/state` (observe + cache) and receive pushed
  observability, nothing else. The instance is maintained entirely outside the fleet.
- **`manage`** — additionally, the fleet may deploy governed, human-approved artifacts
  ([15](15-artifact-registry.md)). Maps onto the `run`/`admin` tiers ([12](12-auth.md) §4) and the
  separation-of-powers model (observe = media role; deploy = executive, human-gated).

## Registry vs observed snapshot (keep them separate)

The pulled `InstanceState` is a **per-instance observed snapshot** — *what this instance has right
now*. It is **not** dumped into the deployable **artifact registry** ([15](15-artifact-registry.md)),
which stays the curated, versioned, provenance-tracked publish target. Turning an observed artifact
*into* a registry version (to then govern/deploy it) is an explicit, optional **"adopt into
registry"** action — never automatic. This preserves the design's distinction between *what an
instance happens to run* and *what the fleet has vetted and can deploy* (the basis for drift,
[15](15-artifact-registry.md)).

## Security

- Enrollment token: one-time, TTL, **required off-loopback**, **human-issued for `manage`**.
- Issued API keys: opaque, hashed at rest, scoped; **both sides revoke their own** (instance ejects a
  fleet via `DELETE /fleet/membership/{id}`; fleet removes an instance → revokes the connector key).
- Rotation via refresh token ([12](12-auth.md) §6 rotation, generalized).
- Every remote call audited with `client_id`/`membership_id` ([12](12-auth.md) §7).
- Transport scope ≠ governance scope: a membership credential cannot carry a reserved-for-human
  capability ([12](12-auth.md) §2).

## Data-model changes

- **Instance side (`serve`):** a `memberships` record (`membership_id → {fleet_id, scope,
  issued_key_hash, fleet_callback_url}`), an issued-key store (hashes), and one-time enrollment
  tokens. Small local store (sqlite/JSON under `.swarmkit/`).
- **Panel side:** extend `Instance` ([13](13-connector-registry.md)) with `membership_id` +
  `fleet-facing credential ref`; **new observed-state snapshot table** (`instance_id`, `state_json`,
  `content_hashes`, `last_synced`). Fits the SQLAlchemy-Core store model (both dialects).

## Backward compatibility / migration

- Loopback/open dev is unchanged — no enrollment token required on loopback; the current demo flow
  keeps working.
- The existing `POST /instances` + `POST /verify` path is kept during transition; Mode A enroll
  gains the `/fleet/register` handshake underneath. `GET /capabilities` stays (cheap liveness);
  `GET /fleet/state` is additive.
- This is a **breaking change only** for a non-loopback bind that wants the new handshake — same
  default-secure posture already introduced in [12](12-auth.md) §8.

## What this builds (phased — one design, three PR-sized slices)

1. **Observe (read-only value first) — ✅ shipped.** `InstanceState` schema + `GET /fleet/state` on
   serve + the panel observed-state snapshot store + the instance-page cached view + **monitor-only**
   membership. *Delivers: real inventory + offline-resilient observation, with no credential-model
   change yet (uses existing tokens).*
2. **Handshake + credentials — ✅ shipped.** `POST /fleet/register` (Mode A) + `POST /fleet/join`
   (Mode B) + one-time enrollment tokens + issued opaque API keys (stored **encrypted at rest** via a
   pluggable SecretBox — Fernet local / Vault-Transit) + refresh/rotate + revoke; `GET /fleet/state`
   also accepts the membership key; the panel enroll/rotate action + the "enroll (poll)" join-code UI
   + `swarmkit connect --join-code` connector bootstrap wire onto it. (PRs #453–#467.)
3. **Manage + adopt — ✅ shipped.** `manage` scope + governed deploy over the membership credential
   (scope-aware serve auth-seam fallback: monitor→read, manage→the deploy PUTs; reuses
   [15](15-artifact-registry.md)/[17](17-growth-loop.md)) + the explicit "adopt observed artifact
   into registry" action + panel-perspective multi-fleet visibility with credential-native
   self-leave. See [20](20-manage-and-adopt.md). (PRs #469–#472.)

## Test plan

- Contract tests for the versioned schemas (`InstanceState`, register/join request+response) — shared
  fixtures, both languages ([schema-change-discipline](../../../docs/notes/schema-change-discipline.md)).
- Mode A register handshake e2e (panel → fake serve): membership + key + state returned; wrong/expired
  enrollment token rejected; off-loopback without a token refused.
- Mode B join e2e (connector → panel): join code single-use; connector credential issued; state cached.
- Offline: kill the instance → panel still serves the cached `InstanceState` with `last_synced`.
- Revocation both directions; refresh rotates and invalidates the prior key.
- Monitor-only cannot deploy (403); manage requires a human-issued token.

## Open questions

1. **Fleet identity.** How does an instance identify a fleet (`fleet_id`) trustably — a fleet public
   key / self-signed identity vs an opaque id? Matters for "same swarm, many fleets" and for the
   instance to attribute pushes. *(Lean: fleet presents a stable key id; instance pins it at join.)*
2. **State size / transfer — ✅ done.** Full content on the first sync, then **delta by
   `content_hash`** on subsequent syncs: serve exposes `GET /fleet/state/manifest` (names + hashes,
   no content) + `POST /fleet/state/artifacts` (fetch only the changed bodies); the panel diffs the
   manifest against its cache and merges (`_delta.py`), falling back to a full pull on the first sync
   or against a pre-delta instance. The `/sync` response reports `{mode, fetched, reused, removed}`.
   (PRs #475–#477.)
3. **Enrollment-token issuance UX — ✅ done.** The instance owner mints the one-time code with
   `swarmkit fleet enroll-token <workspace> --scope monitor|manage [--ttl N]`, which prints the
   single-use token + instructions (operating directly on the instance's `.swarmkit/fleet.sqlite`,
   no running serve or auth token — like `swarmkit auth token`). `swarmkit fleet memberships` lists
   who has registered (+ whether their identity is pinned). The operator pastes the code into the
   fleet UI's Register action; the EnrollmentPanel points at the CLI command.
4. **Where the instance-side membership store lives** — reuse the serve sqlite (`.swarmkit/`) vs a
   dedicated file; interaction with `swarmkit connect` (which already holds the Mode B token).
5. **Standard-protocol packaging — ✅ done.** The register/join + `InstanceState` (+ credential)
   schemas are published under `packages/schema/schemas/protocol/` as canonical JSON Schema, with
   cross-language validators (`validate_protocol` in Python / `validateProtocol` in TypeScript) and
   shared fixtures — a distinct namespace from the artifact schemas (they are wire contracts, not
   codegen'd artifacts). Any client can now validate against the files. Response schemas cross-ref
   the credential + instance-state schemas by `$id`.
