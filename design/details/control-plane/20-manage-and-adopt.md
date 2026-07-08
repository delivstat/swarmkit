# Fleet enrollment Phase 3 — manage scope, governed deploy over the membership credential, and adopt-into-registry

Status: design, pending review. Builds on [19](19-fleet-enrollment-protocol.md) (Phases 1–2, shipped),
[15](15-artifact-registry.md) (registry + versioning), [17](17-growth-loop.md) (growth loop), and
[13](13-connector-registry.md) (connector). This is slice 3 of doc 19's phasing.

## Goal

Make a `manage`-scope membership *actually manage* — deploy artifacts to an enrolled instance using
the credential the instance issued the fleet, not a separately-provisioned admin token. Plus the two
adjacent pieces that complete the enrollment story: promoting an observed artifact into the
deployable registry, and seeing/ejecting an instance's fleet memberships.

Three things:

1. **Governed deploy over the membership credential.** Route deploy through the encrypted membership
   key (Phase 2's `CredentialStore`), gated on `scope == manage`.
2. **Adopt observed artifact into registry.** An explicit action that turns a cached
   `InstanceState` artifact (content + `content_hash`) into a registry version.
3. **Multi-fleet visibility.** Show an instance's memberships (across fleets) and let the owner eject.

## Non-goals

- **No new deploy semantics.** The registry, versioning, content-hash, drift, and schema-compat gate
  ([15](15-artifact-registry.md)) are unchanged. We only change *which credential* carries the push
  and *what scope* authorizes it.
- **No auto-activation.** Adopt and deploy stay human-driven; the growth loop stays proposal-only
  ([17](17-growth-loop.md)). Reserved-for-human governance scopes remain un-grantable (design §8.7).
- **No panel-issued instance credentials.** The instance is still the resource owner that issues its
  own credential (doc 19's core decision); we consume it, we don't mint it.
- **Not "one panel, many memberships per instance."** Multi-fleet already falls out of Phase 2 (each
  fleet/panel holds its own membership; each join mints a fresh `membership_id`). This slice only
  *surfaces* that on the instance page — no data-model change on the panel.

## Design

### 1. Governed deploy over the membership credential

Today `push_artifact(endpoint, token_ref, …)` (Mode A) PUTs to serve `/api/{plural}/{id}` using the
instance's stored `token_ref` — a separately-provisioned `serve:admin` token, unrelated to
enrollment. Phase 3 unifies this: **deploy uses the membership credential** the panel already stores
encrypted, and requires that membership to hold `manage`.

Two enforcement points (defense in depth, mirroring Phase 2's monitor-read model):

- **Panel side.** `DeployService.deploy` (Mode A) resolves the credential via
  `cred_store.get_secret(instance_id)` instead of `inst.token_ref`, and refuses (`409`/`403`) if the
  stored membership metadata's `scope != "manage"` or no membership exists. Mode B is unchanged — it
  still enqueues a `deploy` command the connector applies locally over loopback.
- **Serve side.** The membership auth fallback added in Phase 2 (`_membership_authenticates`, which
  today allows monitor+ on `/fleet/state`) becomes **scope-aware**:
  - `monitor` → the fleet-read routes (`/fleet/state`) — unchanged.
  - `manage` → the fleet-read routes **plus** the deploy write routes (`PUT /api/{topologies,skills,archetypes}/{id}`).

  So a `manage` membership key authorizes a deploy PUT; a `monitor` key gets `403`. This means the
  panel no longer needs a `serve:admin` `token_ref` on the instance at all for deploy — the
  membership key it was issued at enrollment is sufficient and scoped.

Separation of powers is preserved: the panel still only deploys a **human-published registry
version** (an approved artifact), the action stays operator-gated + audited on the panel, and
`topologies:modify` remains reserved-for-human — `manage` authorizes *applying a published version*,
not authoring one.

Backward compatibility: if a `token_ref` is still configured and no membership exists, deploy falls
back to the old `token_ref` path (non-breaking for instances enrolled before Phase 2).

### 2. Adopt observed artifact into registry

The observed-state cache (Phase 1) is deliberately **separate** from the deployable registry (doc
19): a cached `InstanceState` is *what an instance runs*, the registry is *what the fleet curates and
publishes*. "Adopt" is the explicit bridge.

- **Panel:** `POST /instances/{id}/adopt` `{ kind, artifact_id }` → read the artifact's
  `{content, content_hash}` from the cached `InstanceState` (`InstanceStateStore`), then create a
  registry version via the existing `ArtifactStore` (doc 15) with provenance
  `authored_by = "adopted:instance/{id}"` and the instance's `content_hash` (so an adopted artifact's
  hash lines up with the fleet's — the same canonicalisation `_content_hash` already uses). Idempotent
  on `content_hash` (re-adopting an unchanged artifact is a no-op, returns the existing version).
- **UI:** an "Adopt into registry" action per artifact on the `InventoryCard` (the cached-inventory
  view). After adopt, the artifact is a normal registry version — reviewable, deployable.

This closes the loop the Artifacts page couldn't (doc 19's motivation): observe an externally-authored
artifact → adopt the good ones → deploy them across the fleet.

### 3. Multi-fleet visibility

Serve already exposes `GET /fleet/memberships` (owner-listed, no secrets) and
`DELETE /fleet/membership/{id}` (eject) from Phase 2. This slice surfaces them:

- **UI:** on the instance page, a "Fleet memberships" card listing this instance's memberships
  (fleet id, scope, fingerprint, created) via a panel passthrough (`GET /instances/{id}/memberships`
  → serve `GET /fleet/memberships` over the membership credential), with an eject button
  (`DELETE`). This makes "same swarm, many fleets" visible and revocable from either side.

## API shape

**Panel (new):**

| Method + path | Purpose |
|---|---|
| `POST /instances/{id}/adopt` | Promote a cached observed artifact (`{kind, artifact_id}`) into the registry (a new version). |
| `GET  /instances/{id}/memberships` | Passthrough of serve `GET /fleet/memberships` over the membership credential. |
| `POST /instances/{id}/deploy` (changed) | Now carries the deploy over the membership credential; requires `manage`. |

**Serve (changed):**

| Method + path | Change |
|---|---|
| `PUT /api/{plural}/{id}` | Now accepts a `manage`-scope membership key (via the scope-aware auth-seam fallback), in addition to a `serve:admin` transport token. |
| `GET /fleet/state` | Unchanged (monitor+); the fallback just learns to distinguish monitor vs manage. |

## Test plan

- **Serve:** a `manage` membership key authorizes `PUT /api/topologies/{id}`; a `monitor` key gets
  `403`; a `serve:admin` transport token still works (back-compat); an invalid key `401`.
- **Panel deploy:** `DeployService.deploy` uses `cred_store.get_secret` and refuses when the stored
  scope is `monitor` (`403`) or no membership exists and no `token_ref` (`409`); Mode B still enqueues
  a `deploy` command.
- **Adopt:** `POST /adopt` creates a registry version from the cached artifact with the instance's
  `content_hash` + `adopted:` provenance; idempotent on re-adopt; `404` when the artifact isn't in the
  cache; `409` when nothing is cached yet.
- **Memberships passthrough:** lists what serve returns, leaks no secret; eject calls serve `DELETE`.
- **UI:** adopt action posts `{kind, artifact_id}` and reflects the new version; memberships card
  renders + ejects; deploy surfaces a manage-scope error.

## Demo plan

`examples/` script (or transcript): enroll an instance with `manage` → sync its state → **adopt** one
of its topologies into the registry → **deploy** that version back over the membership credential (no
`serve:admin` token in play) → show the instance's membership listed, then eject it.

## Open questions

1. **Deploy credential precedence.** When both a `manage` membership *and* a legacy `token_ref` exist,
   which wins? *(Lean: membership credential first; `token_ref` only as fallback, with a deprecation
   note — the membership is the scoped, revocable, enrollment-native credential.)*
2. **Adopt provenance + trust.** Should adopt record the source instance + a timestamp in the version
   provenance so an operator can see "this came from instance X" before deploying it fleet-wide?
   *(Lean: yes — `authored_by = "adopted:instance/{id}"` + the cache's `synced_at`.)*
3. **Manage on Mode B.** A poll instance applies `deploy` locally over loopback — does it also need a
   `manage` membership check, or is the panel's enqueue-time scope check sufficient given the
   connector runs trusted on the instance host? *(Lean: panel-side check is sufficient for Mode B; the
   serve-side scope gate is a Mode-A concern.)*
