---
title: Trigger schema v1
description: Unified schedules + triggers. Cron / webhook / file_watch / manual / plugin discriminator.
tags: [schema, trigger, m0]
status: implemented
---

# Trigger schema v1

**Scope:** `packages/schema`
**Design reference:** §5.4 (schedules and triggers as first-class), §9.3 (workspace layout — `schedules/`, `triggers/`), §14.1 (scheduled execution mode).
**Status:** in review

## Goal

Define the `Trigger` artifact — a workspace-level descriptor for "something that causes one or more topologies to run." Fifth and final schema for Milestone 0.

## Non-goals

- **Input-to-topology mapping semantics** — how a webhook body becomes a topology's input, how a cron tick becomes a timestamp argument, etc. — is runtime behaviour (M9 when HTTP server + scheduler lands). Schema captures shape.
- **Trigger chaining** (trigger A fires trigger B) — not a v1 feature.
- **Event-driven triggers from Kafka / SQS / webhooks.example.com / whatever else** — reachable via `type: plugin` once the runtime trigger plugin path lands; not part of v1 built-ins.
- **Retry / DLQ semantics** — runtime concern; use topology's `runtime.max_concurrent_tasks` + governance policy.

## Design decision — `Trigger` unifies schedules and triggers

The v0.6 design uses "Schedules and Triggers" as if they were two artifact types, with separate directories in §9.3. In practice every "schedule" is a cron-type trigger, so the schema **unifies them under a single `Trigger` kind with a `type` discriminator**. The workspace directory split (`schedules/` vs `triggers/`) is organisational convention — both contain `kind: Trigger` artifacts, cron-ish things conventionally in `schedules/`, everything else in `triggers/`. Runtime reads both.

This keeps one shape, one registry, one discriminator enum. If a future v2 needs true separation, adding a second kind is additive, not breaking.

## API shape

### Top-level structure

```yaml
apiVersion: swael/v1
kind: Trigger
metadata:
  id: daily-code-review
  name: Daily Code Review
  description: |
    Runs the Code Review Swarm every weekday morning against the current
    main branch of the three core repos.
type: cron
enabled: true
targets:
  - code-review-swarm
config:
  expression: "0 9 * * 1-5"
  timezone: Europe/London
```

### `type` — the discriminator

Required. Exactly one of:

| Value | Fired by | Config shape (runtime-validated) |
|---|---|---|
| `cron` | Schedule tick at a given cadence | `{ expression: <5- or 6-field cron>, timezone?: <IANA zone> }` |
| `webhook` | HTTP POST to an exposed endpoint | `{ path, auth?: {...}, credentials_ref?: <workspace credential name> }` |
| `file_watch` | Filesystem change event | `{ root, pattern, events: [created, modified, deleted, moved] }` |
| `manual` | `swael trigger fire <id>` or the UI "Run now" button | no config required |
| `plugin` | Third-party `TriggerProvider` registered via entry points | `provider_id` required + arbitrary `config` |

Matches the uniform `{ source/type, config }` pattern already established for credentials and model-providers — future trigger backends go through `type: plugin` with a `provider_id`, graduating to built-in only when wide enough demand exists.

### `targets`

Required. Array of topology IDs (lowercase-kebab identifier pattern) to fire when the trigger activates. At least one; ordering is unspecified — fired in parallel by the runtime.

Multiple targets enable the §5.4 statement: "the same swarm can be triggered by multiple schedules, and one schedule can trigger multiple swarms." Second half is this field.

### `enabled`

Optional, defaults `true`. Disabled triggers are loaded by the runtime but do not fire — useful for temporarily pausing a trigger without deleting it (good for audit).

### `config` per type

Schema validates the discriminator + presence of `config` when the type demands it; per-type config shape is runtime-validated (same choice as workspace credentials — avoids rigid nested `oneOf` over every type, keeps the schema flat and readable).

The v1.0 **webhook** type deserves one extra field in the schema: `auth`, because HMAC / bearer / api-key variants are common and the runtime needs schema-level validation to enforce that a secret reference exists when `auth` is configured. `auth` is optional; if present, it's `{ method: hmac | bearer | api_key, credentials_ref: <string> }`. Reuses the workspace `credentials` block for secret storage — no duplication of SecretsProvider shape.

### `plugin` type

```yaml
type: plugin
provider_id: acme-kafka-trigger
config:
  bootstrap_servers: [kafka.acme.internal:9092]
  topic: agent-tasks
  group_id: swael-consumer
```

Registered via Python entry points group `swael.trigger_providers` — mirror of ModelProvider / SecretsProvider. Schema enforces `provider_id` presence when `type: plugin`.

## What's not in the schema

- **Secrets** — triggers that need credentials (e.g. webhook HMAC validation) reference workspace `credentials` by name via `auth.credentials_ref`. No per-trigger secret storage.
- **Input mapping specifics** — covered by topology-side input declarations (§10.2) and runtime glue (M9).
- **Priority / ordering** — trigger fires are atomic; running topologies parallelise per the topology's `runtime.max_concurrent_tasks`.
- **Rate limiting** — governance-layer concern (§8.6 token budget and §16.2 PBAC).

## Test plan

Following `docs/notes/schema-change-discipline.md`:

- **Valid fixtures** under `packages/schema/tests/fixtures/trigger/`:
  - `manual.yaml` — smallest valid (type manual, no config).
  - `cron-daily.yaml` — basic schedule.
  - `cron-with-timezone.yaml` — IANA zone specified.
  - `webhook-github-pr.yaml` — HMAC-authenticated GitHub webhook firing the Code Review Swarm.
  - `file-watch-repo.yaml` — file_watch over a repo for rebuilds.
  - `multi-target.yaml` — one trigger fires multiple topologies.
  - `disabled.yaml` — exercises `enabled: false`.
  - `plugin-kafka.yaml` — `type: plugin` with `provider_id`.
- **Invalid fixtures** under `packages/schema/tests/fixtures/trigger-invalid/`:
  - `missing-type.yaml`
  - `bad-type.yaml` — outside the enum
  - `missing-targets.yaml`
  - `empty-targets.yaml` — `targets: []` rejected (minItems: 1)
  - `bad-target-id.yaml` — target not matching identifier pattern
  - `plugin-missing-provider-id.yaml` — `type: plugin` requires `provider_id`
  - `webhook-auth-missing-credentials-ref.yaml` — `auth: { method, }` without `credentials_ref`
  - `cron-missing-config.yaml` — `type: cron` requires `config.expression`
- **Python test:** extends `test_schemas.py` with parametrised trigger cases.
- **TS test:** single `describeFixtures` line.

## Demo plan

`just demo-trigger-schema` via the existing parametrised runner.

The aggregate `just demo-schema` meta-target covers all five schemas after this PR lands — **M0's exit demo** is functionally complete once this merges; Task #16 is a formality (already wired, just a README paragraph).

## Open questions

- **6-field cron.** Quartz-style 6-field (with seconds) is sometimes wanted. Schema does not enforce field count — the runtime validates per-scheduler-library rules. Revisit if confusion emerges.
- **Webhook path uniqueness.** Two triggers with the same path cause collisions. Runtime-level check at workspace load; not a schema concern.
- **File-watch reliability.** Not all filesystems emit the same event shape (inotify vs ReadDirectoryChangesW). Runtime normalises; schema treats as best-effort.

## Follow-ups

- Pydantic codegen (Task #14).
- TS codegen (Task #15).
- Aggregate demo README (Task #16) — mostly a documentation pass.
- Runtime trigger plumbing (M9) — HTTP server, scheduler, file-watcher, manual command.
