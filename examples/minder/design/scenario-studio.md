# Minder Scenario Studio — On-Site Trainable Detection (Design Note)

**Scope:** `examples/minder` (new "Scenario Studio" capability — capture → label →
train → deploy → rule, all on the appliance).
**Design references:** the three-tier model in `vision-architecture.md` (detect /
recognize / reason); the deterministic rule engine in `frigate-mqtt-events.md`;
the router/plan model in `conversation-router.md`.
**Status:** proposal — not yet implemented. Roadmap + architecture for review.

## Goal

Let a non-expert teach Minder to detect **anything specific to their site** — and
act on it — using **nothing but the Minder appliance**. No cloud, no ML toolchain,
no data scientist. Point a camera, say what to watch for, correct a few boxes,
press train, and Minder is now watching for it 24/7 with deterministic rules.

The same machinery must serve wildly different sites with no special setup:

- **Logistics:** "no more than 7 boxes on the conveyor" → stop the belt.
- **Restaurant hygiene:** "anyone in the kitchen without a hairnet" → alert the manager.
- **Dark/industrial factory:** "a person in the press hazard-zone while it's running" → e-stop.
- **Retail / safety:** "a spill on aisle 3", "a forklift in the pedestrian lane", "PPE: a worker without a hard hat".

These differ only in *what object* and *what condition* — the pipeline is identical.

## Non-goals

- **Not** a general cloud ML platform. On-site, single-appliance, local-only.
- **Not** an LLM feature. The detector is a small supervised vision model; a
  foundation model assists *labeling*, once, never in the live loop.
- **Not** replacing Frigate's stock detection — this *adds* site-specific classes
  and a richer condition grammar on top of it.
- **Not** safety-certified. For life-safety interlocks (e-stop) this is an
  advisory layer, not a substitute for a rated safety system. (Stated plainly to
  the user.)

## The idea in one line

The expensive "understand an image" model runs **once, at setup, to label data**;
training distills that into a **tiny fast detector**; and the 24/7 loop is then
just **deterministic detection + a numeric/zone rule** — the same tiered
philosophy as the rest of Minder (LLM/foundation models for the hard one-off,
code for the doing).

## Where this fits — the three tiers (recap)

| Tier | Question | Cost | Role here |
| --- | --- | --- | --- |
| **Detect** (YOLO) | "is there an *X*, and where?" | cheap, 24/7 | the trained custom detector |
| **Recognize** (embeddings) | "is this the *same* thing / *who*?" | cheap | optional (faces, known items) |
| **Reason** (VLM) | "*describe/judge* this scene" | expensive | escalation only, on rare matches |

