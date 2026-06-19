# Scenario Studio Phase 1.5 — Zones (Design Note)

**Scope:** `examples/minder` — let a scenario target a **drawn region** of a camera,
not just the whole frame. Builds directly on [[scenario-studio-phase1-count]].
**References:** the count matcher (`frigate/server.py`, `scenario-studio-phase1-count.md`),
the Frigate config generator (`_build_config`), the parent [[scenario-studio]] design.
**Status:** proposal → implementing.

## Goal

Phase 1 counts/matches over the **whole frame per camera**. The flagship scenarios
need a *region*: "max 7 boxes on the **conveyor belt**", "person loitering **at the
gate**", "forklift in the **pedestrian lane**". This phase adds **named zones** (drawn
on a camera) that count/presence rules can target. Still deterministic, still on the
4 GB box, no GPU lift — Frigate already does zone tracking + per-zone counts.

## Why it's mostly wiring

Frigate already supports zones: define a polygon under `cameras.<cam>.zones.<zone>`
and it (a) publishes per-zone object counts on `frigate/<zone>/<object>` and (b) tags
each tracked event with `current_zones`. Minder's Phase-1 paths already consume both:
`handle_count_update(source_key, object, count)` keys on the topic's source segment,
and `_normalize` already extracts `zone` from `current_zones`. So this phase is:
generate the zone config, route zone-keyed counts/events, add a `zone` to rules, and
a draw-zone UI.

## Zone model + storage

A new store `/data/zones.json`, keyed by **camera name** (what rules already use):

```json
{ "Porch-1": [ { "name": "driveway", "points": [[0.1,0.2],[0.6,0.2],[0.6,0.9],[0.1,0.9]] } ] }
```

- `points` are **normalized** [0..1] (x,y) pairs — resolution-independent, so the
  draw canvas works regardless of snapshot size.
- **Global zone key** = `_slug(camera) + "__" + _slug(zone_name)` (e.g.
  `porch_1__driveway`). Frigate zone names are global in MQTT (`frigate/<zone>/...`),
  so they must be unique across cameras — the camera prefix guarantees it and lets us
  map a zone-count topic back to (camera, zone).

## Backend

- **`_load_zones()` / `_save_zones()`** + `_zone_key(cam, name)` / `_zone_index()`
  (key → {camera, name}) helpers in `frigate/server.py`.
- **`_build_config`**: for each camera with zones, emit
  `cams[key]["zones"][zone_key] = {"coordinates": "<x1,y1,x2,y2,…>"}` (flattened
  normalized points). Whole-frame rules are unaffected.
- **Reconfigure**: saving a zone regenerates + applies the Frigate config via the
  existing validated `configure_cameras` path (`/api/config/save?save_option=restart`),
  so a bad zone can never take detection down.
- **Matcher — count in zone**: `handle_count_update` learns zone keys. If
  `source_key` is a zone key, it resolves to (camera, zone); a count rule with a
  `zone` field matches when the update's zone key == the rule's zone key (whole-frame
  rules keep matching the camera-level `frigate/<camera>/<object>` topic). The
  debounce/cooldown/re-arm logic is unchanged.
- **Matcher — presence in zone**: `_match_and_fire_event` gains a zone check — a
  presence rule with a `zone` only fires when the event's `current_zones` contains
  the rule's zone key. Whole-frame presence rules unchanged.
- **Rule shape** (rides the permissive schema, no schema change): add optional
  `zone` (the zone name) to count/presence rules; the matcher resolves it to the
  global key via the rule's camera.

## UI

- **Draw-zone**: pick a camera → show its live snapshot (`/api/cameras/{ip}/snapshot`)
  → draw a region on a canvas → name it → save (→ Frigate reconfigure). v1 ships a
  **drag-rectangle** (covers belts/gates/aisles and is simple + reliable to validate);
  freeform polygon is a follow-up. Existing zones are listed + deletable.
- **Rule targeting**: the count (and presence) rule builder gains a **Zone** dropdown
  populated from the selected camera's zones (+ "Whole frame"). Persists `zone`.

## Not in this phase (follow-ups)

- Freeform polygon drawing (v1 is rectangles).
- `cross` (line-crossing) and richer spatial conditions.
- `absence_on` / co-occurrence (PPE/hygiene) — still its own later phase.
- Per-zone live preview overlay (the Studio wizard's running verdict).

## Test plan

- **Config gen** (standalone, like `test_config.py`): cameras + zones → valid Frigate
  config with a `zones.<key>.coordinates` flattened-normalized string; zone keys are
  globally unique + camera-prefixed.
- **Zone store** round-trip; `_zone_key` / `_zone_index` mapping.
- **Count in zone**: `handle_count_update("<cam>__<zone>", "car", 4)` fires a count
  rule scoped to that zone, and does NOT fire a whole-frame-only or different-zone
  rule.
- **Presence in zone**: an event with `current_zones=[zone_key]` fires a zone
  presence rule; an event outside the zone does not.

## Demo plan

- Define a `driveway` zone on Porch-1 in the dashboard → Frigate reconfigures →
  add "> 3 car in driveway" → publish `frigate/porch_1__driveway/car = 5` → alert
  fires (and a whole-frame "> 3 car on Porch-1" still works independently). Live on
  the box, same as the Phase 1 count demo.
