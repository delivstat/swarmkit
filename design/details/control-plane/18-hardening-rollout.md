# 18 — Phase 8: Hardening + rollout

The path to GA: security review, migrating the existing instances, and the operational story.
Builds on all prior phases.

## Security review (gate before GA)

- **Auth surface** ([12](12-auth.md)): per-route scope enforcement actually wired; default-secure on
  non-loopback verified; no token grants a reserved-for-human governance scope (the guard); tokens
  are refs, never literals; rotation/revocation tested.
- **Separation of powers**: artifact push + activation are human-gated and audited; the panel cannot
  bypass `evaluate_action` or mutate audit ([05](05-identity-governance-iam.md)).
- **Audit completeness**: every panel-triggered run + mutation lands in the central append-only log
  with the acting `client_id` and `instance_id` ([14](14-aggregation.md)).
- **Transport**: TLS on panel↔instance + human↔panel; CORS locked down (no `*` in production,
  [02](02-serve-api.md)); secrets via `SecretsProvider`/env only.
- **Blast radius**: per-instance scoped tokens; compromise of one is contained.

## Rollout to existing instances (Minder, Sterling, vedanta)

These are the first real fleet. Per instance: enroll ([13](13-connector-registry.md)) → set
`server.auth` tokens → point `telemetry.endpoint` at the fleet collector → register current
artifacts in the registry ([15](15-artifact-registry.md)).

- **Minder** is loopback (`127.0.0.1:8321`) and behind a home network → stays `none`-auth-OK locally;
  to be *controlled* by the panel it needs reachability (Tailscale/VPN) + a token, or it remains
  **observe-only** via push/heartbeat ([13](13-connector-registry.md) reachability constraint).
- Sterling / vedanta: enroll with tokens; if reachable, full control; else observe-only.

## The breaking change (communicate)

Default-secure ([12](12-auth.md)) makes a **non-loopback bind with `provider: none` refuse to
start**. Document it, provide the `require_on_nonloopback: false` / `--insecure` escape hatch, and
call it out in release notes for anyone currently running serve open on `0.0.0.0`.

## Operational story

- **Control-plane app versioning** + upgrade path; schema-skew handling across instances
  ([07](07-schema.md), [15](15-artifact-registry.md)).
- **Backup/restore** for the central stores (audit Postgres, registry git+DB).
- **Runbooks**: enroll/revoke an instance, rotate tokens, recover an unreachable instance, roll back
  a bad deploy, read the audit trail for an incident.
- **Docs**: operator guide + the OSS single-instance vs fleet-panel boundary ([09](09-ui.md)).

## Exit criteria (GA)

Security review passed; the three existing instances enrolled (control or observe-only as
reachability allows); central audit/eval/usage aggregating; registry deploying with human gates;
the growth loop ([17](17-growth-loop.md)) demonstrable end-to-end on one gap; docs + runbooks
published.

## Open questions

- HA for the control plane itself (single vs replicated; the central stores' availability).
- Multi-tenant hardening (the `org`/`team` schema hooks, [07](07-schema.md)) — likely post-GA.
