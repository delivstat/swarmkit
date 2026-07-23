# Trigger

A **trigger** is a first-class SwarmKit artifact (`kind: Trigger`) that describes something which causes one or more topologies to run — a schedule, a webhook, a filesystem watch, a manual "run now", or a third-party plugin. It unifies schedules and triggers under one kind with a `type` discriminator.

The unification decision, per-type config shapes, and what stays out of the schema are specified in the [trigger schema design note](https://github.com/delivstat/swarmkit/blob/main/design/details/trigger-schema-v1.md). This page is the artifact reference.

## Fields

Required top-level: `apiVersion`, `kind`, `metadata`, `type`, `targets`.

| Field | Required | What it does |
|---|---|---|
| `type` | yes | Discriminator: `cron` \| `webhook` \| `file_watch` \| `manual` \| `plugin`. Per-type `config` shape is validated at runtime. |
| `targets` | yes | Topology IDs to fire (at least one). Fired **in parallel**. |
| `enabled` | no | Default `true`. A disabled trigger loads but does not fire — pause without deleting. |
| `provider_id` | conditional | **Required when `type: plugin`** — names a registered `TriggerProvider`. |
| `config` | conditional | **Required for `cron`, `webhook`, `file_watch`, `plugin`** (not `manual`). Type-specific, runtime-validated. `config.auth` is a schema-validated block meaningful only for `webhook`. |

### Type / config summary

| `type` | Fired by | `config` (runtime-validated) |
|---|---|---|
| `cron` | schedule tick | `{ expression: <5/6-field cron>, timezone?: <IANA zone> }` |
| `webhook` | HTTP POST to an endpoint | `{ path, auth?: {...} }` |
| `file_watch` | filesystem change | `{ root, pattern, events: [...] }` |
| `manual` | `swarmkit trigger fire <id>` / UI "Run now" | none required |
| `plugin` | a registered `TriggerProvider` | `provider_id` + arbitrary config |

### Webhook `auth`

Optional, and the one config field the schema validates. Required members `method` (`hmac` \| `bearer` \| `api_key`) and `credentials_ref` (the name of a workspace `credentials` entry holding the secret); optional `header`. Secrets are never stored on the trigger — only referenced by name.

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: <lowercase-kebab>
  name: <human name>
  description: <optional>
type: cron                       # cron | webhook | file_watch | manual | plugin
enabled: true                    # default true
targets: [<topology id>, ...]    # at least one; fired in parallel
provider_id: <provider id>       # required only when type: plugin
config:                          # required for cron/webhook/file_watch/plugin
  expression: "0 9 * * 1-5"
  timezone: Europe/London
```

## Examples

Smallest valid (a manual trigger needs no config):

```yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata: { id: run-review, name: Run Code Review }
type: manual
targets: [code-review-swarm]
```

An HMAC-authenticated GitHub webhook:

```yaml
apiVersion: swarmkit/v1
kind: Trigger
metadata:
  id: github-pr-review
  name: GitHub PR Review
  description: Runs the review swarm on every pull_request event.
type: webhook
targets: [code-review-swarm]
config:
  path: /hooks/github-pr
  auth:
    method: hmac
    credentials_ref: github-webhook-secret
```

## Not in the schema

Secrets (referenced by `auth.credentials_ref`, never stored inline), input-to-topology mapping semantics, trigger chaining, retry/DLQ, and rate limiting are all runtime or governance concerns — the schema captures shape only. By convention cron-ish triggers live in a `schedules/` directory and everything else in `triggers/`; the runtime reads both.

## See also

- [Trigger schema design note](https://github.com/delivstat/swarmkit/blob/main/design/details/trigger-schema-v1.md) — the unification rationale, per-type config, and plugin path.
- [Triggers & Canary tutorial](../tutorials/12-triggers-canary.md).