Scenario Studio is a factory for new **Detect-tier** capabilities. Counting,
presence, PPE, spills — all are detection + a rule, not reasoning. (Critically:
VLMs are unreliable at exact counts and consistent yes/no rule calls, so the live
decision must be a trained detector + deterministic compare, never "ask the VLM
each frame.")

## Recognizer types (not everything is object detection)

"Object detection" answers *"where are the Xs?"* — perfect for boxes, people, PPE,
litter. But many real questions aren't that shape, and forcing them into detection
either fails or wastes effort. A Scenario picks the **lightest recognizer that
answers its question**; Studio supports a small family of them, and the capture →
label → review → train → deploy → rule flow is shared across all (only the model
type and the labelling UI differ).

| Recognizer | Answers | Good for | Cost / notes |
| --- | --- | --- | --- |
| **Object detection** | "where are the Xs, how many?" | count, presence, PPE, spills, intrusion | the default; one box per object |
| **State classification** | "what state is this region in?" | gate open/closed, light on/off, door up/down, valve position | tiny — a 2–3 class classifier on a fixed ROI; very reliable in a fixed scene |
| **Anomaly / embedding** | "does this look different from normal?" | "street clean or not", "is anything out of place" | learn a clean baseline, flag large embedding distance; fuzzier, needs a threshold |
| **Temporal composition** | "did a sequence happen over time?" | hand-wash proxy, loitering, queue too long | not a model — detection + `dwell`/`cross`/sequence + sensors (the condition grammar) |
| **Action recognition** | "is this *activity* happening?" | true hand-washing, falls, fighting | video model; needs clip training data; heavy — a future tier |
| **VLM escalation** | "is this subjectively OK?" | the rare nuanced/edge call | slow; on a *flagged candidate* only, never 24/7 |

How the recurring example questions actually map:

- **"Is the gate open or closed?"** → **state classification** on the gate ROI (or
  even the embedding trick: compare to an enrolled "open"/"closed" reference). One
  of the *easiest* scenarios — fixed object, two states, a handful of examples.
- **"Is the street clean?"** → **anomaly/embedding** vs a clean baseline, *or*
  bound it as **object detection** of known litter where "clean = zero detections
  in the zone". Pick detection when the failure modes are finite, anomaly when
  they're open-ended.
- **"Did the employee wash their hands?"** → **temporal**, not single-frame.
  Object detection alone *cannot* decide this. Feasible today as a **composition
  proxy**: `person in sink-zone` + `dwell ≥ N s` (+ optional tap/flow sensor via
  the existing sensor-trigger rules) ≈ washed. True verification needs **action
  recognition** (a heavier future tier) or pose estimation — stated plainly so the
  proxy isn't oversold as proof.

Rule of thumb: **fixed thing, few states → classify; bounded bad-things → detect;
open-ended "looks wrong" → anomaly; "happened over time" → compose; genuine
activity → action model; subjective edge → escalate to the VLM.** Most useful
scenarios land in the first four (cheap, deterministic); only the last two are
heavy, and they stay rare.

## Concept: a Scenario is a composable spec

```yaml
scenario:
  name: conveyor-box-cap
  cameras: [Belt-Cam-1]
  zone: belt                # drawn region, or whole frame
  detect: [box]             # custom class(es) this scenario trained
  condition:                # the rule grammar (see below)
    type: count
    object: box
    op: ">"
    value: 7
    debounce_s: 3
  schedule: always
  actions:
    - {type: alert}
    - {type: device, device: "Conveyor Relay", action: turn_off}
```

Same shape, different site:

```yaml
scenario:
  name: kitchen-hairnet
  cameras: [Kitchen-Cam]
  zone: prep-area
  detect: [person, hairnet]
  condition:                # person present WITHOUT an overlapping hairnet
    type: absence_on
    subject: person
    required: hairnet
    overlap: head
    dwell_s: 5
  schedule: "06:00-23:00"
  actions: [{type: alert}]
```

## The on-site workflow

All steps run on the appliance; the only heavy models (auto-labeler, trainer) are
pulled on demand for the setup job and are **not resident** during monitoring.

```
1. CAPTURE     pull a burst of frames from the live camera(s) + recent recordings
2. AUTO-LABEL  a foundation model (Grounding DINO / YOLO-World / SAM) pre-draws
               boxes from a text prompt ("cardboard box", "hairnet")
3. REVIEW      human corrects/adds/deletes boxes in the browser — the quality gate
4. TRAIN       Ultralytics YOLOv8-n trains on the labelled set (background job)
5. DEPLOY      export → register as a Frigate custom model + add class to the
               camera's objects.track + (re)draw the zone
6. RULE        compose the condition (count / presence / absence / dwell) + action
7. TEST        run the detector live, show detections + rule verdict in real time
8. ARM         activate — the deterministic poll/MQTT matcher now enforces it 24/7
```

"Quick mode" skips 3–5: use the open-vocab detector (YOLO-World) directly from the
text prompt — zero training, lower accuracy — to prototype a scenario in minutes,
then "promote" it to a trained model when it's worth the accuracy.

## UI: Scenario Studio (dashboard wizard)

A guided flow in the Minder dashboard (sits beside the existing Rules tab):

1. **Define** — name it, pick camera(s), draw the zone on a live still.
2. **Capture** — "Collect frames": grabs a rolling burst (and offers recent
   Frigate events for the camera). Grid of thumbnails; user can keep/discard.
3. **Targets** — type what to detect in plain words ("cardboard box"). Minder runs
   the auto-labeler over the captured frames and pre-draws boxes.
4. **Review** — annotation canvas: drag/resize/add/delete boxes, fix the class.
   Progress: "142 / 200 frames reviewed". This is the only manual labour and it's
   correcting, not drawing-from-scratch.
5. **Train** — one button → background job with a progress bar + ETA; the user can
   leave. (Or "Use without training" → quick mode.)
6. **Rule** — pick the condition (presence / count threshold / absence / dwell),
   schedule, and action (alert / device) — the same builder as the Rules tab,
   extended with the new condition types.
7. **Preview** — live overlay on the camera: boxes + the running verdict
   ("6/7 boxes — OK", flips red at 8). Tune threshold/debounce before arming.
8. **Arm** — deploys the model + activates the scenario. Editable/disablable later
   like any rule.

Principle: the user never sees a YAML, a label format, or a CLI — Studio is the
authoring surface, exactly as the rules editor is for rules.

## Architecture / components

```
            ┌───────────────────────── Scenario Studio (dashboard UI) ─────────────────────────┐
            │ define → capture → targets → review → train → rule → preview → arm                │
            └───────────────────────────────────┬──────────────────────────────────────────────┘
                                                 │ /api/studio/*
        ┌────────────────────────────────────────┴───────────────────────────────────────┐
        │ detector-trainer service (new) — orchestrates the one-off setup job             │
        │   capture frames ─ auto-label (foundation model) ─ store dataset ─ train (YOLO) │
        │   ─ export model ─ register in model registry (/data/models/<scenario>/)        │
        └───────────────┬───────────────────────────────────────────────┬────────────────┘
                        │ deploy                                         │ heavy models pulled
                        ▼                                                ▼ on demand (not resident)
        ┌──────────────────────────────┐                  ┌──────────────────────────────────┐
        │ Frigate sidecar              │                  │ auto-labeler: Grounding DINO /    │
        │  custom model + zone + track │                  │   YOLO-World / SAM                │
        │  → events + per-zone counts  │                  │ trainer: Ultralytics YOLOv8-n     │
        └──────────────┬───────────────┘                  └──────────────────────────────────┘
                       │ MQTT events + zone counts
                       ▼
        ┌──────────────────────────────────────────────┐
        │ deterministic matcher (frigate/server.py)     │
        │  condition grammar → alert / device action    │  ← extends today's label-in-zone match
        └──────────────────────────────────────────────┘
```

- **Capture** reuses the existing frame/clip grab (`_grab_frame` / Frigate
  snapshots) plus a "pull recent events" path.
- **Auto-labeler**: an open-vocab detector/segmenter run *once per capture batch*
  to bootstrap boxes from a text prompt. Heavy, transient, on GPU/CPU.
- **Review** is browser-side (canvas) + a label store under `/data/datasets/<scenario>/`.
- **Trainer**: Ultralytics YOLO, a background job writing to `/data/models/<scenario>/`.
- **Model registry**: versioned per scenario; "deploy" wires the model into the
  Frigate config (or a detection sidecar — see open questions on one-model-per-Frigate).
- **Rule engine**: extended condition grammar (below), evaluated by the existing
  deterministic poll/MQTT matcher — no new always-on LLM.

## Condition grammar (the rule-engine extension)

Today the matcher does **label-in-zone presence**. Scenarios need more — kept as a
small, deterministic grammar (no LLM at match time):

| Condition | Meaning | Example |
| --- | --- | --- |
| `presence` | object in zone (today's behaviour) | person at gate |
| `count` | `count(object in zone) {>,<,>=,<=} N` | > 7 boxes on belt |
| `absence` | object expected in zone is missing | no attendant at counter |
| `absence_on` | subject present WITHOUT a required co-object overlapping it | person without hairnet/helmet |
| `dwell` | object in zone continuously for > T | loitering; spill persists |
| `cross` | object crosses a line/zone boundary | forklift enters pedestrian lane |

All evaluated from Frigate's tracked objects + per-zone counts (already published
over MQTT), with debounce/hysteresis to avoid flicker. `absence_on` /
co-occurrence needs spatial overlap of two detected classes — the most advanced;
ships after the simpler count/presence/dwell.

## Worked examples (same machinery)

- **Conveyor cap:** detect `box`, zone `belt`, `count > 7`, debounce 3 s → alert +
  stop relay. (Clean top-down scene → classical-CV fallback even without training.)
- **Kitchen hygiene:** detect `person` + `hairnet`, `absence_on(person, hairnet, head)`,
  dwell 5 s, 06:00–23:00 → alert manager.
- **PPE on a site:** detect `person` + `hard_hat`, `absence_on(person, hard_hat, head)`
  in zone `yard` → alert.
- **Dark factory interlock:** detect `person`, zone `press-hazard`, `presence` while
  a machine-running sensor is on (combine with the **sensor-trigger** rules we
  already have) → e-stop relay (advisory).
- **Retail safety:** detect `spill`, zone `aisle`, `dwell > 30 s` → alert cleaning.

## Resource reality (honest)

The appliance's GPU is the constraint (reference box: 4 GB GTX 1050 Ti, already
hosting the reasoning model).

