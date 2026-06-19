# Scenario Studio Phase 1 — Count Conditions (Implementation Note)

**Scope:** `examples/minder` — first slice of [[scenario-studio]] (§"Phased plan"
item 1, §"Condition grammar"). Ships the `count` condition end-to-end; no
training/auto-label/Studio-wizard yet (those are Phases 2–4).
**Status:** implemented.

## What shipped

A **count rule** fires when `count(object on a camera) {op} value` has held for
`debounce_s`, evaluated deterministically — **no LLM at match time**. It works with
the **stock Frigate model** (person/car/dog/cat), so it's useful immediately
("> 3 cars in the driveway") without any custom detector.

### Source — Frigate object-count MQTT topics

Frigate publishes a retained integer count on `frigate/<camera|zone>/<object>`. The
webapp's MQTT listener now also subscribes to `frigate/+/+` (3-level, so it never
collides with the 2-level `frigate/events`) and treats any integer-payload message
as a count update → `frigate.handle_count_update(source_key, object, count)`.
Non-count topics (motion `ON`/`OFF`, etc.) are filtered by the integer-payload check.

Counts are **whole-frame per-camera** for now (the live Frigate config has no zones).
Per-zone counts (`frigate/<zone>/<object>`) flow through the *same* path unchanged
once zones are configured — the matcher already keys on the topic's source segment.

### Rule shape (round-trips via the permissive rule schema, no schema change)

```json
{
  "condition": "more than 3 car on Porch-1",   // human label (display + reuse the desc path)
  "camera": "Porch-1",                          // or "cameras": [...]
  "condition_type": "count",
  "count_object": "car",                        // person | car | dog | cat (synonyms normalized)
  "count_op": ">",                              // > >= < <= ==
  "count_value": 3,
  "debounce_s": 3,
  "schedule": "always",
  "actions": [{"type": "alert"}]                // + {type: device} like any rule
}
```

### Matcher (frigate/server.py)

- `_eval_count_rule` — debounce + cooldown + re-arm: fires once when the condition
  has held for `debounce_s`, never re-fires within `ALERT_COOLDOWN_S`, and re-arms
  only after the count drops out of the condition again. State under `state["_count"]`.
- `handle_count_update` — the shared, testable core: loads active count rules,
  matches camera (`_camera_match`) + object (`_count_object_label`, same synonym
  table presence uses), evaluates, and fires via the existing `_fire` (so device
  actions + alert fan-out + shadow mode all come for free).
- `_match_and_fire_event` now **skips** count rules — their condition text mentions
  the object ("…cars…"), which would otherwise also match the per-event presence
  path and double-fire.

### UI (rules editor)

The dashboard "Add Rule Manually" card gained a **Type** selector (Presence /
Count). Count shows object + operator (more than / at least / fewer than / at most)
+ value + hold-seconds; `dashAddRule` persists a count rule with a generated
human-readable `condition`. Authored exactly like any other rule (lands in
`rules.json`, enforced by the deterministic matcher) — Studio is just the eventual
richer authoring surface.

## Not in Phase 1 (follow-ups)

- **Zones** — per-zone counts (drawn regions) — needs the Frigate zone config; the
  matcher path is already zone-ready.
- **NL authoring** of count rules ("alert if more than 3 cars in the driveway")
  via the router — Phase 1 is the editor + matcher; NL is a small follow-up.
- **Live preview overlay** (the Studio wizard's "6/7 — OK, flips red at 8").
- The training pipeline (capture → auto-label → train → deploy) — Phases 2–4.

## Tests

`mcp-servers/frigate/test_count.py` (standalone, like `test_sensor.py`): comparator;
object normalization; debounce-then-fire; re-arm after clear; cooldown blocks rapid
re-fire; `handle_count_update` end-to-end (camera+object match, ignores other
camera/object); and that the presence matcher skips count rules (no double-fire).

## Demo (verified live on the box)

Added a `> 3 car on Porch-1` count rule, published `frigate/porch_1/car = 2` (no
fire) then `= 5` → alert fired and landed in the dashboard log + fanned out to
channels: *"more than 3 car on Porch-1 — Porch-1: 5 car (limit >3)"*.
