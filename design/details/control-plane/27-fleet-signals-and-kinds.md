# 27 — Fleet signals and kinds: what the panel never received

**Status:** design · **Scope:** runtime + control-plane + fleet UI · **Follows:** [14](14-aggregation.md), [17](17-growth-loop.md), [19](19-fleet-enrollment-protocol.md), [20](20-manage-and-adopt.md), [23](23-usage-pull-on-sync.md)

## Why now — the September 2026 audit

The control plane last changed on 2026-08-04 (fleet UI: 2026-07-16). Between then and runtime
1.227.0 the runtime took 109 commits and 83 releases, including the removal of the bundled
pipeline layer. An audit ran runtime 1.227.0 enrolled into control-plane 0.45.0 in both modes
and walked every page and verb. Every path the panel calls still exists; the drift was semantic.
Two fixes shipped first because they were correctness bugs:

- **#897** (runtime 1.228.0, panel 0.46.0, UI 0.11.0) — a funnel's multi-party role-task was
  rendered as an input request and, when approved, marked the queue row without casting a
  resolution: the row left the pending list, the gate stayed open, the run never resumed. The
  runtime now refuses the generic verbs on a role-task; the panel learned `resolve`; the UI shows
  the role, scope, run and the instance's refusal. Canary aliases no longer inflate
  `/capabilities`; the run drivers understand `deferred`/`stopped`/`interrupted`.
- **#898** (runtime 1.229.0, panel 0.47.0) — drift read `missing` for every real deployment
  because nothing ever reported an instance's actual artifacts; a sync now reports them and drift
  compares content hashes. A deploy rewrote the target file from a dict; it now writes the adopted
  text verbatim once it parses to the signed content.

What remains is not drift but **feeds that were designed and never wired**, and **kinds the fleet
does not know**. That is this note.

## Goal

1. The panel's **Audit**, **Gaps** and (where the runtime records them) **Evals** views show real
   fleet data without any push code in the runtime — the same pull-on-sync pattern doc 23 chose
   for usage.
2. The fleet inventory, adopt and deploy cover **funnels and contracts**, and the inventory
   shows **role registries**, so a topology whose funnel is not on the target instance is a visible
   condition rather than a resolver error after the deploy.

## Non-goals

- A push channel from instances to the panel. Doc 14 left push-vs-pull open; doc 23 answered it
  for usage and the answer holds: the panel already polls, the connector already exists for
  Mode B, and pull needs no new credential on the instance.
- Deploying role registries. `iam:modify` is a human-reserved scope (design §8.7); a registry
  change from a fleet panel needs its own approval story. Roles are *observed* (state, inventory)
  and *adoptable* (so a registry can be versioned), not deployable.
- Eval results beyond what the runtime already stores. If a workspace has no eval store, the Evals
  page stays empty and says so.
- Forwarding the operator's identity to an instance for gate resolution (the panel resolves as its
  enrolled key; #897 shows the instance's refusal when that key is not a role member). A per-
  operator identity is an OIDC → role-member mapping and belongs with doc 12's next slice.

## API shape

### Runtime

| Route | Change |
| --- | --- |
| `GET /gaps` (new, `serve:read`) | `[{skill_id, topology_id, pattern, suggested_action, first_seen, occurrences}]` — the `skill_gaps` table, what `swarmkit gaps` prints. |
| `GET /audit` | gains `since: datetime` (ISO 8601) so a sync fetches only what it has not seen; the provider's `query(since=)` already exists. |
| `GET /fleet/state` (+ manifest, + `/fleet/state/artifacts`) | `artifacts` gains `funnels`, `contracts`, `roles`, each `[{id, version, content_hash, content, yaml}]`. |
| `/capabilities` | unchanged. |
| `connect.DEPLOY_PLURAL` | adds `funnel → funnels`, `contract → contracts` (PUT routes exist). |

### Control plane

| Route | Change |
| --- | --- |
| `POST /instances/{id}/sync` | after state: pull `GET /gaps` → aggregation kind `gap` (record id `f"{skill_id}@{topology_id}#{occurrences}"`, so a recurring gap adds a row per occurrence and the rollup's count is its occurrence count); pull `GET /audit?since=<cursor>&limit=500` → kind `audit` (record id = the event's `event_id`), cursor stored per instance; best-effort like usage — a failure logs and reports `pulled_gaps: 0`. Response gains `pulled_gaps`, `pulled_audit`. |
| `POST /instances/{id}/adopt` | `_ADOPT_COLLECTIONS` gains `funnel`, `contract`, `role`. |
| `POST /instances/{id}/deploy` | `DEPLOYABLE` gains `funnel`, `contract` (contract test keeps it equal to the runtime). |
| `GET /instances/{id}/state` | unchanged shape; three more collections. |
| `GET /gaps`, `GET /audit` | unchanged — they finally have rows. |

A **sync cursor** table (`sync_cursors(instance_id, kind, cursor)`) rather than a column, for the
same no-migration reason as `artifact_sources`.

### Fleet UI

- Inventory card: three more columns (funnels, contracts, roles).
- Deployments card: `funnel` and `contract` in the kind selector.
- Runs page "Recent activity" and the Gaps view need no change — they render the rollups.
- Sync result toast shows the pulled counts.

## Test plan

- runtime: `GET /gaps` lists a recorded gap (via `SkillGapLog.record`); `GET /audit?since=` excludes
  older events; fleet state carries the three kinds with content and text, the manifest strips
  both; `DEPLOY_PLURAL` round-trips through `execute_command("deploy", kind="funnel")`.
- control-plane: sync ingests gaps and audit from stubbed fetchers, a second sync deduplicates
  audit by `event_id` and advances the cursor, a failing fetcher yields `pulled_gaps: 0` and does
  not fail the sync; `GET /gaps` ranks the pulled gap; adopt of a funnel and a contract; deploy of
  a funnel PUTs to `/api/funnels/{id}`; the verb contract test still passes.
- fleet UI: inventory renders the new collections; the kind selector offers funnel/contract.

## Demo plan

Level 22 is rewritten from a real run, the way Levels 1–16 were: runtime + panel + fleet UI
started on the Level 16 workspace, enrolled in Mode A and Mode B, a doc-review run recorded as a
`skill.gap` after calling a tool it does not hold, a sync, then the fleet UI's Gaps and Runs pages
and the inventory with funnels/contracts/roles — screenshots and a short screen recording embedded
in the tutorial, the transcript in the PR body.
