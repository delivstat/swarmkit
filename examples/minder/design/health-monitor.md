# Minder Active Health Monitor (Design Note)

**Scope:** `examples/minder` (a periodic health checker + status reporting +
transition alerts, on top of today's supervisor + `health()`/doctor).
**Design references:** the supervisor entrypoint (service-supervision fix); the
existing `minder_ops.health()` / `diagnose_and_alert()` (file + HA/Frigate); the
MQTT alert bus + per-channel status ([[project_minder_channels]]).
**Status:** proposal. First of the agreed next-three (health monitor → Scenario
Studio → Baileys).

## Goal

Know — and surface — the difference between the three failure modes Minder can be
in, and act appropriately for each:

1. **Process down** (crashed) — already handled: the entrypoint `supervise` loop
   restarts it. The monitor just *reports* it happened.
2. **Process up but not working** (hung, or its dependency is unreachable) — a
   restart-loop can't see this. The monitor must detect it and **report** (and,
   for a clearly-hung *core* process, optionally restart — see policy).
3. **A dependency is down/blocked** (Ollama, HA, Frigate, broker, or a *channel*
   like Telegram blocked) — can't be restarted from here; **report** it.

And the kicker that motivates a dashboard surface: a *"Telegram is blocked"*
warning **can't be delivered over Telegram** — so health status must live on the
**dashboard** (and alert via whatever channel is up).

## Non-goals

- Not replacing the supervisor (crash-restart stays the recovery for #1).
- Not a full APM/metrics system — liveness + reachability, not latency graphs.
- Not auto-restarting *external* dependencies (Ollama/HA/Frigate/mosquitto are
  separate containers; their restart is host-level, out of scope here).

## What it monitors

| Component | Check | Down means |
| --- | --- | --- |
| swarmkit serve | TCP/HTTP :8321 responds | router/topologies fail |
| webapp/ops API | :80 `/api/ops/health` responds | the brain is unreachable |
| mosquitto | TCP :1883 | no alert fan-out |
| Ollama | HTTP :11434 (+ a model loaded?) | no LLM/VLM — chat + descriptions fail |
| Home Assistant | :8123 reachable + token valid | no device control / weather |
| Frigate | `/api/version` (already in `health()`) | no perception |
| Telegram adapter | process alive + MQTT-connected + Telegram reachable (getMe) | no Telegram (e.g. India block) |
| Discord adapter | process alive + MQTT-connected + gateway connected | no Discord |
| Frigate poll loop | last successful poll within N×interval | monitoring stalled |
| Precious files | existing `health()` file checks | data fault (human /repair) |

Each component reports a **state**: `ok` / `degraded` (up but dependency
unreachable / not-configured) / `down` (process gone / unreachable), with a short
`detail` and `last_checked`.

## Probe model

A lightweight **background checker** in the webapp (started at startup like
`frigate_poller` / `mqtt_listener`), running every ~30–60 s:
- cheap TCP/HTTP probes + process liveness (walk `/proc` for the adapter procs,
  as the bot-debug did) + the existing file/Frigate checks.
- writes a health snapshot to `/data/health.json` and exposes it via the existing
  **`GET /api/ops/health`** (extended with a `components` array).
- transitions (ok→down, down→ok) drive alerts (below), with hysteresis (N
  consecutive failures before "down") so a single blip doesn't alarm.

## Recovery policy (the decision to confirm)

- **Process down →** supervisor restarts (no monitor action).
- **Dependency / channel down →** **report only** (external; can't fix from here).
- **Core process hung** (port unresponsive for N checks though the process is
  alive) → two options:
  - **A. Report-only (recommended v1):** surface "degraded", let the human act.
    Safest — a false-positive restart of a busy-but-alive service (e.g. swarmkit
    mid-long-inference) would be disruptive.
  - **B. Auto-restart** the hung core process (kill → supervisor restarts), with a
    **rate cap** (≤1 restart / 5 min) so it can't thrash.
  Recommend **A** to start (report), add **B** behind a flag once we trust the
  hung-detection thresholds. This is the main open question.

## Reporting + alerting

- **Dashboard** is the always-on surface (a "Health" panel — extend the existing
  Settings → Health & Recovery card, or an Overview status strip): each component
  with a state dot + detail + last-checked. This is where a "Telegram blocked"
  warning shows, since it can't go over Telegram.
- **Alert on transition:** when a component goes `down`/`degraded`, publish a
  Minder alert to the MQTT bus — it fans out to whatever channels are up
  (a blocked Telegram won't deliver, but Discord/dashboard will). Recovery
  (`down→ok`) sends an "back up" note. Rate-limited per component.
- Folds in the existing `diagnose_and_alert()` (file health → human /repair).

## API shape

```
GET /api/ops/health   ->  {
  healthy: bool,
  components: [ {id, kind, state: ok|degraded|down, detail, last_checked} ],
  files: {...}            # existing
}
```
(Extends today's `health()`; back-compatible — existing keys stay.)

## Implementation sketch

- `webapp/health_monitor.py`: the periodic checker (background task started in
  app startup), the probes, snapshot writer, transition→alert logic.
- Extend `minder_ops.health()` to merge the live component snapshot.
- Dashboard: a Health panel rendering `components` (mirrors the channels card
  pattern).
- Reuse `_alert_sink`/MQTT for transition alerts (so they fan out to channels +
  show in events).

## Test plan

- Unit (standalone, like `test_*.py`): probe state mapping (reachable→ok,
  refused→down, dependency-missing→degraded); hysteresis (N-fail before down);
  transition detection (ok→down→ok) fires exactly one alert each way; rate-limit.
- Live: stop mosquitto → health shows broker `down` + (if policy B) no restart of
  externals; block a port → `degraded`; confirm dashboard reflects it and a
  transition alert publishes to the bus.

## Demo plan

- Dashboard Health panel green across components; kill the Telegram adapter →
  supervisor restarts it, panel blips then recovers; simulate Telegram blocked
  (it already is in India) → Telegram channel shows `down/blocked` on the
  dashboard while Discord stays `ok`.

## Open questions

1. **Hung-core-process policy: report-only (A) vs auto-restart-with-cap (B).**
   Recommend A first; B behind a flag.
2. **Probe cadence** (30 s vs 60 s) and **hysteresis N** — tune to avoid alarm
   on transient blips (e.g. Ollama busy loading a model).
3. **Adapter "connected" signal** — process-alive is easy; "actually connected to
   the platform" needs the adapter to heartbeat (write last-ok to a file / a
   retained MQTT status topic). Start with process + MQTT-connected; add
   platform-reachability (getMe) as a second signal.
