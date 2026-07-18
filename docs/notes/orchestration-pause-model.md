# Orchestration pauses — decision shortcut

**Recurring question:** if SwarmKit gates are checkpoints that pause for weeks, why is there an
external controller, and does it drive the approval pauses?

**Already decided — two kinds of pause, two layers; the controller does *not* drive approvals:**

- **Human approval *within* a stage** → a **SwarmKit checkpoint** (`interrupt()` + checkpointer),
  resumed by a **human**. The controller is idle during it; it is only *notified* the gate resolved.
- **Transition *between* stages** → the **external controller**, triggered by an **external system
  event** (CI / Jira / Git), because SwarmKit's `interrupt/resume` doesn't subscribe to webhooks.

One-line model: *determination + governance + the approval pause are inside SwarmKit (resumed by
humans); cross-stage sequencing on external events is the controller. The controller learns that a
gate resolved; it never is the gate.*

Authoritative detail (timeline, why-not-one-big-run): `design/details/orchestration-pause-model.md`.
