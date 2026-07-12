---
status: draft
---

# Executor interactive tier — §6.3 input-request escalation ("what do you want?")

The second half of the interactive tier (RFC `executor-abstraction.md` §6.3). Relay (§6.2, shipped)
handles **permission** questions ("may I?"). This handles **domain-judgment** questions ("which of
these three implementations?", "what should I name this key?") — routed as *work*, not policy:
answered by a capable **lead node** where the approved design implies the answer, by a **human** only
when it must be, and **memoized** so a re-run never asks twice.

## The reality check (state it up front)

Headless agentic harnesses are built to be autonomous — they usually **decide and proceed**, noting
assumptions, rather than stop to ask a domain question. So input requests are **rarer than
permissions**, and unlike permissions there is **no structured event** for them (Claude Code reports
`permission_denials` for §6.2, but "which approach?" is just prose in its final message). The value
is real but narrower: catch the case where a harness **punts a decision back** (ends with a question,
took no action) and answer it — usually via the lead node — so the run completes autonomously instead
of dead-ending on an unanswered question.

## Three parts

| Part | Mechanism | Where |
| --- | --- | --- |
| **Detect** the question + extract options | a shared core **classifier** (decision 7), not per-adapter regex; or a native vendor event via `event_map` | core (+ data opt-in) |
| **Answer** it | the **lead node** (parent agent's model) within a token budget; else a human | core |
| **Deliver** the answer + not ask twice | park-resume relaunch with the answer injected; **memoize** into the task spec | core + data |

## Detection — the classifier (RFC decision 7)

Cheap pre-filters gate a small structured-output LLM call — **language-agnostic by construction**
(harnesses answer in the task's language; punctuation heuristics fail outside English):

1. **Pre-filters (no LLM):** the run ended with **no action taken** (no artifact / empty diff) OR the
   declared `artifacts.profile` output is absent. If the harness *did* the work, it wasn't asking.
2. **Classifier:** one `ModelProvider.complete` with `response_format` (structured output) over the
   harness's final message: *"does this request input? extract the question + enumerated options +
   whether free text is acceptable."* Small, cheap model (tiered strategy). Returns
   `input_requested{question, options[], free_text_allowed, question_class}`.
3. **Native override:** an adapter MAY map a native vendor question-event in its `event_map` to
   `approval`… no — to `input_requested` (data); when present, the classifier is skipped. None of our
   harnesses have one today, so the classifier is the path.

Misfires resolve harmlessly (a no-op answer / operator dismiss).

## Escalation — the lead node, then a human

The archetype declares an `input_escalation` chain. Default first stop: the worker's **lead**
(its parent agent — `current_parent_agent()` — a capable model holding topology context + the
approved design + workspace conventions).

1. **Lead answers** within a small token budget when the approved design implies the answer
   (implementation-choice questions often qualify). Injected back; the run continues, no human.
2. Questions matching the archetype's **`human_required_patterns`** (e.g. naming external-facing
   things), or **declined by the lead**, land in the cockpit **inbox** as a human gate (reuse the
   relay `ReviewQueue` + bounded-wait → abort; never hangs).
3. **Lead answers are auditable** (decision 8): surfaced in run review — delegated judgment that
   can't be audited is not governed.

## Delivery + memoization

- **Deliver:** the answer is fed back the same way relay grants are — a **park-resume relaunch** with
  the answer injected. The adapter declares how (an `answer` template, alongside `grant`/`resume`);
  reuses the generic park-resume loop already shipped.
- **Memoize (the real fix):** every Q→A is recorded (`exec.input_response`) into the run record. On a
  re-run / retry the prior answers are **pre-injected into the `TaskSpec.pre_answered`** map (the P2
  field, already present) so the harness never asks twice. Recurring question classes are a signal of
  a missing workspace convention — mined later (spec-quality feedback), not built here.

## Grounding (what exists vs. new)

- `TaskSpec.pre_answered` — **exists** (P2), the memoization sink.
- `ExecInputRequested` / `ExecInputResponse` — **exist** (P2 vocabulary).
- Structured-output model call — **exists** (`CompletionRequest.response_format`).
- park-resume relaunch loop + `ReviewQueue` inbox + bounded-wait → abort — **exist** (relay).
- **New:** the classifier + pre-filters; threading a `ModelProvider` + the parent `ResolvedAgent` into
  the harness node (the lead's model); the `input_escalation` / `human_required_patterns` archetype
  fields; the `answer` adapter template; memoization wiring.

## PR slices (dependency-ordered)

1. **The classifier (core).** `_input_classifier.py`: pre-filters + one structured-output call →
   `input_requested{question, options, free_text_allowed}`. Pure; mock-model tested. No wiring.
2. **Input-request handling in the harness node.** After a run with a punt-back (or a native
   `input_requested` event): classify → route to the **human inbox** for now (bounded wait → abort) →
   park-resume relaunch with the answer injected (new `answer` adapter template). Reuses the relay
   loop. Memoize the Q→A into `pre_answered` for the session.
3. **Lead-node escalation.** Thread the parent `ResolvedAgent` + a `ModelProvider` into the harness
   node; the lead answers within a token budget; `human_required_patterns` + a decline bypass to the
   human inbox. Lead answers audited.
4. **Cross-run memoization + spec-quality signal.** Persist Q→A to the run record; pre-inject on
   re-run; count recurring question classes (surface only — the fix is a workspace convention).

## Never-hang

Identical to relay: a bounded wait for the answer (lead budget + human `max_approval_wait`), then
`abort`. No input request can hang a node.

## Acceptance (subset of RFC §11)

- An `exec.input_requested` with enumerated options is answered by the lead within its budget and
  injected back **without human involvement**; a `human_required_patterns` match bypasses the lead to
  the inbox. On re-run, the memoized answer is pre-injected and the question does not recur (RFC #11).
- Lead answers appear in run review (decision 8).
- No answer inside the wait ⇒ `abort`, never hangs.

## Deferred

The spec-quality mining/analytics; the classifier's model-tier config polish; a future native-event
adapter. This ships the punt-back-answered path with lead + human + memoization.
