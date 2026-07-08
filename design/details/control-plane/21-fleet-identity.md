# Fleet identity — self-certifying `fleet_id` + pinned public keys

Status: design, pending review. Resolves [19](19-fleet-enrollment-protocol.md) open question 1
("how does an instance identify a fleet trustably"). Builds on the enrollment handshake (doc 19) and
the membership model.

## Goal

Make `fleet_id` mean something a fleet can't forge. Today it is an opaque string the panel presents
at register (default `swarmkit-fleet`) — the instance records it but never verifies the caller *is*
that fleet. Give each fleet an **Ed25519 keypair**, derive `fleet_id` from its **public key**
(self-certifying), have the fleet **prove possession** of the private key at register, and have the
instance **pin** the public key on the membership (trust-on-first-use). Then:

- **"Same swarm, many fleets" is trustworthy.** Two fleets can't collide on a `fleet_id`; the id
  *is* the key fingerprint.
- **Re-enrollment is verifiable.** A fleet that re-registers (after a panel migration, or a second
  instance) presents the same `fleet_id` **and** proves the same key — the instance/operator sees a
  known identity, not a claimable label.
- **A foundation for attributing pushes.** Once the instance holds the fleet's pinned public key, a
  later slice can require the fleet to *sign* deploys, so a stolen membership key alone can't push.

## Threat model — what pinning defends against

| Attack | Today | With pinned keys |
|---|---|---|
| A rogue client registers claiming `fleet_id: acme-prod` | Accepted — id is a free string | Rejected: it can't produce a proof for acme-prod's key |
| A fleet's membership key is stolen and used to re-register as that fleet | Possible | The thief lacks the private key → can't prove the identity on a fresh enrollment |
| Operator can't tell fleet A from a look-alike | id is cosmetic | id = key fingerprint; the pin is verifiable |

Non-threats (out of scope here): a stolen *membership key* still authenticates existing scoped calls
(that's the credential's job, revoked via rotate/leave); protecting the panel's private key at rest
is delegated to the existing SecretBox.

## Non-goals

- **No signed pushes yet.** Requiring the fleet to sign each deploy (so a stolen membership key can't
  push) is the natural next slice; this one establishes the pinned identity it would build on.
- **No PKI / CA.** TOFU (pin-on-first-use, like SSH `known_hosts`), not a certificate authority.
- **No change to the credential/auth model.** The issued membership API key still authenticates
  scoped calls; identity is an *additional*, orthogonal binding.
- **No key escrow / recovery.** A lost fleet private key means re-enrolling (a new identity) — same
  as losing an SSH key.

## Design

### 1. Fleet keypair + self-certifying `fleet_id`

The panel (fleet) owns one **Ed25519** keypair (`cryptography` — already a dependency via Fernet; no
new lib). The **`fleet_id` is derived from the public key**:

```
fleet_id = "fleet:" + base32(sha256(pubkey_raw))[:52]     # ~256-bit id, stable, self-certifying
```

The private key is stored **encrypted at rest** via the panel's existing `SecretBox` (Fernet local /
Vault-Transit) — the same seam that protects membership credentials. Generated on first use; one
identity per panel. (An operator can also supply an existing key to keep a stable identity across
panel rebuilds.)

### 2. Augmented register handshake — proof of possession

Register (Mode A) gains two fields — the fleet's public key and a signature proving it holds the
matching private key:

```
POST {serve}/fleet/register
  Bearer <enrollment token>
  { fleet_id, fleet_public_key, proof, requested_scope? }
      proof = ed25519_sign(privkey, enrollment_token)      # binds the proof to THIS one-time token
```

Serve verifies, in order:
1. `fleet_id` equals the fingerprint derived from `fleet_public_key` (the id is self-certifying).
2. `proof` verifies against `fleet_public_key` over the (single-use) enrollment token — so a
   replayed public key without the private key fails, and the proof can't be lifted to another join.
3. Then the existing flow: consume the token, issue the membership, return state.

Signing the **enrollment token** (already one-time + TTL-bounded) gives freshness for free — no extra
nonce round trip. Off-loopback, the public key + proof are **required**; on loopback they stay
optional (mirrors default-secure, doc 12 §8), so local dev is unchanged.

### 3. Pinning + mismatch (trust-on-first-use)

The instance stores the fleet's public key on the membership (`MembershipStore`). Pinning is
per-`fleet_id`:

