# Scenario Studio Phase 2/3 — Custom Local Detectors (cloud-assisted authoring) — Design Note

**Scope:** `examples/minder` — let a user teach Minder to detect a **site-specific
object** the stock Frigate model doesn't know ("cardboard box", "hard hat", "puddle"),
producing a **tiny detector that runs 24/7 fully on-device**. Cloud touches only the
**one-time setup** (labeling), and **training happens off-box** — so the 24/7
monitoring loop stays 100% local. Builds on the deterministic grammar (count/zones/
cross) — the trained detector is a new *detection source*; the matchers are unchanged.
**Design references:** parent [[scenario-studio]] (§"On-site workflow", §"Resource
reality", Open Questions 1–2); the VLM runtime decision ([[project_minder_vision_runtime]]).
**Status:** proposal. **Confirmed direction: cloud-assisted labeling + off-box training
+ local inference.**

## The key decision (confirmed)

On-box open-vocab inference (YOLO-World/Grounding-DINO) is too heavy even for the prod
appliance, and a cloud VLM *in the monitoring loop* breaks "no cloud in the loop". So
instead: **use the cloud once, at setup, to help build a local model**, then run that
model locally forever. The expensive understanding happens once; the 24/7 loop is a
tiny deterministic detector. This is exactly the design's tiered philosophy.

Two distinct cloud/compute jobs (an important correction — OpenRouter is inference-only):
- **Auto-labeling = OpenRouter (a cloud VLM).** Captured frames → a cloud VLM returns
  rough boxes for the prompted object → a human corrects them. One-time, at setup,
  never in the loop. Replaces the heavy local auto-labeler (Grounding-DINO/SAM) the box
  can't run.
- **Training = off-box GPU (NOT OpenRouter).** OpenRouter can't run training. The
  labeled dataset is exported and trained on a free Colab / rented GPU / the user's
  machine (we ship a one-click script), producing a `.pt` imported back to the box.
  Keeps the *running* system cloud-free.

## Pipeline

```
capture frames (box)
  → auto-label via cloud VLM / OpenRouter (one-time, opt-in, key-gated)
  → human review/correct (box, browser canvas)        ← the quality gate
  → export YOLO dataset + a training script (box)
  → train YOLOv8-n OFF-BOX (Colab / GPU), import the .pt   ← cloud/GPU touches setup only
  → deploy to Frigate + arm a rule
  → run 24/7 FULLY LOCAL (feeds the existing count/zone/cross matchers)
```

## What's local vs cloud (the principle, kept)

| Step | Where | Cloud? |
| --- | --- | --- |
| Capture frames | box | no |
| Auto-label (bootstrap boxes) | OpenRouter VLM | **yes — one-time, opt-in** |
| Review/correct labels | box (browser) | no |
| Export dataset + train script | box | no |
| Train the detector | off-box GPU (Colab/own machine) | **GPU one-time, user-run** |
| Run the detector 24/7 | box | **no — fully local** |

The monitoring loop never calls the cloud. Snapshots leave the box **only** during the
one-time auto-label step, and only for the frames the user captured for training — not
live footage. (Air-gapped alternative: skip auto-label, label fully by hand.)

## Build slices (each its own PR)

1. **Dataset pipeline (this slice):** capture frames into a per-detector dataset;
   cloud auto-label via OpenRouter (key-gated, graceful, mock-tested); export a
   YOLO-format dataset + a self-contained off-box `train.py` / Colab instructions.
   Backend + minimal UI. *Produces a training-ready dataset + script.*
2. **Review canvas:** browser box-drawing to correct the auto-labels (reuses the
   zone-draw canvas), frame-by-frame — the quality gate.
3. **Import + deploy:** import the trained `.pt`; wire it into Frigate. **Frigate runs
   one detector model** (Open Question 1) → resolve via a combined model, a second
   detection sidecar, or a dedicated-camera model. The fiddliest integration.
4. **Arm:** a rule on the custom class, feeding the existing count/zone/cross matchers.

## Components / config

- **Capture:** reuse the Frigate snapshot grab; store under
  `/data/datasets/<name>/{images,labels}` + a `meta.json` (prompt, class, camera).
- **Auto-label:** a cloud-VLM call (OpenRouter, OpenAI-compatible chat/vision; reuses
  the `_vlm_answer` shape) asking for boxes as JSON → YOLO `labels/*.txt`. Gated on
  `MINDER_OPENROUTER_KEY` (env/config, **never committed**), model
  `MINDER_CLOUD_VISION_MODEL` (e.g. `google/gemini-2.0-flash`); graceful no-op without
  a key.
- **Export:** YOLO `data.yaml` + images + labels, zipped, plus a generated `train.py`
  (ultralytics) and Colab-ready steps.
- **Whole feature is opt-in** (a setup tool, not the loop) — merely shipping it never
  touches the live monitoring path.

## Honest hard parts

- **VLM box quality is rough** — a *bootstrap*, not final; review is essential (the
  manual effort, though correcting beats drawing from scratch).
- **Training needs a GPU somewhere** — off-box import is the clean answer for this box;
  on-box training stays an opt-in "real GPU" tier.
- **Frigate single-detector-model** (Open Question 1) — the deploy step's core
  challenge; default to a dedicated-camera model, offer a combined model for mixed cams.
- **Cost** — auto-label is a handful of VLM calls per dataset (one-time); bounded +
  surfaced.

## Test / demo plan

- **Unit (this slice):** dataset create/capture (mock frame grab); auto-label parse
  (mock OpenRouter boxes → YOLO labels); export writes a valid `data.yaml` + label
  files + `train.py`. Graceful no-op without a key.
- **Live:** capture frames from a camera → export → confirm a valid YOLO dataset; with
  a key, auto-label a few frames and inspect the boxes. Training + deploy validated in
  later slices.

## Open questions

1. **Frigate single-model deploy** — combined vs sidecar vs dedicated-camera (slice 3).
2. **Auto-label box format per model** — which OpenRouter VLM returns the most usable
   boxes (Gemini vs GPT-4o vs Qwen-VL); the export is model-agnostic regardless.
3. **On-box training** as an opt-in later tier for beefier appliances.
