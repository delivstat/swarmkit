# Minder Vision Architecture — Design Note

## Goal

Give Minder a robust 24/7 home-security perception layer that works across a
**heterogeneous camera fleet** (proper RTSP/ONVIF cameras *and* cloud-locked
WiFi cameras), without Minder reinventing an NVR and without any dependency on
cloud LLMs for monitoring.

The shape: **three execution substrates, one brain.** Minder/SwarmKit stops
doing pixel-level detection (which it was never good at) and becomes the
orchestration + authoring + alerting brain over two mature perception
substrates.

## Non-goals

- Replacing Home Assistant's role in device control (unchanged).
- Cloud LLMs anywhere in the monitoring path. The VLM is local (Ollama) and
  fires only on events, never on the live stream.
- Making the cloud-locked cameras (Xiaomi) security-critical. They ride a
  lower-fidelity tier by design.
- Auto-deriving Frigate pixel **zones** from natural language (not reliably
  possible — see Open Questions).

## The model: three substrates, one brain

| Substrate | Owns | Minder's relationship |
| --- | --- | --- |
| **Frigate** (new sidecar) | perception for RTSP cameras — motion-gating, object detection, tracking, zones, crop-and-zoom VLM enrichment | configures it from discovery; reads its events/snapshots/clips |
| **Home Assistant** (existing sidecar) | device control **+** integration & on-device detection for cloud-locked cameras (Xiaomi via Mi Home) | reads camera/motion sensor events; controls devices; (later) deploys reflex automations |
| **Minder / SwarmKit** | the brain — NL scenario authoring, unified event normalization, the rule engine, alerting, conversational queries | orchestrates all of it |

### Why Frigate rather than building it

Minder's current `run_monitoring_rules` is a cron-driven YOLO-on-frames loop
with no motion gating, no tracking, and no zones. Frigate provides all three
plus a built-in Ollama `genai` hook that *is* the YOLO→crop→VLM "detect then
describe" pattern. Adopting it is a robustness win, and it does **not** violate
SwarmKit-native principles: Frigate sits behind an MCP/skill seam exactly like
HA already does. SwarmKit consuming a real NVR through a skill is a *stronger*
demo than running YOLO in a subprocess.

## Camera tiering

Each camera is classified at discovery and stored with a `tier`:

| Tier | Cameras (this deployment) | Pipeline | Role |
| --- | --- | --- | --- |
| **frigate** | CP PLUS (RTSP/ONVIF, Dahua-OEM) | Frigate: motion-gate → detect → track → zones → VLM enrichment → event | always-on intrusion alerting at critical points (gate, door, pathway) |
| **ha-snapshot** | Xiaomi (Mi Home, no local RTSP) | HA surfaces the camera's own on-device motion/person detection as `binary_sensor` entities; Minder reads them | secondary/interior, soft motion alerts, on-demand snapshot view |

The user never sees the tier. Minder presents one uniform "camera" concept and
routes behind it.

## Event unification — the heart of the design

The non-negotiable rule: **one brain, not two automation engines.** Events from
both substrates flow into a single normalized stream that Minder's rule engine
evaluates and from which all alerts are delivered.

```
Frigate events  (RTSP cams) ─┐
                             ├─► normalize ─► Minder rule engine ─► alert (Telegram)
HA motion/person (Xiaomi)  ──┘                (scenarios)          + dashboard event
```

A **normalized event** is `{source, camera, label, zone?, ts, confidence?,
snapshot_ref, clip_ref?, description?}` — `source` ∈ {frigate, ha}. The rule
engine matches the user's scenarios (camera + object/label + schedule + optional
zone) against this stream regardless of which substrate produced the event.
Alerting, the dashboard event log, and the conversational "what triggered"
queries all read this one stream — they never fragment across HA's logbook and
Minder's store.

## Scenario compilation

A natural-language scenario (authored by the existing `minder-scenario` worker
agent) compiles to one of three execution targets, chosen by Minder:

- **Frigate event filter** — RTSP-camera detection rule (camera + label + zone +
  schedule). Evaluated against the unified event stream.
- **Minder-managed rule** — needs cross-source reasoning, rich alert text, or a
  schedule/condition Frigate can't express. Evaluated in Minder. (This is what
  `rules.json` holds today.)
- **HA automation (reflex)** — *later phase.* Fast local sensor→actuator rules
  ("Xiaomi motion → hallway light") that should run in HA: instant, no
  round-trip, survive Minder downtime. Authored in Minder, deployed to HA, and
  **tracked by Minder** so the conversational view stays complete.

Day one, scenarios stay Minder-managed rules evaluated against the unified
stream; HA-automation compilation is a later enhancement.

## Graceful degradation (security discipline)

- **Detection never depends on Minder or the VLM.** Frigate's detector and the
  Xiaomi's on-camera detection run independently. If Minder, Ollama, or the
  network hiccups at 3am, detection still happens and the base alert
  ("person in driveway") still fires.
