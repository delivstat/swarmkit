# Scenario Studio — severity + VLM escalation (local vs cloud) — Design Note

**Scope:** `examples/minder` — extend a Scenario with a **severity** and an optional
**VLM escalation** stage that runs *after* the deterministic condition matches, routing
high-severity scenarios to a **cloud** VLM (fast + accurate) and routine ones to the **local** VLM.
**Design references:** the three tiers (detect / recognize / **reason**) and the `scenario:` schema
in `scenario-studio.md`; the cloud-VLM `_vlm_answer` shape from `scenario-studio-phase2-quickmode.md`;
the local/cloud model split in `two-layer-vision-models.md` / `model-runtime-options.md`.
**Status:** proposal → **Slice 1 implemented** (the runtime gate).

## Implemented (slice 1) — the escalate gate in the fire path

`frigate/server.py` now honours `severity` + `escalate` on a rule. A matched rule with an
`escalate` block routes through `_deliver_escalated` instead of firing instantly: it waits for the
snapshot, asks the VLM a grounded yes/no (`_vlm_confirm` — cloud for `critical`/`auto`, local
otherwise, cloud→local fallback), and fires the alert + actions **only on a matching answer**. A
trusted "no" is a near-miss (no alert); when verification is impossible a `critical` rule fires
`(unverified)` — never missed. `cooldown_s` rate-limits; a `notify` action raises an urgent
"notifying <contact>" alert. Cloud/local provider is now logged (diagnosable). Tests: `test_escalate.py`.

Slice 1 gates on the **existing flat `condition`** string (e.g. `"person"`) — an escalate rule is:

```json
{ "condition": "person", "severity": "critical",
  "escalate": { "tier": "cloud", "prompt": "Is a person holding a weapon or dangerous object (knife, gun, stick)?",
                "require": "yes", "cooldown_s": 30 },
  "actions": [ {"type": "alert"}, {"type": "notify", "contact": "me"} ] }
```

**Follow-ups (later slices):** the richer `detect:`/structured-`condition` schema below, NL authoring
("alert me if someone has a weapon") → an escalate rule, the dashboard UI + privacy notice, and the
per-day cloud budget cap.

## The one honest tension, up front

`scenario-studio.md` states a core value: **"the monitoring loop never calls the cloud"** — the VLM
"reason" tier escalates only on a flagged candidate, and even that is the *local* reasoning model.
This note **deliberately relaxes that for high-severity scenarios**: a `critical` scenario may call a
**cloud** VLM in the live loop. That's a real change to a stated principle, so it's:

- **opt-in and per-scenario** (never global; default stays local / cloud-free),
- **key-gated** (no cloud without a configured OpenRouter key),
- **surfaced in the UI** with a plain-language "this scenario sends snapshots to the cloud when it
  fires" notice, and
- **budget-capped** (below), degrading to local when the cap is hit.

The privacy/cloud-free guarantee still holds for every scenario that doesn't opt in. For a
weapon/intrusion alert, the owner is trading a frame-to-the-cloud for a materially better, faster
call — their choice to make, explicitly.

## Where escalation sits in the pipeline

The deterministic detector + condition is the **gate**; the VLM is a **confirmation stage** between
the condition matching and the actions firing:

```
Frigate/YOLO detect ─► condition (deterministic gate) ─► [escalate: VLM confirm?] ─► actions
  cheap, 24/7          count/presence/absence/dwell        local or cloud, by severity   alert/device
```

