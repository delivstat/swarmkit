# 07 — Schema

Scope: `packages/schema/` — five canonical JSON Schemas (source of truth), Python + TS validators,
codegen. Versioned independently of the runtime (`swarmkit-schema` 1.5.0 / `@swarmkit/schema` 0.0.1).

## The five artifact types

- **Topology** (`topology.schema.json`) — the swarm. `metadata`, `runtime` (mode
  `one-shot|persistent|scheduled`, planning, synthesis, checkpointing), `agents` (root + children
  tree; per-agent `model`, `prompt`, `skills`/`skills_additional`, `iam.base_scope`/`elevated_scopes`,
  `output_schema`, `children[].depends_on` DAG), `artifacts`, `intent_monitoring`, `governance`
  (topology-level decision-skill bindings).
- **Skill** (`skill.schema.json`) — `category` (capability/decision/coordination/persistence),
  `inputs`/`outputs` (decision skills require `reasoning`), `implementation` (oneOf
  `mcp_tool`/`llm_prompt`/`composed`), `iam.required_scopes`, `constraints`, `audit`, `provenance`.
- **Archetype** (`archetype.schema.json`) — `role`, `defaults` (model/prompt/skills/iam/output_schema;
  skills may be abstract placeholders), `provenance`.
- **Workspace** (`workspace.schema.json`) — see surface below.
- **Trigger** (`trigger.schema.json`) — `type` (cron/webhook/file_watch/manual/plugin), `enabled`,
  `targets`, `config` (incl. webhook `auth`), `provider_id`.

## The workspace.yaml surface (per-instance config the panel manages)

Top-level blocks: `metadata` (req), `organisation`, `team` (multi-tenant forward-compat),
`governance` (provider, policy_language, config, limits, decision_skills), `identity` (provider
incl. `oidc`), `model_providers[]`, `credentials{}` (source+config; never literal), `mcp_servers[]`
(stdio/http, sandboxed, permission tiers), `storage` (checkpoints/audit/runtime/knowledge_bases
backends), `context_compression` (backend/min_bytes/overrides), `planning`, `synthesis`, `server`
(jobs, mcp, canary). **Not present: `server.auth`** (the CLI reads it but the schema doesn't define
it — see [02](02-serve-api.md)).

## Versioning, validators, codegen

- `apiVersion: swarmkit/v1` on every artifact; breaking changes → `v2` + migration note.
- Validators consume the JSON Schemas directly: Python `swarmkit_schema.validate(name, instance)`
  (jsonschema, draft 2020-12); TS `@swarmkit/schema.validate` (ajv). Bundled copy at
  `python/src/swarmkit_schema/_schemas/` (must be kept byte-identical — dev/test reads it first).
- Codegen: pydantic (`scripts/codegen_pydantic.py`) + TS types
  (`typescript/scripts/codegen-types.mjs`); CI drift checks. **Shape vs full-validation gap:**
  generated models don't encode `allOf`/`if-then` — only `validate()` enforces those.
- Discipline: `docs/notes/schema-change-discipline.md` (edit schema → sync bundled copy → fixtures
  → regen → commit).

## Control-plane implications

- **Centrally versioned:** topologies (already `(name, version)` + canary). The registry should add
  versioning + provenance for **skills/archetypes** too (today they're id-immutable, no versions).
- **Workspace is a per-instance singleton** the panel manages/pushes; needs a `server.auth` schema
  block added (auth-hardening phase) so the panel can configure instance auth declaratively.
- **Schema-version skew across instances** is a real fleet concern: the panel must track each
  instance's `swarmkit-schema` version and refuse to push artifacts an instance can't validate.
  A future `v2` needs a federation migration story (currently single-workspace, co-versioned).
- The `org`/`team` blocks are forward-compat hooks for multi-tenant — the panel's tenancy model
  should build on them rather than inventing a parallel one.
