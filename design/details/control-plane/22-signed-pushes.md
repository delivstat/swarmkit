# Signed pushes — a stolen membership key alone can't deploy

Status: design, pending review. Builds on [21](21-fleet-identity.md) (pinned fleet keys) and
[20](20-manage-and-adopt.md) (governed deploy over the membership credential). The next slice fleet
identity enables.

## Goal

Today a `manage`-scope **membership key** is sufficient to deploy an artifact to an instance (design
20). That key is a bearer token — if it leaks, the holder can push arbitrary artifacts. Require the
fleet to **sign** each deploy with its **private key** (the one behind its pinned public key, design
21). The instance verifies the signature against the pinned key before applying. Now a stolen
membership key is not enough: the attacker also needs the fleet's private key, which never leaves the
panel.

Defence in depth: the membership key still *authenticates + authorizes* the call (scope); the
signature *attributes + authorizes the payload* to the pinned fleet identity.

## Threat model

| Attack | Design 20 | With signed pushes |
|---|---|---|
| Membership key leaks → push malware | Possible | Rejected: no valid signature over the payload |
| MITM swaps the artifact in flight | (TLS assumed) | Also rejected: signature covers the content hash |
| Replay a *previously signed* deploy of the same artifact | Applies it again (idempotent, benign) | Same — idempotent |
| Replay an **older** signed version over a newer one (downgrade) | Possible | **See open question 2** |

Out of scope: protecting the fleet private key at rest (SecretBox already does), or the transport
(TLS is the deployment's job).

## Design

### What is signed

The fleet signs a compact, content-bound statement — **not** the raw bytes — so both sides agree
regardless of wire encoding:

```
message = f"deploy:{kind}:{artifact_id}:{content_hash}"
signature = ed25519_sign(fleet_privkey, message)          # base64
```

`content_hash` is the **same canonicalisation** the artifact registry + `InstanceState` already use
(sorted-keys compact JSON of the content dict — `_content_hash`), so the panel signs the registry
version's hash and the instance recomputes it from what it received and verifies. Binding `kind` +
`artifact_id` stops a signature for artifact A being replayed onto artifact B.

### Where the signature travels

An `X-Fleet-Signature` header on the deploy `PUT /api/{plural}/{id}` (Mode A). Mode B carries it in
the enqueued `deploy` command's args. The header keeps the artifact payload unchanged.

### Verification (serve)

1. The deploy PUT is already authenticated by the membership key (design 20 auth-seam fallback). The
   fallback now **stashes the authenticated membership** on `request.state` so the handler can reach
   `fleet_id`.
2. The handler looks up the **pinned public key** for that membership's `fleet_id`
   (`MembershipStore.get_fleet_key`).
3. It recomputes `content_hash` from the received content and verifies the `X-Fleet-Signature` over
   `deploy:{kind}:{id}:{content_hash}` against the pinned key.
4. **Opt-in enforcement** (`SWARMKIT_FLEET_REQUIRE_SIGNED_DEPLOY`, mirrors design 21's identity
   toggle): when set, an unsigned or invalid deploy is rejected (`401`); when unset (default), a
   *present* signature is still verified (reject on invalid) but absence is allowed — opportunistic,
   backward-compatible.

### Signing (panel)

`DeployService.deploy` (Mode A) already resolves the manage membership credential (design 20). It
now also signs `deploy:{kind}:{id}:{content_hash}` with the panel `FleetIdentity` and passes the
signature to `push_artifact`, which sends it as `X-Fleet-Signature`.

## The pre-existing wire mismatch (must resolve)

`push_artifact` PUTs the registry `content` **dict** to `/api/{plural}/{id}`, but serve's handler
reads `body.get("yaml", "")` — so today a Mode-A `/api` deploy sends content the handler ignores
(every deploy test stubs the connector, so this was never exercised end-to-end). Signed pushes force
us to make the deploy payload real and agreed. **Proposal:** the panel sends
`{ "content": <dict>, "content_hash": <hash> }` (or `{ "yaml": <dump> }`), and serve reads the
content, recomputes the hash, verifies the signature, then applies. Pin the exact body shape as part
of this work so the content_hash both sides sign/verify is unambiguous.

## API shape

**Serve:** `PUT /api/{topologies,skills,archetypes}/{id}` gains an optional `X-Fleet-Signature`
header + a defined body carrying the content; verifies against the pinned fleet key.

**Panel:** `DeployService.deploy` signs; `push_artifact` sends the header. No new route.

**Schema:** none — this is a header + an existing body; document it in doc 20/22.

## Test plan

- **Sign/verify unit:** a valid signature over `deploy:kind:id:hash` verifies against the fleet key;
  a wrong key / wrong hash / wrong kind fails.
- **Serve deploy:** a manage membership + a valid fleet signature applies; an **invalid** signature
  is rejected even without the enforce flag; an **absent** signature is allowed by default but
  rejected under `require_signed_deploy`; a signature by a **non-pinned** key fails.
- **Panel:** `DeployService.deploy` attaches a signature the serve verifier accepts (cross-package,
  like design 21's `fleet_id` contract test).
- **Wire:** the agreed body shape round-trips (panel content → serve hash matches the signed hash).

## Demo plan

`examples/` transcript: deploy a published version with a valid fleet signature → applied; tamper the
content in flight (hash mismatch) → rejected; present the membership key without a signature under
`require_signed_deploy` → rejected.

## Decisions (resolved at review)

1. **Wire body → `{content}` dict, serve recomputes.** The panel sends the artifact content dict;
   serve recomputes `content_hash` with the registry canonicalisation and verifies the signature
   over it. One hash definition, no YAML round-trip ambiguity — and it fixes the wire mismatch.
2. **Downgrade → accept replay for v1.** Deploys are operator-published + idempotent; a monotonic
   deploy-sequence guard is a follow-up if needed.
3. **Enforcement → follows `require_identity`.** `SWARMKIT_FLEET_REQUIRE_SIGNED_DEPLOY`, when set,
   wins; **otherwise it defaults to whatever `require_identity` is** (an instance that already
   requires a fleet identity also requires signed deploys). When not required, a *present* signature
   is still verified (reject on invalid); absence is allowed.
4. **Mode B → sign it too.** The enqueued `deploy` command carries the signature; the connector
   verifies it against the pinned key before applying locally. Closes the same gap for poll
   instances (cheap).

## Open questions (deferred)

- **Monotonic downgrade guard** — bind the signature to a deploy sequence so an old signed deploy
  can't be replayed over a newer one.