- No `escalate` block → condition-match fires actions directly (today's behaviour, unchanged).
- With an `escalate` block → the condition is the pre-filter ("something worth a closer look"); only
  then does the VLM get a snapshot and a grounded yes/no; only a `yes` fires the actions.

So the VLM never runs 24/7 — the "high gate" the detector provides is what keeps cloud calls (and
cost) rare. **Severity chooses the VLM tier; the condition chooses what's worth escalating.**

## Schema additions

Two new fields on the existing `scenario:` spec — `severity` and `escalate`:

```yaml
scenario:
  name: person-with-weapon
  cameras: [Entrance-Cam]
  zone: entrance
  severity: critical            # info | warning | critical  (drives tier + alert urgency)
  detect: [person]              # NOTE: YOLO/COCO can't see a gun — gate on person, not weapon
  condition:                    # the deterministic GATE (deliberately loose for critical — see below)
    type: presence
    object: person
    debounce_s: 5
  escalate:                     # the VLM confirmation stage (optional)
    tier: cloud                 # local | cloud | auto   (auto = derive from severity)
    model: gemini-2.5-flash     # optional; else the configured default for the tier
    prompt: "Is a person in this image holding a weapon? Answer yes/no + one short reason."
    require: "yes"              # actions fire only if the VLM answer matches
    cooldown_s: 30              # per-scenario, per-zone rate-limit on VLM calls
    fallback: local             # cloud unreachable / over budget → this tier, flagged "unconfirmed"
  schedule: always
  actions:
    - {type: alert, urgency: critical, require_ack: true}
```

Field semantics:

| Field | Meaning |
| --- | --- |
| `severity` | `info` \| `warning` \| `critical`. Drives the default VLM tier (`auto`), the alert urgency, whether a human-ack loop is required, and retention. |
| `escalate.tier` | `local` (appliance VLM), `cloud` (OpenRouter VLM), or `auto` → `cloud` for `critical`, `local` otherwise. |
| `escalate.model` | Optional explicit model; otherwise the tier's configured default (`qwen2.5vl:3b` local, e.g. `gemini-2.5-flash` cloud). |
| `escalate.prompt` | The grounded question. Reuses the `_vlm_answer` shape (phase-2 doc). |
| `escalate.require` | The answer that permits the actions (default `"yes"`). A non-match logs a *near-miss*, no alert. |
| `escalate.cooldown_s` | Rate-limit + dedup: one VLM call per scenario/zone per window. The real cost/spam control (not gate-tightness alone). |
| `escalate.fallback` | On cloud failure / budget exhaustion: fall to this tier and flag the alert `unconfirmed` — never silently drop a critical detection. |

## The routing policy

**`auto` maps severity → tier:** `critical → cloud`, `warning/info → local`. Explicit `tier` always
wins. Two nuances worth stating:

- **"Cloud is faster" is a property of *this* hardware.** The local VLM is faster-to-lose because it's
  CPU-bound on the reference box; a cloud call adds network latency but still wins on total latency
  *and* accuracy today (per the Gemini-2.5-Flash vs qwen2.5vl:3b benchmark). This is **measured, not
  assumed** — the tier defaults live in config, so an appliance that gains an NPU/GPU can flip
  `critical` back to local without touching scenarios.
- **Budget cap.** A per-day (and per-scenario) cloud-VLM call budget; when exceeded, `auto`/`cloud`
  degrade to `fallback` and the dashboard flags it. Cheap insurance against a stuck camera burning
  tokens.

## The gate, and why it inverts with severity

`scenario-studio.md`'s whole point is *don't ask the VLM every frame* — the detector gate keeps
escalation rare. But there's a trap for the weapon case: **YOLO/COCO has no `gun` class**, and
"holding" is a relationship, not an object. If a `critical` weapon scenario gated on *"YOLO sees a
weapon,"* it would miss the very thing it exists for.

So the rule is: **gate strictness scales *inversely* with severity.**

| Severity | Gate | Cloud calls | Rationale |
| --- | --- | --- | --- |
| `info` / `warning` | **tight** — specific objects + zone + confidence | few | save cost; a miss is cheap |
| `critical` | **loose** — a broad signal (e.g. `person` in a sensitive zone) | more | let the *cloud VLM* make the call; a miss is expensive |

For critical scenarios YOLO's job isn't "confirm the weapon" — it's "there's a person here, look
closer," and the cloud VLM decides "armed?". The extra cloud calls are the price of not missing, and
`cooldown_s` + the budget cap keep it bounded.

## Guardrails (non-negotiable)

- **Debounce/dedup** (`cooldown_s`) so a persisting detection is one call, not a stream.
- **Fallback, never drop.** Cloud down or over budget → `fallback` tier, alert flagged `unconfirmed`.
- **Budget cap** with degrade-to-local + a dashboard indicator.
- **Advisory framing** (carried from the parent note): weapon/intrusion detection is an *assist that
  flags for a human*, not a safety-rated guarantee — stated to the user, and `critical` alerts default
  to `require_ack: true`.
- **Privacy notice** on any scenario with `tier: cloud` (or `auto`+`critical`): "snapshots leave the
  appliance when this fires."

## Worked examples

- **Person with a weapon** — `critical`, gate `presence(person)` in `entrance`, `escalate: cloud`,
  prompt "holding a weapon?", `require_ack`. (Loose gate because the gun isn't a YOLO class.)
- **Unfamiliar person at night** — `warning`, gate `presence(person)` + schedule `22:00-06:00`,
  `escalate: local` prompt "is this a delivery/known visitor or someone loitering?".
- **Spill on aisle 3** — `info`, gate `dwell(spill) > 30s`, **no escalate** (detector + rule is enough;
  a puddle needs no cloud reasoning).

Only the first sends frames to the cloud — and only when a person is actually in the entrance.

## Deterministic-first, still

This does **not** move the live *decision* to the VLM in general. The default path stays
detector + deterministic rule (the parent note's philosophy). Escalation is an **opt-in confirmation
layer** for the subjective/high-stakes calls a detector genuinely can't make (armed? menacing?
hurt?), and cloud is just a *tier* of that layer, chosen by severity, gated hard by the detector, and
capped. Everything a trained detector can decide, a trained detector still decides.

## API / runtime touchpoints

- Schema: `severity` + `escalate` on the armed-scenario record (extends the rules store schema).
- Matcher (`frigate/server.py`): after a condition matches, if `escalate` is present, grab the
  snapshot (existing `_grab_frame`), call the tier's VLM (reuse `_vlm_answer`; local = Ollama,
  cloud = OpenRouter, key-gated), apply `require`, honour `cooldown_s` + budget, then fire actions or
  log a near-miss.
- Studio UI: a "Confirm with AI" step in the rule builder — severity picker, tier (with the cloud
  privacy notice), the prompt, cooldown; live preview shows the detector gate *and* the VLM verdict.

## Test plan

- **Routing:** `auto` → cloud for `critical`, local otherwise; explicit `tier` overrides; over-budget
  → `fallback` (unit, mocked clock/budget).
- **Gate + escalation:** condition-match with `escalate` → VLM called once (respecting `cooldown_s`);
  `require` match fires actions, non-match logs a near-miss and does **not** alert; VLM error →
  `fallback` + `unconfirmed` (mock the VLM, same standalone style as `test_sensor.py`).
- **Budget/debounce:** N matches in a window → 1 cloud call; budget exhausted → degrade + flag.
- **E2E:** a recorded entrance clip (person, no weapon) → gate fires, cloud VLM returns "no" → no
  alert; (person + weapon prop) → "yes" → critical alert with `require_ack`.

## Phased delivery

1. **Severity + local escalation** — `severity` field, `escalate` with `tier: local`, `require`,
   `cooldown_s`; matcher confirmation stage + near-miss logging. (No cloud yet — pure local, no
   principle change.)
2. **Cloud tier + budget/fallback** — `tier: cloud|auto`, OpenRouter path (key-gated, mock-tested),
   per-day budget, degrade-to-local, the UI privacy notice.
3. **Studio "Confirm with AI" UI** — the rule-builder step + live gate/verdict preview.

Phase 1 is independently useful and changes no principle; Phase 2 is where the opt-in cloud-in-loop
relaxation lands, behind the guardrails above.
