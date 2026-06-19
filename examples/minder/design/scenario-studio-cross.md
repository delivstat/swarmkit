# Scenario Studio — Cross Condition (Design Note)

**Scope:** `examples/minder` — a directional "cross a boundary" condition, building on
zones ([[scenario-studio-phase15-zones]]). **Status:** implemented.

## Goal

Fire when a tracked object **enters or leaves** a region — "forklift crosses into the
pedestrian lane", "someone enters the restricted area", "a car leaves the driveway".
The distinguishing feature vs presence is **direction** (enter vs leave) and that it
fires on the *transition*, not while the object is merely present.

## Model: a line crossing = entering/leaving a (thin) zone

Rather than a separate free-line primitive (which needs per-frame trajectory tracking
and resolution-aware geometry), a cross is modelled as a **zone enter/leave**: draw a
thin zone over the boundary, and crossing it = entering that zone. This reuses the
zone editor and Frigate's native per-object zone tracking — deterministic, no LLM, no
new geometry. Direction (enter/leave) gives the "which way" that presence lacks.

## How it works

Frigate's live event stream reports each tracked object's `current_zones` per update.
`handle_cross_event` (frigate/server.py) tracks per-object zone membership across
updates (`state["_cross_zones"][event_id]`, TTL-pruned), computes the `entered` /
`left` zone sets on each update, and fires a matching cross rule on the transition:

- Runs on **every** MQTT event (before the presence dedup) — it needs the continuous
  stream to see transitions. **Real-time (MQTT) only**; the per-minute REST poll can't
  see transitions, so cross has no poll backstop (noted to the user).
- Matches camera + object label (empty object = any) + the rule's zone key + direction
  (`enter` default / `leave`). Per-`camera|zone|direction` cooldown (`ALERT_COOLDOWN_S`)
  prevents boundary-flicker / busy-boundary spam, consistent with count/presence.
- Fires via the shared `_fire` (alert fan-out + device actions + shadow mode).
- `_match_and_fire_event` skips cross rules (handled on their own path — no double-fire).

## Rule shape (rides the permissive schema, no schema change)

```json
{ "condition_type": "cross", "object": "car", "zone": "lane", "direction": "enter",
  "camera": "Porch-1", "actions": [{"type":"alert"}] }
```

## UI

The rule builder's Type selector gains **Cross — enters or leaves a region**: object
(or "anything") + direction (enters/leaves) + region (the camera's zones). Authored
like any rule; draw the boundary zone with the existing zone editor.

## Not in this phase (follow-ups)

- True free **line** crossing with arbitrary A→B direction (centroid-trajectory vs a
  2-point line) — heavier, resolution-aware; the zone-transition model covers the
  common cases.
- Directional counting (people in vs out tallies).

## Test plan

`mcp-servers/frigate/test_cross.py` (standalone): fires on the enter transition (not
while present), leave direction, object/camera filter, the per-zone cooldown, and that
the presence matcher ignores cross rules. (Validated by feeding synthetic Frigate
events with changing `current_zones` — no moving object needed.)