- **VLM enrichment is additive.** The descriptive text ("delivery person left a
  package") is a nice-to-have layered on top; its absence never blocks an alert.
- **Pin the VLM** (`OLLAMA_KEEP_ALIVE=-1`) so it isn't reloaded per event.

## What changes vs. what stays

**Stays (reused as-is):**
- ONVIF discovery (`camera/server.py` `discover_cameras`) — now *feeds Frigate's
  config* instead of being the detection path. Discovery is Minder's onboarding
  value.
- The alert delivery pipeline: `pending_alerts.json` → `/api/ops/alerts` →
  `bot.py poll_alerts` (10s) → Telegram. New event sources write into the same
  pending-alert + `events.json` sink.
- HA token management, `get_ha_devices`, `control_device`, `ha-init.sh`.
- The `minder-scenario` worker agent + `_persist_scenario` rule store.
- The dashboard event log (`events.json`).

**Changes / new:**
- **New `frigate` service** in `docker-compose.yml` (+ a generated Frigate
  `config.yml`; optional `mosquitto` later for MQTT push).
- **New `frigate` MCP server** — `configure_cameras` (write Frigate config from
  `cameras.json` RTSP entries), `get_events`, `get_event_snapshot`,
  `get_event_clip`.
- **`cameras.json` gains `tier` + `ha_entity`** fields; discovery classifies
  RTSP vs cloud-locked.
- **`run_monitoring_rules` (cron YOLO loop) is retired** in favour of a
  **Frigate event poller** + an **HA motion-sensor reader**, both feeding the
  unified event stream. The `monitor-cameras` cron trigger changes from "run
  YOLO rules" to "poll Frigate `/api/events` since last cursor + read HA motion
  sensors, then apply scenarios."
- **`get_ha_devices` extended** (or a sibling `get_ha_camera_events`) to read
  `binary_sensor` motion/person entities for the Xiaomi tier.
- **Frigate `genai` → Ollama** (granite3.2-vision by default) for crop-and-zoom
  enrichment; Minder reads `event.data.description`.
- **The `detect` YOLO MCP server is deprecated for monitoring** but may be kept
  for interactive "what does this camera see right now" on the snapshot tier
  (or that query is answered from Frigate's latest detections).

## Open questions / risks

1. **Zones from NL.** A zone is a pixel polygon; can't be derived from words.
   Start whole-frame-per-camera (still gets tracking + motion-gating + dedup);
   refine zones later via Frigate's zone editor.
2. **MQTT vs poll.** MQTT push needs a Mosquitto sidecar. Start by polling
   Frigate `/api/events` (reuses the existing 10s alert cadence); add MQTT only
   if latency demands it.
3. **VRAM contention.** Frigate's `genai` VLM competes with `llama3.2:3b` on a
   shared 4GB box. Fine on the friend's dedicated appliance (VLM owns the GPU).
   On a shared dev box, use `granite3.2-vision` (2.3GB, non-thinking) and accept
   eviction; on a dedicated appliance use `qwen2.5vl:3b`/`:7b` for richer text.
   **Avoid `qwen3-vl*`** — thinking models that spend the token budget on
   `<think>` and return empty descriptions to genai (verified). Detection
   (safety-critical) never touches the GPU contention.
4. **Xiaomi cloud dependency.** The Xiaomi's detection round-trips Xiaomi's
   cloud — acceptable for the secondary tier, not for security-critical points.
5. **Migration safety.** Frigate must run *alongside* the working YOLO path
   before the cutover, so a single PR never leaves Minder without alerting.

## Test plan

- Frigate sidecar boots and ingests the CP PLUS RTSP streams (config generated
  from `cameras.json`); `/api/events` returns person/vehicle events on motion.
- `frigate` MCP `get_events` returns normalized events; `get_event_snapshot`
  returns the JPEG.
- The event poller writes a Frigate-sourced alert into `pending_alerts.json` and
  it reaches Telegram via the existing loop.
- An HA `binary_sensor` motion event for the Xiaomi tier produces a normalized
  event through the same path.
- A `minder-scenario`-authored rule ("person at the gate after 9pm") matches a
  Frigate event and fires exactly one alert (dedup via track, not per-frame).
- `genai` enrichment: an event carries a `description`; its absence still fires
  the base alert (degradation test).

## Demo plan

End-to-end: walk past a CP PLUS camera → Frigate detects + tracks + enriches →
Minder normalizes the event → matches a gate scenario → Telegram alert with
snapshot, clip, and VLM description. Then trigger the Xiaomi (HA motion) → same
unified alert path, lower fidelity. Show that killing Ollama still delivers the
base alert (degradation).

## Phased implementation

Sized so every phase ships independently and never leaves Minder without
working alerting.

- **Phase 0 — Frigate sidecar, read-only.** Add the `frigate` service +
  generated config for the CP PLUS cameras, `genai` off. Verify ingestion +
  `/api/events` by hand. No Minder code change. *(Frigate runs alongside the
  existing YOLO loop.)*
- **Phase 1 — `frigate` MCP server + camera tiering.** `configure_cameras`,
  `get_events`, `get_event_snapshot/clip`; add `tier`/`ha_entity` to
  `cameras.json` and classify at discovery.
- **Phase 2 — Unified event poller.** New poller reads Frigate events (cursor)
  → normalized stream → existing alert sink. Run in parallel with
  `run_monitoring_rules`, compare, then **retire the YOLO loop + cron trigger.**
- **Phase 3 — HA snapshot tier.** Read cloud-locked cameras' (Xiaomi via Mi
  Home) on-device motion/person `binary_sensor` via HA into the same normalized
  stream. The poller folds `_fetch_ha_events` in beside Frigate events; media
  comes from HA `camera_proxy`. **Going live needs the camera in HA first:**
  install the Xiaomi integration in Home Assistant and add the camera with the
  Mi account (partly manual — Mi credentials), then call `register_ha_camera(
  name, motion_entity, camera_entity)` to wire it onto the tier. Motion is
  treated as a person signal for this secondary, lower-fidelity tier.
- **Phase 4 — genai enrichment.** Turn on Frigate `genai` → Ollama (granite3.2-vision);
  surface `description` in alerts; verify graceful degradation.
- **Phase 5 — scenario compilation polish.** Map authored scenarios to Frigate
  filters where possible; (optional) deploy reflex rules as HA automations
  tracked by Minder.

Each phase is its own PR with tests and a demo transcript.
