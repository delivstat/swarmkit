# A named operator under `auth: none`

Status: implemented (runtime 1.140.0). Relates to `control-plane/12-auth.md` and
`human-decision-comments.md`.

## The gap

There was no usable middle ground between the two auth modes for a local workspace:

| mode | identity | authority | can resolve a gate? |
| --- | --- | --- | --- |
| `none` | hardcoded `anonymous` | `*`, `authorize()` returns `True` unconditionally | **no** |
| `jwt` | `sub` from the token | scopes from the token | yes — but needs an identity provider |

`api_key` is not an option at all: `approvals:resolve` is a reserved scope, so `APIKeyAuthProvider`
raises at construction if granted one.

So a single operator on a laptop had to stand up Authentik or Keycloak purely to click Approve on
their own machine. That is disproportionate, and it is why the approval path went unexercised in
practice.

## Why the old behaviour was odd rather than safe

`NoneAuthProvider` already trusted the caller absolutely: wildcard scopes, and an `authorize()` that
returns `True` for everything. Nothing was withheld — except a name.

And the name is what the approval engine actually checks. It does not consult scopes: it matches
`client_id` against `members:` in `swarm/roles.yaml`. So `anonymous` was refused not because it
lacked authority but because it was **nobody** — leaving a mode that permits every action unable to
perform the one action that depends on identity.

That is not a security property. It is a mode that says yes to everything and then cannot answer
"who said yes".

## The connection to the rework bug

This is the root cause behind the dropped-comment bug fixed in 1.137.0, not merely adjacent to it.
Because `/review/{item}/resolve` was unusable under `auth: none`, callers fell back to enqueuing a
`rework` controller event — and that break-glass path was the one discarding reviewer comments.
Fixing this removes the reason that path is reached at all.

## Design

Optional `identity` / `identity_name` under `server.auth.config`, defaulting to `anonymous`:

```yaml
server:
  auth:
    provider: none
    config:
      identity: srijith
      identity_name: Srijith Kartha
```

Config lives under `config:` rather than directly on `auth:` (as the original request proposed)
because that is where every other provider's settings already live — `keys` for `api_key`,
`issuer`/`audience` for `jwt`.

**Naming grants no capability this mode does not already give.** The identity still carries
`scopes={"*"}` and `authorize()` still returns `True`; a test asserts a named identity is identical
in authority to `anonymous`.

### Guardrails

- **Loopback only, still.** `create_app` already refuses `provider: none` on a non-loopback bind
  via an `isinstance` check, which covers the named case unchanged. A test pins it: a named identity
  must not become a way to turn a local convenience into an open door with somebody's name on it.
- **Asserted, not authenticated.** The identity carries `provider="none"`, which is what lets an
  audit reader distinguish "srijith approved this, verified by an IdP" from "someone on the loopback
  interface claimed to be srijith". Nothing is misrepresented.
- **Warn when the name matches no role member.** A role-check 403 is indistinguishable from being
  unauthenticated, so an operator debugs their token when the real problem is a name.

### What the warning does not cover

Only providers with a *static* asserted identity can be checked at startup, which today means
`none`. The same trap exists under `jwt` — most identity providers put a UUID in `sub` by default,
which authenticates fine and then fails every role check — but that identity arrives per request, so
catching it needs a check at resolve time. Left open deliberately rather than half-done.

## Test plan

`packages/runtime/tests/test_none_auth_identity.py`:

- the default is still `anonymous`, including for blank/whitespace config
- the identity can be named; the display name defaults to the identity
- naming grants no extra capability (same scopes, same `authorize()`)
- the assertion is labelled `provider="none"`
- **a named operator can resolve a gate that `anonymous` cannot** — the point of the change
- a named operator who is not a member is still refused; the registry stays the authority
- the registry factory passes it through, and still works with no arguments
- the workspace schema accepts the new keys
- a named identity does not escape the loopback refusal
- an identity matching no role member warns; a listed one does not; a workspace with no roles
  does not

Schema: `workspace/with-named-local-operator.yaml` (valid) and
`workspace-invalid/auth-unknown-config-key.yaml` (a misspelled key must be rejected, not ignored —
a silently dropped auth setting leaves the operator staring at a 403).

## Demo

`just demo-named-operator` — the same gate, refused for `anonymous` and resolvable for a named
operator, with the audit distinction shown.