- **Inference** of a trained YOLOv8-n in Frigate is cheap and runs 24/7 fine.
- **Training** is the heavy one-off. YOLOv8-n trains on a 4 GB GPU but slowly and
  contends with the reasoning model. Options, surfaced to the user:
  1. **Background training with a heads-up** — "training will use the box heavily
     for ~20–60 min; alerts continue but chat may be slow." Pause/evict the
     reasoning model during the job.
  2. **Import-a-model** — train on a beefier machine (or a one-time cloud job the
     user runs themselves) and import the model file; the appliance only does
     capture/label/deploy/run. Keeps the *running system* cloud-free.
  3. **Quick mode (no training)** — open-vocab detector at inference; works on the
     appliance immediately, trade accuracy for zero training.
- **Auto-labeler** runs once per capture batch — minutes, acceptable transient load.
- **Single-model-per-Frigate** caveat (see open questions): a custom class may
  require a combined model or a second detection pass.

## API shape (new endpoints, dashboard-only)

```
POST /api/studio/scenario              create/draft a scenario (name, cameras, zone)
POST /api/studio/<id>/capture          grab N frames (+ recent events) → dataset
POST /api/studio/<id>/autolabel        run foundation model over the batch (prompt)
GET/POST /api/studio/<id>/labels       fetch/save reviewed annotations
POST /api/studio/<id>/train            start background training; GET → progress
POST /api/studio/<id>/deploy           register model + wire Frigate (model+zone+track)
POST /api/studio/<id>/preview          live detections + rule verdict (test before arm)
POST /api/studio/<id>/arm              activate scenario (persists a rule)
```

