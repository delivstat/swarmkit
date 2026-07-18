# Task surface + shared board (the human layer at scale)

Parent: `design/details/sdlc-pipeline-example.md` (capability 5 of 5). Humans are the scarce
resource in a multi-team pipeline, so their surface is a first-class capability, not an afterthought.

Two things, one substrate: **per-role task queues** (a person's pending gate tasks across *all*
requirements) and the **cross-requirement board** (every in-flight requirement and where it sits).
The key decision (from the parent review): both are views over **one shared, namespaced coordination
space**, so the org-wide picture and the individual's to-do list come from the same place.

## Goal

Give every human one honest place to act (their queue) and every operator one place to see (the
board), both derived from a shared namespaced space — with notification routing so tasks reach people
where they are, not only when they happen to open a dashboard.

## Non-goals

- **Not the approval semantics.** Who must approve and quorum are `multi-party-approval`; this note
  *routes and renders* the tasks that gate emits.
- **Not the saga state of record.** The controller (`pipeline-controller`) owns per-requirement
  truth; the board is a derived/annotating view, and the append-only audit remains authoritative.
- **Not a new notification stack.** Channel delivery reuses Minder's channel-adapter model
  (`project_minder_channels`), config-driven, no code per channel.

## Where it lives

The board is a **shared, namespaced coordination space** exposed over MCP — a GBrain-style knowledge
graph (`reference_gbrain`: git-backed, hybrid search, namespaced). It is a workspace MCP server, not
new bespoke infrastructure (a second reuse of an existing building block). The task-queue and
notification-routing pieces are thin services over it.

## API shape

### The shared namespaced space

Every team, requirement, and role writes under its **namespace**; it is one common space, so
separation and a single queryable view come from one substrate:

- `req/<id>` — a requirement's live status, current stage, held locks, open gates.
- `app:<oms|web|mobile>` — a team's writes.
- `role:<name>` — a role's pending tasks.

**Write-scoping = the same IAM discipline.** A team/agent writes only its namespace; the common
*read* space is the shared view. No new access model — it reuses the per-app scoping used everywhere
else in the pipeline.

### Per-role queue

A role's queue is a query on `role:<name>` — every pending gate task for that role across *all*
requirements, not a hunt through runs. Each task entry carries the **provenance bundle** the funnel
assembled (`gate-funnel`): the artifact, the judge score + critique, attached reviewer findings, the
retry count, and the diff-since-last-approval — so a human decides in one place with full context.
Resolving a task calls back to the gate (the approval is recorded by `multi-party-approval`; the queue
is just the surface).

### The cross-requirement board

A query over the space: every in-flight requirement and the gate/lock it sits at, throughput and
bottleneck per gate, and held integration-contract locks + their queues. This is the operator's
primary surface at scale — it makes the approver bottleneck and lock contention *visible* rather than
hidden. It reuses the existing runs/trace federation for per-run detail; the new surface is the
requirement-correlated roll-up.

### Notification routing

Gate tasks notify their role's members via **configurable channels** — reuse the Minder channel
adapters (Slack / Telegram / email), config-driven per role, no code per channel. Routing is data:
a role maps to one or more channels; SLA breaches (from the gate's SLA, `pipeline-controller`)
escalate the notification to the next role up. A task in a queue and a nudge in a channel are the same
task, two surfaces.

### Three roles, kept distinct

| Concern | Owner | Nature |
| --- | --- | --- |
| Approval record | `multi-party-approval` audit | immutable source of record |
| Requirement/saga truth | `pipeline-controller` state | authoritative live state |
| Artifacts | Curator KBs | versioned artifact store |
| Human surface | **this note** (the board) | derived coordination/status view |

The board never becomes the source of record; it derives from and annotates the other three.

## Eject

The board/queues are services over an MCP space, not part of a topology run, so there is no LangGraph
eject. The MCP space is portable (git-backed); a real deployment can point the same queue/board
services at its own space. Like the controller, this is deliberately not a runtime feature.

## Test plan

- **Namespace write-scoping:** a writer in `app:oms` cannot write `app:web` or `role:qa-lead`; the
  common read view returns all namespaces.
- **Per-role queue:** a role's query returns its pending tasks across multiple requirements; a
  resolved task leaves the queue; each entry carries the provenance bundle.
- **Board roll-up:** in-flight requirements map to their current gate/lock; held locks + queues show;
  per-gate throughput is computed across requirements.
- **Notification routing:** a gate task notifies the configured channel(s) for its role; an SLA breach
  escalates to the next role; adding a channel is config only (no code).
- **Derivation, not record:** the board reflects controller/audit state and never diverges as the
  source of truth (a reconcile against controller state is idempotent).

## Demo plan

`just demo-task-surface`: with two concurrent requirements in flight, show (a) `qa-lead`'s single
queue spanning both, (b) the board with one requirement parked on the design gate and the other queued
on a contract lock, (c) a task notification delivered to a configured channel, and (d) an SLA breach
escalating the nudge. Terminal transcript / screenshots in the PR body.

## Schema-change checklist

Adds the board/task record shapes (namespaced entries) — follow
`docs/notes/schema-change-discipline.md` where they cross the canonical schema. The MCP board server
and notification routing are reference services (reusing the Minder channel adapters), not runtime
features.
