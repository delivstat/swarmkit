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

## Open questions

- `monitor_state.json` is written by both the subscriber (webapp process) and the
  cron poller (serve process) — concurrent writes can clobber a cooldown delta
  (low risk: 10min cooldown, atomic writes). File-lock if it proves flaky.
- Auth: broker is anonymous on the host. Fine for a single-box appliance; add
  credentials if the broker is ever exposed.