The armed scenario lands in the existing rules store (extended schema) and is
enforced by the deterministic matcher — Studio is just the authoring path.

## Phased plan (milestone-level; each phase its own design note + PRs)

1. **Condition grammar** — extend the matcher with `count` (+ debounce) and zone
   reads from Frigate's per-zone counts; rule schema + rules-editor UI. *(Unlocks
   the conveyor case with the stock model where a class already exists, and is
   useful on its own — e.g. "> 3 cars in the driveway".)*
2. **Quick mode** — open-vocab detector wired as an on-demand detection source +
   a minimal Studio (define → prompt → rule → arm), no training.
3. **Capture + auto-label + review UI** — the dataset pipeline and annotation canvas.
4. **On-box training + model registry + Frigate deploy** — the trainer job, model
   versioning, and the deploy wiring (resolve the single-model caveat).
5. **Advanced conditions** — `absence_on` / co-occurrence (PPE, hygiene), `dwell`,
   `cross`; combine with sensor triggers for interlocks.

Phase 1 is independently valuable and the natural first PR; the rest builds the
self-service Studio on top.

## Test plan

- **Condition grammar:** unit tests per condition (count >/</threshold + debounce;
  presence; absence_on overlap; dwell timing) against synthetic Frigate
  events/zone-counts — same standalone style as `test_sensor.py`.
- **Pipeline:** dataset round-trip (capture → label store → train stub → registry);
  deploy writes a valid Frigate config with the new class + zone (assert via the
  config generator, like `test_config.py`).
- **Detector quality:** a held-out frame set per scenario → precision/recall report
  surfaced in Studio so the user sees accuracy before arming.
- **End-to-end:** a recorded belt clip with a known box count → assert the rule
  fires at the right frame; a kitchen clip with/without hairnet → assert
  absence_on fires only when missing.

## Demo plan

- **Phase 1 demo:** "> N" rule on a stock class live (e.g. cars in the driveway),
  shown firing in the dashboard preview + a Telegram alert.
- **Full demo (recorded transcript + screencast):** open Studio → capture from a
  belt camera → type "cardboard box" → review ~10 corrected frames → train →
  preview overlay counting boxes → arm → drop an 8th box on the belt → alert +
  belt-stop. The whole loop on the appliance, no external tools.

## Open questions

1. **One model per Frigate detector.** Frigate runs a single detection model. A
   custom class either (a) trains a *combined* model (stock classes + new), (b)
   runs a **second detection sidecar** for the custom class, or (c) is a
   dedicated-camera model (fine for single-purpose cams like a conveyor). Decide
   per-scenario; default to (c), offer (a) for mixed cams.
2. **Training compute.** Is on-box training acceptable on the reference GPU, or do
   we lead with import-a-model + quick mode and treat on-box training as an
   "appliance with a real GPU" tier? Likely: quick mode + import first, on-box
   training as an opt-in heavy job.
3. **Classical-CV fallback.** For clean, controlled scenes (conveyor) a non-ML
   blob/contour counter is faster and needs no training — worth a "no-AI" detector
   option in Studio for such cases.
4. **Safety framing.** Interlock/e-stop scenarios must be labelled advisory and
   gated behind an explicit acknowledgement; never imply a safety rating.
5. **Drift / re-training.** Lighting/seasonal/layout drift degrades detectors —
   Studio should make "add more frames + retrain" a one-click loop and track
   per-scenario accuracy over time.

## Why this fits Minder

- **On-site, cloud-free in the loop** — capture/label/train/run all on the
  appliance; the only optional cloud is a one-off user-run training job, never the
  monitoring path.
- **Tiered, code-does-the-doing** — foundation model labels once; a tiny detector
  + deterministic rule does the 24/7 work; the VLM only escalates rare matches.
- **Authoring-first** — Studio is to detection what the rules editor is to rules:
  the human teaches and approves; the runtime executes deterministically.
- **One mechanism, many sites** — boxes, hairnets, hard hats, spills, forklifts:
  all "(object) (where) (condition) → (action)", trained and armed without leaving
  Minder.
