---
title: Human decisions carry comments, and agents read them
description: Approve and reject are binary today, so the reasoning a human supplies is discarded — there is no field to put it in. This adds a comment to every human decision, a third outcome (changes-requested) that routes an artifact back with that comment, and a structured, attributed envelope so the agent that asked reads the decision in context rather than receiving a bare boolean.
tags: [runtime, governance, approval, executors, ui, cli]
status: draft
---

# Human decisions carry comments, and agents read them

**Scope:** `runtime` (`review/`, `governance/_approval.py`, `executors/`, `langgraph_compiler/`,
serve routes), `cli` (`swarmkit review`), `ui` (`/gates`, `/runs`)
**Design reference:** §8.5 (GovernanceProvider), §8.7 (reserved-for-human scopes), §6.2/§6.3
(harness permission + input gates), §14. Builds on `multi-party-approval.md`, `gate-funnel.md`,
`pipeline-gate-approval-ui.md`, `pipeline-gate-convergence.md`.
**Status:** draft

## Goal

Let a human attach a comment to any approve/reject decision, and deliver that comment — attributed,
typed, and distinguishable from the artifact — to the agent whose work was gated, so it can act on
the reasoning rather than on a bare boolean.

## Non-goals

- **Not a discussion thread.** One comment per decision, not a conversation. Multi-turn negotiation
  with an agent is `swarmkit chat`, and mixing the two would make a gate unbounded in time.
- **Not comments on arbitrary objects.** Only on a human decision at a gate. Annotating a run, a
  topology or an artifact is a different feature with a different lifetime.
- **Not changing quorum.** A comment never affects whether a rule is satisfied. `changes-requested`
  does, but through the engine's existing evaluation, unchanged.
- **Not free-text steering of `swarmkit run`.** The §6.3 input answer already does that; this note
  does not widen it.
- **Not a UI for editing an artifact by hand.** A reviewer comments; the agent revises.

## The problem: there is nowhere to put the reasoning

Three gate kinds, and only one carries text at all.

| gate | surface | what a human can say |
| --- | --- | --- |
| §6.2 permission | `review approve\|reject` | nothing — binary |
| §6.3 input request | `review answer <id> "…"` | free text, which becomes the agent's next prompt |
| multi-party role-task | `review resolve --approve\|--reject` | nothing — binary |

For the two binary kinds there is no field. `ReviewItem` has `answer: str`, and multi-party
resolution has already **repurposed it to hold the resolver's identity** (`record_resolution(item_id,
status, answer=identity)`) because the engine needs identity for quorum. So the one text field is
taken, and its meaning depends on the item kind — which is itself a defect worth fixing while we are
here.

Three consequences, in increasing order of cost:

1. **A rejection's reason is lost.** The controller records `saga.add("rejected", detail=...)`, but
   the gate event carries only `{kind, approved, stage}`, so `detail` is always empty. A run reads
   as rejected, never as *why*.
2. **An approval's conditions are lost.** "Approved, but only deploy to staging first" is the normal
   shape of a real sign-off. Today it is either not said or said in Slack, where the audit cannot
   see it and the agent certainly cannot.
3. **Send-it-back does not exist as a surface.** `governance/_approval.py` already defines
   `Outcome = Literal["approve", "changes-requested", "reject"]` and `evaluate()` already routes
   `changes-requested` to rework. No client can emit it: HTTP `resolve` takes
   `Literal["approve", "reject"]`, and the CLI has `--approve/--reject`. The capability the
   reasoning is *for* is dead code.

## The model: a decision is a record, not a boolean

```python
@dataclass(frozen=True)
class HumanDecision:
    outcome: Literal["approve", "changes-requested", "reject"]
    identity: str            # the authenticated resolver (never a body field — §8.7)
    comment: str = ""        # what they said; may be empty
    role: str = ""           # the capacity acted in, for a multi-party role-task
    scope: str = ""
    at: datetime = ...
```

`ReviewItem` gains two fields and stops overloading one:

```python
resolved_by: str = ""   # the resolver identity — was crammed into `answer`
comment: str = ""       # NEW: the human's reasoning
# `answer` reverts to its documented meaning: the §6.3 input response, and nothing else.
```

The file-backed queue reads old items with `resolved_by` absent by falling back to `answer`, so
in-flight gates survive the upgrade. `collect_resolutions` reads `resolved_by` with the same
fallback.

## Delivering it to the agent

This is the part that matters, and it differs by gate because the agent is in a different state.

### A. Harness parked mid-run (§6.2 permission, §6.3 input)

The executor already relaunches with a substituted statement:

```python
if resuming and answer:
    run_task = replace(task, statement=answer)
elif resuming and self._spec.resume_prompt:
    run_task = replace(task, statement=self._spec.resume_prompt)
