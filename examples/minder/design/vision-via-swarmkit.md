# Vision via SwarmKit archetypes

**Status:** proposed
**Scope:** `examples/minder` (frigate MCP server + workspace archetypes)

## Problem

Minder's VLM calls — the escalate confirmation ("is there something dangerous?") and the post-alert
describe — are hand-rolled HTTP in the frigate MCP server (`_cloud_vlm`, `_local_vlm`,
`_vlm_confirm`, `_describe_snapshot`). That means:

- **Hardcoded** model, provider, endpoint, and prompt in Python — a rebuild to change any of them.
- **Inconsistent with the framework's own rule** (invariant 4: *all LLM calls go through
  `ModelProvider`; only `model_providers/` imports vendor HTTP clients*). The MCP server imports
  `urllib` and talks to OpenRouter/Ollama directly.
- **Invisible** — the request/response only reaches container stderr. It is not in the Minder UI and
  not in SwarmKit's audit/run-traces (because it is deterministic MCP code, not a topology run). So
  near-misses ("No, gardening tools" → dropped) are unobservable after the fact.

## Goal

Move the VLM *call* onto **SwarmKit vision archetypes**, so model/provider/prompt are configuration,
the call goes through `ModelProvider`, and every check lands in SwarmKit's audit + run-traces (hence
the UI). Minder's deterministic flow is unchanged.

## Non-goals

- **Not making the flow agent-orchestrated.** `detect → confirm → alert/drop`, cooldowns, tiers,
  thresholds stay plain Python in the MCP server. Only the model *call* moves. This keeps Minder's
  principle intact (*code decides the flow; the LLM only determines*) — a VLM yes/no is exactly the
  "determine" part.
- **Not a new provider.** Reuse the existing `ModelProvider` (Ollama + OpenRouter/OpenAI).
- **Not changing detection.** Frigate still owns detection; the hi-res snapshot grab stays.

## The two archetypes

Per the design discussion: **two vision archetypes**, one per tier, same shape, differing only in the
bound model/provider.

| Archetype | Model / provider | Selected when |
| --- | --- | --- |
| `vision-local` | Ollama `qwen2.5vl:3b` (local, zero-cloud) | rule tier `local` |
| `vision-cloud` | `google/gemini-2.5-flash` via OpenRouter | rule tier `cloud` |

Both share: input `{image, question}`, output `yes/no + short reason`, and the system prompt
("You are a home-security camera assistant. Answer 'yes' or 'no' as the FIRST word, then a short
reason…") — which becomes **archetype config**, tunable without a rebuild.

## Tier → archetype mapping (deterministic)

The escalate code already computes a tier (`_escalate_tier(rule)` → `local` | `cloud`). It maps 1:1
to an archetype: `vision-{tier}`. No parameterization — Minder just picks the archetype name. The
whole selection stays deterministic code.

## The `{image, question} → verdict` contract

- **Input:** the hi-res JPEG (base64) + the rule's question (e.g. "Is there something dangerous?").
- **Output:** the raw VLM text; Minder's existing `_is_yes` / `require` logic parses the verdict (no
  change to the decision code).
- The archetype's job is purely the VLM determination; parsing + firing remain in the MCP server.

## Enabling dependency — already present

A vision archetype needs `ModelProvider` to carry an image. **It already does:**
`model_providers/_types.py` `ContentBlock` has `type: "image"` with `image_data` (base64) +
`image_media_type`, plus an `image_block()` helper; `_anthropic.py` and `_ollama.py` wire it through.
So this is wiring, not new plumbing.

**One build-time check:** Minder's cloud is Gemini via **OpenRouter** (OpenAI-compatible), so confirm
`_openai.py` forwards image blocks the same way `_anthropic`/`_ollama` do. Anthropic + Ollama are
confirmed; OpenRouter is the one to verify (and add if missing — a small, generally-useful fix).

## Invocation: `serve /run` vs `ModelProvider`

- **`serve /run` (recommended).** The MCP server POSTs to the local serve API to run the
  `vision-{tier}` archetype with the image + question. Heavier (a bounded topology run per check) but
  it lands in audit + run-traces + the UI — which is the point.
- **`ModelProvider` directly.** Lighter, gets config + provider-swap, but no run-trace observability.

Recommendation: **`serve /run`**, because visibility is the driving goal. Measure the added latency
(below) to confirm it's acceptable.

## Fallback

Unchanged behavior, expressed as archetypes: if `vision-cloud` returns empty, Minder calls
`vision-local` (the current cloud→local fallback). Stays in deterministic code.

## Observability payoff

Every escalate check becomes a SwarmKit run: the question, the image, the model's answer, cost, and
timing are captured in the audit log + trace — browsable in the run view (and the complete-node view
from the SDLC design). This directly answers "where can I see what the VLM was asked and answered",
including the near-misses that are silently dropped today. It also lets the temporary stderr debug
logging retire.

## What it retires

`_cloud_vlm`, `_local_vlm`, `_vlm_confirm`, and the direct-HTTP half of `_describe_snapshot` collapse
into "call `vision-{tier}` via serve". The describe path reuses the same archetypes.

## Latency

The escalate already runs in a background thread and the VLM call (~seconds) dominates; a bounded
topology run adds startup overhead on top. Budget: the added overhead should stay small relative to
the VLM inference. Measure `serve /run` round-trip vs the current direct call before committing to it;
if the overhead is material for local, fall back to `ModelProvider` for `vision-local` only.

## Test plan

- **Archetype resolution (unit):** `tier → vision-{tier}`; a missing archetype errors clearly.
- **Contract (integration):** run `vision-cloud` / `vision-local` with a fixture image + question →
  a `yes/no + reason` string; `_is_yes` parses it. Mock the provider so it's offline-testable.
- **Image passthrough:** the base64 image reaches the provider (assert on the `ModelProvider` request
  — a `ContentBlock(type="image")`), for Ollama and OpenRouter.
- **Fallback:** empty `vision-cloud` → `vision-local` is invoked.
- **Observability:** a run is recorded (audit event + trace) for each check, near-misses included.
- **No-regression:** the existing escalate decision tests (`test_escalate.py`) still pass with the
  VLM call swapped for the archetype call.

## Demo plan

`just` / a script: trigger an escalate, then show the SwarmKit **run view** entry for the vision
archetype — the question, the snapshot, and Gemini's answer — for both a fired alert and a near-miss.
The "where can I see this" answer becomes a screenshot.

## Open questions

- **`serve /run` per detection vs `ModelProvider`** — resolve with the latency measurement.
- **OpenRouter image support in `_openai.py`** — confirm/patch during build.
- **Describe path** — fold into the same archetypes now, or in a follow-up (it's the same shape).
