# Two-layer vision/reasoning models (design note)

**Status:** implemented (phase 1 — routing + tiers; semantic rules deferred).
**Scope:** `examples/minder`. Builds on [[vision-architecture]] and the cloud-describe
opt-in (`project_minder_cloud_describe`).

## Problem

Minder used one model tier for every "what it sees" call, and the query path
conflated two different questions:

- *"is someone at the office"* — **presence**. YOLO answers this fast and cheap.
- *"what is the person doing at the office"* — **description / judgment**. Needs a
  vision-language model to read the scene.

The dispatcher set `open_scene` (the flag that sends the frame to a VLM) to `True`
only when the subject was NOT `person`/`vehicle`/`animal`. So an activity question
that named a person fell through to YOLO and answered "🔴 Yes — person at Office" —
the image was captured but never described. The user expected "a person is waving."

## Goal

Two explicit model layers, chosen by cost/frequency:

| | **Layer 1 — rule alerts** | **Layer 2 — queries + complex** |
|---|---|---|
| Trigger | Frigate event → deterministic rule | "what is X doing?", "is there danger?" |
| Frequency | high (per event) | low (on demand) |
| Job | detect + brief enrichment | understand + judge the scene |
| Model | **local qwen2.5vl:3b** (zero-cloud, additive) | **Kimi K2.6** (multimodal: reasons + sees in one call) |

Non-goals (this phase): semantic monitoring rules ("arm: alert if there's danger").
The poller stays object/zone/count-based; only interactive queries reach Layer 2.

## Design

1. **Routing.** A deterministic describe-intent check (`_wants_description`) forces
   `open_scene=True` for activity/description/judgment phrasings ("doing",
   "happening", "going on", "describe", "wearing", "carrying", "danger",
   "suspicious", "what/who is …") even when the subject is a tracked object. Pure
   presence questions ("is anyone there", "is there a car") still take the fast YOLO
   path. Router stays the parser; this guard is deterministic code (the runtime
   principle: LLM for language, code for doing).

2. **Layer split.** Two independent provider/model knobs:
   - Layer 1 (alert enrichment, `frigate/server.py:_describe_snapshot`):
     `MINDER_DESCRIBE_PROVIDER` (default `ollama`) + `MINDER_VISION_MODEL`.
   - Layer 2 (interactive query, `webapp/minder_ops.py:_vlm_answer`):
     `MINDER_QUERY_PROVIDER` (default `ollama`) + `MINDER_QUERY_MODEL`. On the test
     box: `openrouter` + `moonshotai/kimi-k2.6`. Cloud failure falls back to the
     local VLM, so a query always gets an answer.

3. **Kimi K2.6 is multimodal** (K2.5 added vision, K2.6 adds video) — one call both
   reasons about the request and reads the frame, so Layer 2 is a single model.

## Test plan

- Unit: `_wants_description` classifies activity/danger vs presence; `_vlm_answer`
  cloud branch parses an OpenAI-compatible reply and falls back to local on error.
- Live: "is someone at the office" → fast YOLO presence; "what is the person doing
  at the office" → Kimi K2.6 scene description; cloud-down → local VLM answer.

## Follow-on

Semantic monitoring rules — the poller calls Layer 2 on a candidate event to judge
"is there danger?" and arm it as a rule. Separate note when built.
