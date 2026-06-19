# Scenario Studio Phase 2 — Quick Mode (open-vocab "detect anything") — Design Note

**Scope:** `examples/minder` — detect **any object described in words** ("cardboard
box", "hard hat", "puddle") with **zero training**, so a scenario can watch for
site-specific things the stock Frigate model (person/car/dog/cat) doesn't know.
Builds on the deterministic condition grammar (count/zones/cross) — quick mode is a
new *detection source*; the rules + matchers are unchanged.
**Design references:** parent [[scenario-studio]] (§"Phased plan" item 2, §"Quick
mode", §"Resource reality", Open Question 2); the count/zone/cross matchers; the VLM
runtime decision ([[project_minder_vision_runtime]]).
**Status:** proposal. **Runtime decided: cloud VLM via OpenRouter (opt-in, on-demand)**
— on-box open-vocab is too heavy even for the prod appliance, so the hard perception is
offloaded to a cloud VLM. Needs an OpenRouter API key + model choice to implement +
validate.

## Goal

"Quick mode" from the parent design: an **open-vocabulary detector** (YOLO-World /
Grounding-DINO / YOLOE) run from a text prompt, no capture/label/train. The user
types what to watch for ("cardboard box on the belt") and Minder detects + counts it
immediately — lower accuracy than a trained model, but instant and on-box. The
trained-model pipeline (capture → label → train) is Phases 3–4; quick mode is the
prototype tier that the design says to "lead with."

## The hard reality (why this is the careful phase)

The reference appliance is a **4 GB GTX 1050 Ti** that is **already at capacity**, and
the project has already learned the GPU thrashes:
- **VRAM holds only the qwen2.5:3b router.** Everything vision is deliberately on CPU:
  Frigate's detector (`detectors: {cpu1: {type: cpu}}`), the VLM (`num_gpu: 0`), the
  describe path (`num_gpu: 0`). This was a hard-won decision (GPU thrash → moved off).
- An open-vocab detector (YOLO-World-s ≈ a YOLOv8 backbone **+ a CLIP text encoder**)
  is bigger than the stock YOLO and, on GPU, would contend with / OOM against the 3B.

So "GPU frontier" is a slight misnomer for *this* box: **the only safe on-box place
for open-vocab is the CPU, on-demand** — exactly the pattern the VLM already uses.
Putting it on the GPU is the contention risk the parent design flags as Open Question 2.

## Decision (confirmed): cloud VLM via OpenRouter, on-demand, opt-in

On-box open-vocab is too heavy even for the prod appliance, so quick mode offloads the
hard perception to a **cloud VLM via OpenRouter** instead of running a detector locally.
Important reframing this implies:

- **OpenRouter serves VLMs, not object detectors.** So quick mode is **"send a snapshot
  + a question to a cloud VLM"** ("is there a cardboard box?", "is the gate open?",
  "anyone without a hard hat?"), not open-vocab box detection. This is *better* for
  nuanced / state questions, and reuses Minder's existing `_vlm_answer` pattern almost
  verbatim — just pointed at OpenRouter (an OpenAI-compatible endpoint) instead of local
  Ollama. Fits SwarmKit's `ModelProvider` abstraction (OpenRouter = another provider).
- **Model:** a cheap vision model on OpenRouter (e.g. `google/gemini-2.0-flash`,
  `openai/gpt-4o-mini`, a Qwen-VL) — configurable (`MINDER_CLOUD_VISION_MODEL`).
- **On-demand only:** evaluated on a cadence / Frigate-motion trigger (default ~60 s,
  motion-triggered preferred), **never 24/7**.
- **Opt-in + key-gated:** off unless an OpenRouter API key is configured
  (`MINDER_OPENROUTER_KEY`, read from env/config — **never committed**); graceful no-op
  without a key.

### Guardrails (non-negotiable)

- **Presence / state, not precise counts.** The design's core warning stands — VLMs
  miscount. Quick mode answers *presence / state / scene-judgment*; any count it returns
  is **approximate** (fine for "roughly more than N", never an exact tally). Precise
  counting stays on the local deterministic detector (stock or, later, trained).
- **Privacy — footage leaves the box.** This is the one real tension with Minder's
  "fully local, no cloud in the loop" promise: an armed cloud scenario sends camera
  snapshots to a third-party API. Defensible because it's **opt-in, on-demand, and
  per-scenario** (the 24/7 monitoring loop stays fully local; only the specific armed
  cloud-scenario transmits frames) — but the UI **must say so plainly** at arm time.

## Architecture

```
cloud quick-mode scenario (prompt question + presence/state rule)
        │  on a cadence / on Frigate motion   (opt-in, key-gated)
        ▼
  _grab_frame(camera)  ──►  OpenRouter VLM (snapshot + prompt → structured {present, note, count?})
        │                         │
        ▼                         ▼
  presence/state verdict ──► existing alert path (write_alert + actions)
```

- A cloud-VLM provider call (reuse the `_vlm_answer` shape; OpenRouter base URL +
  `Authorization: Bearer <key>`; OpenAI-compatible chat/vision payload). Structured
  output: `{present: bool, note: str, count?: int}`.
- A quick-mode evaluator (like `frigate_poller`/`mqtt_listener`): for each armed cloud
  scenario, grab a frame on its cadence, ask the VLM the prompt, fire on the verdict
  through the existing alert path.
- The rule carries `detector: "cloud"` + `prompt: "..."`; presence/state verdict feeds
  the existing alert path (counts, if requested, flagged approximate).

## Studio UI (minimal — define → prompt → rule → arm)

The rule builder gains a cloud quick-mode toggle: type a **question/prompt** ("is the
gate open?"), pick camera + cadence + action; a **clear "sends snapshots to OpenRouter"
notice** + a **Test** button (one live call showing the verdict) before arming.

## Cost + accuracy + honesty

- **Cost:** per-call cloud billing — bounded by on-demand cadence + opt-in; surfaced
  (model + rough per-call cost) in the UI.
- **Accuracy:** stronger than on-box open-vocab for nuanced/state questions; **counts
  approximate**; prompt-sensitive — the Test button lets the user check before arming.

## Phases 3–4 (after quick mode proves out) — the trainer

Capture frames → auto-label (a foundation model, transient) → review canvas → train
YOLOv8-n → register + deploy. **Training is the genuinely heavy GPU job** the parent
design flags: on a 4 GB card it's slow and contends hard with the 3B. The design's
recommendation stands — **lead with quick mode + import-a-model**, and treat on-box
training as an opt-in "appliance with a real GPU" tier, not the default. (Detailed in
the parent note; its own design note when we get there.)

## Non-goals

- Open-vocab as the 24/7 Frigate detector (it's on-demand only).
- On-box training (Phases 3–4).
- Replacing the stock Frigate detection (this adds a parallel prompt-driven source).

## Test / demo plan

- **Unit:** `detect_prompt` returns boxes for a known prompt on a fixed test image;
  the quick-mode evaluator feeds the count/zone matchers (reuse the count/zone tests
  with a synthetic detector). Graceful no-op when deps/model absent.
- **Live (opt-in, off the critical path):** enable `MINDER_QUICKMODE`, arm a "cardboard
  box on the belt, count > 0" quick-mode scenario, place a box in view → alert; measure
  CPU inference time + confirm the 3B/Frigate stay responsive. Run this **deliberately,
  with the user**, given it loads a new model on the live box.

## What's needed to implement + validate

1. **An OpenRouter API key** (added to the box's `.env` as `MINDER_OPENROUTER_KEY` —
   **never committed**); the feature is a no-op without it.
2. **A model choice** — recommend a cheap vision model (`google/gemini-2.0-flash` or
   `openai/gpt-4o-mini`); configurable.
3. **Cadence** — fixed interval vs Frigate-motion-triggered (motion preferred, cheaper).

The implementation can be built + unit-tested with a **mocked OpenRouter response**;
live validation needs the key (done deliberately with the user, off the critical path).

## Alternative (noted, not chosen): local YOLO-World on CPU

If staying cloud-free ever matters more than capability, the on-box option is
ultralytics **YOLO-World-s** on **CPU**, on-demand (mirroring the VLM) — true open-vocab
*detection* (so usable for counts), but ~1–5 s/inference and prototype accuracy, and it
adds the ultralytics-world + CLIP dependency. Kept as a fallback tier for an air-gapped
or privacy-strict deployment.