- **First time** an instance sees a `fleet_id` → pin its public key.
- **Re-register with the same `fleet_id`:** the public key **must match** the pin. A mismatch is
  rejected (`409 fleet identity changed`) — the SSH-`known_hosts` moment. Re-keying is a deliberate
  `DELETE /fleet/identity/{fleet_id}` (owner/admin) then re-enroll.
- Because `fleet_id` = fingerprint(pubkey), a mismatch can only happen via a genuine key change or an
  attempted impersonation — the two cases we want to surface.

### 4. Mode B (instance-initiated join)

Symmetric: the panel is the resource the instance joins, so the panel presents its fleet identity in
the join **response** (`fleet_id` + `fleet_public_key`), and the connector pins it — so the edge
trusts the fleet it polls. The join code remains the join's authorization.

### 5. Where keys live

- **Panel:** the fleet private key, encrypted via `SecretBox` (new `fleet_identity` row, same crypto
  as `instance_credential`). The public key + `fleet_id` are non-secret.
- **Instance (serve):** the pinned fleet public key added to the `memberships` record (public, no
  secret). Surfaced (no secret) in `GET /fleet/memberships`.

## Backward compatibility

Additive + default-secure:
- On **loopback** or when the panel has no identity configured, register works exactly as today
  (unauthenticated `fleet_id`) — existing tests and the local demo are unchanged.
- **Off-loopback**, an instance that has `server.auth` on **may require** proof (a
  `server.fleet.require_identity` flag, default following the auth posture). Instances that don't
  require it still *pin opportunistically* when a key is presented.
- Existing memberships (no pinned key) keep working; the pin is filled on the next re-register.

## API shape

**Serve:**
| Method + path | Change |
|---|---|
| `POST /fleet/register` | body gains `fleet_public_key`, `proof`; verifies + pins |
| `GET /fleet/memberships` | each entry gains `fleet_public_key` (non-secret) |
| `DELETE /fleet/identity/{fleet_id}` | (admin) unpin a fleet key to allow a deliberate re-key |

**Panel:**
| Method + path | Change |
|---|---|
| `GET /fleet/identity` | this panel's `{fleet_id, fleet_public_key}` (no private key) |
| register/join connector calls | attach `fleet_public_key` + `proof`; pin the instance/fleet key |

**Schema:** extend `packages/schema/schemas/protocol/register-request.schema.json` with the optional
`fleet_public_key` + `proof`; add a `fleet-identity.schema.json` for `{fleet_id, fleet_public_key}`.

## Test plan

- **Identity:** `fleet_id` derives deterministically from a pubkey; a tampered key → different id.
- **Proof:** a valid proof over the enrollment token passes; a proof by the wrong key fails; a proof
  for a *different* token fails (no lift/replay).
- **Pinning:** first register pins; a second register with the same key passes; the same `fleet_id`
  with a different key → `409`; unpin then re-key succeeds.
- **Back-compat:** loopback register with no key still works; off-loopback with `require_identity`
  rejects a keyless register.
- **Mode B:** the connector pins the panel's fleet key from the join response.
- **Cross-language schema:** register-request with `fleet_public_key`+`proof` validates in both langs.

## Demo plan

`examples/` script (or transcript): a panel mints its identity (`GET /fleet/identity`), registers with
proof → instance pins it; a rogue client claiming the same `fleet_id` without the key is rejected; the
panel re-registers a second instance and both show the same verifiable `fleet_id`.

## Open questions

1. **`fleet_id` form.** Pure key fingerprint (`fleet:<b32>`) — self-certifying but opaque — vs an
   operator-friendly label *bound to* a key (`acme-prod` + pinned key, label is cosmetic). *(Lean:
   fingerprint id; carry an optional human `display_name` alongside.)*
2. **Require vs opportunistic off-loopback.** Default `server.fleet.require_identity` to on when
   `server.auth` is on, or keep it opt-in for a release? *(Lean: opt-in one release, then flip.)*
3. **Rotation.** Fleet key rotation = new `fleet_id` (new identity, re-pin everywhere) vs a signed
   rotation record chaining old→new key? *(Lean: new identity for v1; chained rotation later.)*
4. **Sign more than the token?** Bind the proof to `fleet_id`+`instance endpoint` too, not just the
   token, to prevent a proof minted for instance A being replayed at instance B in the same TTL
   window? *(Lean: include the instance's `workspace_id` in the signed payload.)*
