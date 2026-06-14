# Minder Frigate MQTT Event Path — Design Note

**Scope:** `examples/minder` (mosquitto broker, Frigate config, frigate MCP server, webapp)
**Design reference:** the "camera event path tightening" addendum in
`vision-architecture.md` (item 1).
**Status:** draft

## Goal

Make camera alerts **real-time** instead of up-to-60s late. Frigate already
detects continuously, but Minder polls `/api/events` once a minute (cron can't go
sub-minute). Subscribe to Frigate's **MQTT** event stream so the rule engine
reacts the instant Frigate fires.

## Non-goals

- Removing the minute poller — it stays as a reconcile/backstop (catches events
  missed while MQTT was disconnected).
- Changing the rule-matching semantics or the alert shape.
- Rule-derived `objects.track` / post-match VLM (the other two addendum items —
  separate follow-ups).

## Architecture

```
Frigate (continuous detection) --MQTT--> mosquitto (frigate/events)
                                              │
                                  webapp MQTT subscriber (always-on)
                                              │  on new/update event
                                              ▼
                          frigate.handle_live_event(after)
                          (shared match + fire + dedup/cooldown state)
                                              ▼
                                     write_alert  ──>  bot poll (10s) -> Telegram

(minute cron poll_events stays as the reconcile backstop — same dedup state)
```

- **Broker:** a `mosquitto` container, `network_mode: host` (consistent with the
  stack; Frigate + Minder address it as `localhost:1883`). Anonymous, no
  persistence.
- **Frigate:** `_build_config` sets `mqtt: {enabled, host: localhost, port: 1883}`
  so Frigate publishes tracked-object events to `frigate/events`.
- **Subscriber:** a long-lived task in the **webapp** (FastAPI is always-on),
  `paho-mqtt`. On each `frigate/events` message of type `new`/`update`, it calls
  the shared `frigate.handle_live_event(after)`.
- **Shared logic, no duplication:** the per-event match+fire is extracted into
  `_match_and_fire_event(ev, active, state, now, live)`, used by BOTH the MQTT
  path and the cron poller. Both share `monitor_state` (`_frigate_seen` dedup +
  per-`camera|condition` cooldown), so MQTT and the backstop poll never
  double-fire the same event.

## Latency

Event → match → `write_alert` is now instant. Final delivery is the Telegram
bot's existing alert poll (~10s) — so ~10s end-to-end vs ≤60s. (Reducing the bot
poll interval is a separate lever.)

## API shape

```
examples/minder/mosquitto/mosquitto.conf            # listener 1883, allow_anonymous
docker-compose.yml: mosquitto service (host net) + MINDER_MQTT/MQTT_HOST/PORT env + depends_on
Dockerfile: pip install paho-mqtt
mcp-servers/frigate/server.py:
    _build_config -> mqtt enabled (localhost:1883)
    _normalize_mqtt(after, slug2name)        # MQTT 'after' -> normalized ev (current_zones->zones)
    _match_and_fire_event(ev, active, state, now, live) -> dict|None   # shared inner loop
    handle_live_event(after) -> dict|None     # load rules+state, normalize, dedup, match, fire, save
    poll_events()                             # refactored to use _match_and_fire_event
webapp/mqtt_listener.py: start_mqtt_listener()  # paho-mqtt, frigate/events -> handle_live_event
webapp/app.py: @startup -> start_mqtt_listener()  (gated on MINDER_MQTT)
```

## Test plan

- Unit: `_normalize_mqtt` maps `current_zones`→`zones`, label/id/camera; a synthetic
  `after` for a person at a rule's camera → `_match_and_fire_event` fires once,
  re-firing the same event_id is deduped.
- Integration (live): broker up; **publish a synthetic `frigate/events` message**
  for a `person` at a camera with a matching rule → assert an alert is written;
  republish same id → no second alert (dedup). Confirm Frigate connects to the
  broker (`mosquitto` logs / `frigate/available`).
- Backstop: stop the subscriber, let the cron poll fire the same class of event;
  confirm no double-fire when both run.

## Demo plan

Terminal: `mosquitto_pub -t frigate/events -m '<synthetic person@gate>'` →
alert appears in `/api/ops/alerts` within ~1s. Plus a live walk-past showing the
alert arriving in ~seconds instead of up to a minute.

## Addendum — media when ready (fix)

The MQTT event fires on `type:"new"`, **before** Frigate has written the event
snapshot (`has_snapshot=false` at that instant), and the recorded **clip only
exists once the event ends**. So `_normalize` produced empty `snapshot_ref` /
`clip_ref` and the alert went out with no media — the regression vs the poller
path, which only ever sees `has_snapshot=1`-filtered (already-ready) events.

Fix: **decouple the text alert from the media.** `_fire` writes the text alert
instantly, then hands off to `_deliver_event_media(event_id, cam)` — a daemon
thread (never blocks the alert path) that:

1. polls `get_event_snapshot` until it returns a path or `SNAPSHOT_WAIT_S` (20s);
   on success sends the boxed photo as a **media-only follow-up** (empty message,
   so the bot skips a duplicate 🚨 bubble);
2. runs the VLM description on that snapshot → a `📷 cam: …` follow-up (warm
   ~9s; the 90s timeout returns "" gracefully — first event after a restart gets
   no description, snapshot + clip still arrive);
3. if recording is on, polls `get_event_clip` until ready or `CLIP_WAIT_S` (90s)
   → sends the clip.

This unifies the #318 (describe) and #319 (clip) enrichments under one "wait for
the media, then send it" path; both needed the not-yet-ready snapshot. The
HA-snapshot tier (`_deliver_ha_media`) pulls a live frame synchronously (no clip)
and is otherwise identical. The poller path is unchanged in shape — its events
already have media, so the follow-ups arrive immediately.

The Telegram message sequence per event: **text alert → photo → description →
clip**, each as it materialises.

## Open questions

- ~~Concurrent writes to the alert queue~~ **(resolved)**: the per-event media
  threads, the webapp MQTT subscriber, and the cron poller all call `write_alert`,
  so the read-modify-write of `pending_alerts.json` / `events.json` is now guarded
  by an `flock` over a sidecar lock file (`_alert_sink._alert_lock`), atomic across
  threads and processes. `monitor_state.json` (cooldown deltas) is still bare
  atomic-write — low risk (10min cooldown); file-lock it too if it proves flaky.
- Auth: broker is anonymous on the host. Fine for a single-box appliance; add
  credentials if the broker is ever exposed.