```

So the delivery path exists; it just carries nothing for a permission gate. The resume statement
becomes the **rendered decision** (below) instead of the bare `resume_prompt`, and a §6.3 answer
keeps its current behaviour with the comment appended when one is given.

### B. Pipeline stage gate (approve → the run continues)

The stage already produced its artifact; the decision affects what happens *next*. So the decision
joins the **next** stage's input, which `_stage_input` assembles. Concretely, `_prior_input` gains a
decisions section, and the next stage sees the artifact plus what the humans said about it.

### C. Pipeline stage gate (changes-requested → the same stage re-runs)

The gate resolves to rework rather than terminal. The controller re-drives the **same** stage, and
`_stage_input` prepends the decisions so the agent's input is "your previous artifact, plus what to
change". This is the case that makes comments *actionable* rather than archival, and it reuses the
funnel's existing revise loop rather than inventing a second one.

`reject` stays terminal. The distinction is deliberate and worth stating in the UI copy: **reject
ends the run, changes-requested asks for another attempt.**

### The rendered envelope

An agent must be able to tell an approval condition from a rejection reason, and human text from
the artifact. So the decision is rendered in a fixed, labelled block — never concatenated raw:

```
<human-decisions gate="run-42:design">
  [changes-requested] security-reviewer (alice), scope=security:approve
    The retry loop has no backoff. Add exponential backoff before this ships.
  [approve] release-manager (bob), scope=security:approve
    Fine by me once alice's point is addressed.
</human-decisions>
```

Three properties, each load-bearing:

- **Attributed.** Who said it and in what capacity. A note from the security reviewer is not
  interchangeable with one from the release manager.
- **Typed.** The outcome is on every line, so "fine by me once…" cannot be misread as unconditional.
- **Delimited.** Human text is untrusted input to a model. It is fenced in a named block and
  described in the surrounding prompt as *a human's decision about your work*, not as instructions
  from the operator. A reviewer who writes "ignore your previous instructions" gets a comment
  faithfully relayed as a comment, not a privilege escalation.

Multi-party gates render **every** role-task's decision, in resolution order, because the aggregate
is the decision.

## API shape

```python
# HTTP — comment is optional everywhere, so an existing client keeps working.
POST /review/{id}/resolve   {"outcome": "approve" | "changes-requested" | "reject",
                             "comment": "..."}          # identity still from the session
POST /review/{id}/approve   {"comment": "..."}
POST /review/{id}/reject    {"comment": "..."}
POST /review/{id}/answer    {"answer": "...", "comment": "..."}

GET  /review?...    # items gain `comment` and `resolved_by`
GET  /pipelines/gate-status/{cid}/{gate}    # items gain `comment`; a `decisions` block is added
```

```
swarmkit review approve <id> -m "only staging for now"
swarmkit review reject  <id> -m "credentials in the diff"
swarmkit review resolve <id> --as alice --changes-requested -m "add backoff to the retry loop"
swarmkit review show    <id>          # renders the decisions block verbatim
```

UI: a comment textarea on the permission card and each role-task row, plus a third button —
**Request changes** — visually distinct from Reject, with copy that says which one ends the run.

Audit: `approval.role_task_resolved` and the harness-gate events gain `comment` and `outcome`.
Comments flow through the existing `audit/_redact.py` policy, so a workspace can redact them like
any other payload — a reviewer may paste a credential into a comment box.

## Test plan

- **Unit.** `ReviewItem` round-trips `comment`/`resolved_by`; an item written by the *old* shape
  (identity in `answer`, no `resolved_by`) still resolves quorum. `changes-requested` reaches
  `evaluate()` and yields rework. The renderer: attribution, outcome labels, multi-party ordering,
  and a comment containing the delimiter is escaped rather than closing the block.
- **Delivery.** §6.2 resume statement contains the rendered decision; §6.3 answer keeps its
  behaviour with the comment appended; the next pipeline stage's input contains the decisions block;
  a `changes-requested` gate re-drives the **same** stage with the comment present.
- **Governance.** Comment appears on the audit event; redaction policy applies; a comment never
  changes quorum (same approvals, with and without comments, evaluate identically).
- **Prompt-injection shape.** A comment containing `</human-decisions>` or "ignore previous
  instructions" is relayed inside the block, and the surrounding framing is asserted present.
- **Full pipeline.** Live `swarmkit serve` + `swarmkit orchestrator`: request changes with a
  comment, assert the stage re-runs and the agent's input carries the note; then approve and assert
  the run advances.

## Demo plan

`just demo-decision-comments` — a two-role gate on a real parked run:

1. `swarmkit review resolve … --as alice --changes-requested -m "add backoff to the retry loop"`
2. the stage re-runs; print the input it received, showing the decisions block
3. the revised artifact appears; both roles approve, one with `-m "fine by me"`
4. `swarmkit pipeline status` shows the run advanced, and the audit shows both comments

Plus a screenshot of the `/runs` panel with the comment box and the three buttons.

## Open questions

- **Who may see a comment?** Today the review queue is world-readable to anyone with `serve:read`.
  A rejection comment may name a person or a security finding. Does a comment inherit the run's
  visibility, or does it need its own scope? Leaving it readable is the current posture by default,
  which is an argument for deciding deliberately rather than inheriting.
- **Does `changes-requested` consume a retry budget?** The funnel has `max_retries` for its advisory
  layers. A human asking for changes is not the same as a judge failing, and counting them together
  would let a reviewer exhaust the budget. Probably a separate counter, but it needs a decision
  before implementation.
- **Stale comments on a retry.** `pipeline-gate-convergence.md` already flags that approvals cast
  against a previous artifact carry forward. Comments make this sharper: a note about v1 of an
  artifact is actively misleading when attached to v3. Options are to clear comments on re-open, or
  to stamp each with the artifact ref it was written against and render that. The second is more
  honest and more work.
- **Rendering budget.** A long comment on a many-role gate could crowd the agent's context. Bound
  it (per-comment and per-block) — the same posture as the harness stderr tail.
