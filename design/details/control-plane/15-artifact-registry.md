# 15 — Phase 5: Artifact registry + versioning

Builds on [07](07-schema.md) (artifacts + the canary versioning that exists) and
[11](11-architecture.md) §5. Designs the central versioned registry and the **governed** sync to
instances.

## Goal

One place to version, store, diff, and deploy SwarmKit artifacts across the fleet — with
reproducibility (replay a run against a pinned version) and drift detection.

## Versioning (extend beyond topologies)

Today only **topologies** version, via canary `(name, version)` ([02](02-serve-api.md)). The
registry extends versioning + provenance to **skills, archetypes, workspace, triggers**:

- `ArtifactVersion` = `(kind, id, version, content_hash)` + provenance (author, authored_by,
  created_at) + the `swarmkit-schema` version it validated against ([11](11-architecture.md) §5).
- `content_hash` enables **drift detection** (registry's expected vs an instance's actual) and
  **replay-with-version-lock** (a `Run` records the active version per artifact).
- Skills/archetypes are id-immutable today; the registry adds explicit versions so an update is a
  new version, not a silent overwrite.

## Sync model (push + pull, governed)

- **Push (panel → instance):** deploy a version via the existing `/api/*` CRUD (validate → write →
  re-resolve, [02](02-serve-api.md)). **Artifact push is a legislative change** — `topologies:modify`
  is reserved-for-human ([05](05-identity-governance-iam.md)) — so push is **governed, audited, and
  human-gated**, not an ambient panel power. Requires `serve:admin` ([12](12-auth.md)) **and** a
  human approval ([17](17-growth-loop.md) gate).
- **Pull (instance → registry):** instances report active versions (via `/capabilities` +
  `content_hash`); the panel computes **drift** (deployed ≠ registry-intended) and surfaces it.
- **Deployment** record = `(instance_id, kind, id) → version`; rollout uses canary weights for
  topologies, direct deploy for others.

## Schema-version compatibility

The registry tracks each instance's `swarmkit-schema` version ([07](07-schema.md), [13](13-connector-registry.md))
and **refuses to push** an artifact an instance can't validate. A compatibility matrix (artifact
`apiVersion` × instance schema version) gates deploys; a future `v2` needs a migration story.

## Storage

- **Recommend:** a **git-backed content store** (artifact YAML + provenance + diff/history for free,
  signed commits possible) + **Postgres metadata** (registry index, deployments, drift). Git gives
  the versioning/diff/provenance primitives without reinventing them; Postgres gives queryable
  fleet state.
- Ties to `eject` (M9 stub) later: ejected code could be a registry artifact kind.

## Relation to canary + rollout

Canary stays the per-topology weighted rollout mechanism on the instance; the registry is the
*source of intended versions* and the fleet-wide promote/rollback control. Promotion criteria
([02](02-serve-api.md)) can be aggregated across instances (needs persisted canary metrics, [04](04-aggregation.md)).

## What Phase 5 builds

The registry store (git + Postgres) + version/provenance model; content-hash drift detection;
the governed/audited/human-gated push + the schema-compatibility gate; deployment tracking; the
registry surface in the UI ([16](16-fleet-ui.md)).

## Open questions

- Git-backed vs pure-DB store (recommend git-backed; confirm).
- Signing artifacts (provenance integrity) — likely yes, later.
- How the existing `swarmkit publish`/`install` expertise packages ([08](08-cli.md)) relate to the
  registry (the registry may subsume or complement them).
