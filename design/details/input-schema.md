---
title: input_schema — validate the caller's input before any agent runs
description: An optional topology-entry JSON Schema, symmetric to output_schema but validate-and-reject (a caller cannot be re-prompted), that fails a run fast at every entry point before a token is spent.
tags: [topology, validation, governance]
status: proposed
---

# input_schema — validate the caller's input before any agent runs

## The gap

`output_schema` gives a free, deterministic shape check on what an agent produces, with a
field-specific re-prompt to fix it (`structured-output-governance.md`). There is no counterpart for
what a caller *sends in*. Deterministic input validation today means authoring a validator tool and
binding it as a `pre_input` decision skill — heavy for "reject a malformed request." A malformed
request instead starts a billable run that fails somewhere inside.

## Goal

An optional `input_schema` on a topology: a JSON Schema the caller-supplied input must satisfy
*before the run starts*. On failure the run never starts — a field-specific error at the entry
point (422 over HTTP, non-zero exit on the CLI, a JSON-RPC error over A2A), no LLM spend, an
`input.rejected` audit event. On success, `input.validated`. Opt-in; a topology that sets nothing
behaves exactly as today.

## Non-goals

- **Not per-agent.** `output_schema` is per-agent because every node produces output; input is
  caller-supplied only at the *entry*. A downstream node's input is assembled by the runtime
  (predecessor outputs, delegation), so a per-node input schema would validate machine-made
  strings. `input_schema` lives on the **topology**, checked once at the boundary.
- **Not validate-and-correct.** `output_schema` re-prompts the model; a caller cannot be
  re-prompted mid-run. `input_schema` is validate-and-**reject**, fail fast. Simpler (no retry
  loop) and the point — a bad request never becomes a run.
- **Not a forced-JSON gate on the conversational path.** Input is often natural language. The
  field is opt-in; `{"type": "string", "minLength": 1}` asserts "non-empty text" without forcing
  JSON, and a topology that omits it is unaffected.

## API shape

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata: { name: triage, version: 0.1.0 }
input_schema:                      # optional; JSON Schema (draft 2020-12), same dialect as output_schema
  type: object
  required: [ticket_id, severity]
  properties:
    ticket_id: { type: string }
    severity: { enum: [P0, P1, P2] }
agents: { ... }
```

- Added to `topology.schema.json` as an optional object (a JSON Schema), mirroring `output_schema`.
- Validated at the single choke point — `WorkspaceRuntime.run()` — so every entry inherits it:
  `POST /run`, `POST /hooks`, A2A `message/send`, `swarmkit run --input`, triggers.
- The input is a string (`run(user_input: str)`); when it parses as JSON it is validated as the
  parsed value, otherwise as the string. `{"type": "string"}` validates raw text; an object schema
  requires JSON and rejects non-JSON with "input must be a JSON object matching input_schema".
- Reuses `skills/_output_validator.validate_all_skill_output` (rename to a neutral
  `validate_against_schema`); field-specific errors go into the rejection message and the audit
  payload. A new `InputValidationError` maps to 422 / exit code / JSON-RPC error per entry.

## Relationship to the pre_input decision skill

`input_schema` is the deterministic **shape** shortcut; a `pre_input` decision skill remains for
**meaning** or computed checks (an allow-list lookup, a policy call). Exactly mirrors
`output_schema` vs a `post_output` decision skill. Ordering: `input_schema` runs first (cheapest,
structural); a `pre_input` skill runs only on input that already has the right shape.

## Test plan

- Schema fixture: a topology with `input_schema`; a valid input runs, an invalid one raises
  `InputValidationError` with the offending field named and **no agent node executed** (assert the
  model provider recorded zero calls).
- Each entry point maps the error: `POST /run` → 422 with the field path; CLI → non-zero + message;
  A2A `message/send` → a JSON-RPC error, no job created.
- `input.rejected` / `input.validated` audit events (durable via the journal).
- A `{"type": "string"}` schema accepts plain text; an object schema rejects non-JSON.

## Demo plan

Extend the governance tutorial (Level 7) or `validating-topology-output.md`: a topology whose
`input_schema` rejects `severity: critical` before any run starts, shown over CLI and `POST /run`.
