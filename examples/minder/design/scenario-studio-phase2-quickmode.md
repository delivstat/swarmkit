# Scenario Studio Phase 2 — Quick Mode (open-vocab "detect anything") — Design Note

**Scope:** `examples/minder` — detect **any object described in words** ("cardboard
box", "hard hat", "puddle") with **zero training**, so a scenario can watch for
site-specific things the stock Frigate model (person/car/dog/cat) doesn't know.
Builds on the deterministic condition grammar (count/zones/cross) — quick mode is a
new *detection source*; the rules + matchers are unchanged.
**Design references:** parent [[scenario-studio]] (§"Phased plan" item 2, §"Quick
mode", §"Resource reality", Open Question 2); the count/zone/cross matchers; the VLM
runtime decision ([[project_minder_vision_runtime]]).
**Status:** proposal — **needs a go/no-go on the runtime approach before implementation**
(it's the first thing that runs a new heavy model on the live box).

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

## Recommended approach: YOLO-World on CPU, on-demand (not 24/7)

- **Model:** ultralytics **YOLO-World-s** (`yolov8s-worldv2`, ~25–50 MB) — the lightest
  open-vocab option, and the detect server already uses ultralytics, so it's a small
  dependency add (+ the CLIP text-encoder weights). Grounding-DINO/SAM are far heavier
  and are out for this box.
- **Where:** **CPU**, never the Frigate detector slot (Frigate runs one fixed model).
  A separate on-demand detection path, mirroring `_grab_frame` + the VLM.
- **Cadence (the key to bounding load):** quick-mode scenarios are evaluated **on a
  cadence, not every frame** — either every N seconds (default ~30 s) or, better,
  **triggered by a Frigate motion/event** on that camera (so the CPU only works when
  something moves). One CPU inference is ~1–5 s; at this cadence it won't starve the
  3B or Frigate.
- **Feature-flagged OFF by default** (`MINDER_QUICKMODE=off`) and **graceful if the
  model/deps are absent** — so the live box is never destabilised by merely shipping
  the code; it's opt-in per appliance.

## Architecture

```
quick-mode scenario (text prompt + count/presence/cross rule)
        │  on a cadence / on Frigate motion
        ▼
  _grab_frame(camera)  ──►  open-vocab detector (YOLO-World, CPU, set_classes([prompt]))
        │                         │ boxes + labels for the prompted class
        ▼                         ▼
  feeds the SAME deterministic matchers (count in zone, presence, cross) — unchanged
```

- A new `mcp-servers/openvocab/` (or extend `detect/`) module: `detect_prompt(image,
  phrases) -> [{label, score, box}]` via `YOLO('yolov8s-worldv2.pt').set_classes(...)`.
- A quick-mode evaluator (like `frigate_poller`/`mqtt_listener`): for each armed
  quick-mode scenario, grab a frame on its cadence, run `detect_prompt`, compute the
  count/zone/presence, and fire through the existing alert path.
- The rule carries `detector: "openvocab"` + `prompt: "cardboard box"`; otherwise it's
  the same count/presence/cross shape — so all the matcher logic is reused.

## Studio UI (minimal — define → prompt → rule → arm)

The rule builder gains a quick-mode toggle: instead of picking a stock object, type a
**prompt** ("cardboard box"); pick the condition (count/presence/cross) + zone + action
+ cadence; arm. A "test" button runs one detection on a live snapshot and shows the
boxes/score so the user can judge accuracy before arming (open-vocab is fuzzier).

## Accuracy + honesty

Open-vocab is **prototype-grade** — it will miss/over-detect more than a trained model
and is sensitive to the prompt. Stated plainly in the UI; the "promote to a trained
model" path (Phases 3–4) is how a scenario graduates to production accuracy.

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

## Open questions / the decision to confirm

1. **Runtime: CPU on-demand (recommended) vs GPU-with-3B-eviction vs import-a-model
   only.** CPU on-demand is the only option consistent with this box's observed
   GPU-thrash; GPU risks OOM + disrupts chat; import-a-model skips on-box open-vocab.
2. **Cadence: fixed interval vs Frigate-motion-triggered** (motion-triggered is
   cheaper and preferred).
3. **Dependency footprint** — adding ultralytics YOLO-World + CLIP weights to the image
   (and whether to lazy-download the model on first enable vs bake it in).
