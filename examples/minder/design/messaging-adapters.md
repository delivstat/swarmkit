# Minder Messaging Adapters — Multi-Channel (Design Note)

**Scope:** `examples/minder` (channel adapters + the ops-API contract + provider
config in onboarding/settings).
**Design references:** the channel-neutral ops API (`bot.py` docstring;
[[project_minder_state]]); the supervisor entrypoint (service-supervision fix).
**Status:** proposal. Phase 2 (Discord adapter) is being implemented alongside
this note; the rest is roadmap.

## Goal

Let Minder talk to families over **whatever channel they actually use** —
Telegram, Discord, WhatsApp, and (later) a native Minder app — chosen in
onboarding/settings, all running at once if desired. So a single blocked channel
(India temporarily blocked **Telegram** — the trigger for this) never takes the
system down: the others keep working, and the blocked one auto-resumes.

This is cheap because Minder already separates **brain from channel**: the ops
API (`POST /api/ops/message` → envelope `{kind,text,media,data}`, `GET /api/ops/alerts`)
is channel-neutral, and each channel is a **thin adapter** (~250 lines) that does
*receive → POST ops/message → render envelope* and *poll alerts → post to channel*.
Adding a channel adds an adapter; the brain doesn't change.

## Non-goals

- Not changing the ops API's semantics — adapters stay thin, logic stays central.
- Not building the native mobile app here (it's named as a future adapter).
- WhatsApp official Business API (templates/verification) is out — see the
  Baileys-sidecar approach below.

## Principles

- **One brain, many mouths.** Every adapter is interchangeable and additive.
- **Token/config-gated + supervised.** An adapter runs iff its provider is
  enabled + credentialed; the entrypoint supervisor restarts it on crash.
- **Resilience by plurality.** Channels fail independently (a block, an outage); a
  family on two channels is never fully dark.

## Architecture

```
                 family on any channel(s)
   Telegram        Discord         WhatsApp         (future) Minder app
      │               │               │                     │
  bot.py        discord_bot.py   wa-sidecar (Node)      app adapter
      │ two-way        │               │                     │
      ├── POST /api/ops/message ───────┴─────────────────────┤  (request/response)
      │                                                       │
      └── SUBSCRIBE minder/alerts (mosquitto, durable) ───────┘  (push, fan-out)
                              ▲
        write_alert ─ PUBLISH minder/alerts ─┘   (producer: frigate/camera/webapp)
                              │
                    ops API (minder_ops) — CHANNEL-NEUTRAL BRAIN
                    router → deterministic execution → envelope
```

Each adapter does two things:
- **Two-way chat (request/response):** receive a message → strip channel cruft →
  `POST /api/ops/message {text, source, sender}` → render the returned envelope
  (text + media) in that channel's idioms. (Unchanged; per-channel, synchronous.)
- **Alerts (push, fan-out):** **subscribe** to the MQTT topic `minder/alerts` and
  post each one (text + snapshot + clip). No polling, no per-channel bookkeeping.
- registers its own target (the group/channel/chat to talk in) under
  `/data/channels/<id>.json` (generalises today's `telegram_group.json`).

The entrypoint supervises one process per enabled adapter (the `supervise` helper
already added), each gated on its credential.

## Alert fan-out via MQTT durable per-subscriber queues

The original draft of this note used per-channel delivery tracking on a polled
`pending_alerts.json`. **Replaced** with MQTT pub/sub — we already run mosquitto
(Frigate uses it), and pub/sub *is* fan-out, so there's no app-side bookkeeping.

The robust pattern is **topic → a durable queue per subscriber**: publish once;
the broker copies into each subscriber's durable queue; subscribers consume their
own queue; because the queues are durable, a subscriber that's **down loses
nothing** — it drains its backlog on reconnect. MQTT realises this with
**persistent sessions** (no separate queue object, same effect):

- **Topic:** `minder/alerts`. `write_alert` (the existing producer in
  `_alert_sink`, run by the frigate/camera servers + webapp) **publishes** the
  alert JSON there at **QoS 1**. It keeps writing `events.json` (the durable
  dashboard record — see durability below).
- **Each adapter = a persistent-session subscriber:** fixed `client_id`
  (`minder-telegram`, `minder-discord`, …), `clean_session=False`, subscribe
  `minder/alerts` QoS 1. The broker keeps that client's **offline queue** and
  redelivers on reconnect — the durable-queue-per-consumer the family pattern
  describes.
- **mosquitto `persistence=true`** (+ a persist volume) so those session queues
  survive a *broker* restart too, and `max_queued_messages` bounds a down
  subscriber's backlog. (Was `persistence false`.)
- A new adapter just connects with its own `client_id` and subscribes — nothing
  else changes. No `?channel=` endpoint, no `delivered` tracking.

**Why MQTT, not RabbitMQ:** RabbitMQ is the textbook topic-exchange→durable-queues
fit, but it's a heavyweight second broker (Erlang/RAM) on the edge box. mosquitto
is already here, lightweight, and persistent sessions give the same
durable-fan-out semantics for this volume. Don't add a broker.

### Durability (it's a security system)
- **Live delivery + offline catch-up:** QoS 1 persistent sessions — a briefly-down
  adapter (the supervisor restarts it in ~5 s) drains its queue on reconnect.
- **Durable record regardless of MQTT:** `events.json` still logs every alert, and
  the dashboard shows it — so even a "broker down at the exact publish instant"
  alert is visible there. mosquitto persistence narrows that window further.
- `write_alert` stays flock-guarded for `events.json`; the MQTT publish is
  additive and graceful (a broker hiccup never fails the alert write).

## Provider config + selection (onboarding + settings)

Config in `minder-config.json`:

```json
{ "channels": {
    "telegram": { "enabled": true,  "token": "..." },
    "discord":  { "enabled": false, "token": "...", "guild_id": null },
    "whatsapp": { "enabled": false, "number": "...", "paired": false }
} }
```

- **Onboarding** "Messaging" step (replaces the Telegram-only step): provider
  **cards** (Telegram / Discord / WhatsApp), each with its own setup — token entry,
  the relevant QR/deep-link, and channel registration. The user enables one or
  more; "you can add more later in Settings."
- **Settings → Channels** tab: list providers with **status** (connected / not
  configured / **blocked — Telegram unreachable**), enable/disable, reconfigure,
  re-pair. Status comes from the health monitor (below).
- Enabling a provider writes its config + token (and `.env`) and (re)starts its
  adapter via the supervisor.

## Channel specs

### Discord (Phase 2 — building now)
- `discord_bot.py`, a direct mirror of `bot.py`: `discord.py` client, bot token
  (`MINDER_DISCORD_TOKEN` / config), a designated guild+channel as the "group".
- Responds to mentions / slash-or-prefix commands / replies; renders the envelope
  (text + photo + video); polls `/api/ops/alerts?channel=discord` and posts.
- Setup: create a bot in the Discord developer portal, invite to the family
  server, pick the channel. No business verification, free, not blocked in India.
- Closest to Telegram in ease → lowest-lift true two-way replacement.

### WhatsApp (Phase 4 — scoped, not built)
WhatsApp's **official** Cloud API needs a Meta business account, a dedicated
number, and **pre-approved templates for proactive messages** — which fights
Minder's core (proactive alerts). So the pragmatic path is an **unofficial
sidecar**:
- A small **Node sidecar container** using **Baileys** (multi-device WhatsApp
  protocol; no headless browser). A **dedicated/spare number** is linked as a
  device (QR scan, like WhatsApp Web). The sidecar exposes a tiny local HTTP
  bridge; a thin Minder adapter speaks to it like any other channel (→ ops API).
- **Honest caveats (must be shown to the user):** it violates WhatsApp's ToS;
  numbers can be **banned** (use a dedicated number, keep to real alert volume);
  it's **fragile** (WhatsApp updates break libs; sessions need re-pairing). So
  WhatsApp is a **best-effort secondary** channel — never the sole alert path for
  a security system.
- Fits the architecture cleanly (sidecar + thin adapter); the risk is operational,
  not structural.

### Native Minder app (Phase 5 — future)
A Minder-native mobile app is **just another adapter** against the same ops API —
and the best one: not subject to third-party blocks, push notifications under our
control, and a rich UI (live view, device control, rule editing) instead of a chat
bubble. It would `POST /api/ops/message` for two-way and receive alerts via push.
Nothing in the brain changes — it validates the whole thin-adapter design.

## Ties to the health monitor

Per-channel **status** is what the earlier health-monitor idea should report:
- adapter **process down** → supervisor restarts (done);
- adapter **up but channel unreachable** (Telegram blocked, WhatsApp unpaired,
  Discord token bad) → **report it on the dashboard/Settings**, don't thrash —
  and note the obvious: a "Telegram is blocked" warning **can't be sent over
  Telegram**, so the dashboard (and any *other* live channel) is where it surfaces.

## API shape

```
POST /api/ops/message                     (unchanged) two-way: adapter → brain
MQTT minder/alerts (publish/subscribe)    alert fan-out (NOT an HTTP endpoint)
GET/POST /api/ops/channels                list / enable / configure providers
POST /api/ops/channels/<id>/register      set the target group/channel/chat
GET  /api/ops/channels/<id>/status        connected | blocked | unconfigured
```

`GET /api/ops/alerts` (the old poll endpoint) is retired in favour of the MQTT
topic.

## Phased plan (each its own PR)

1. **Alert fan-out over MQTT** — `write_alert` publishes `minder/alerts` (QoS 1);
   adapters subscribe with persistent sessions; mosquitto `persistence=true` +
   persist volume; retire the `/api/ops/alerts` poll + `pending_alerts.json`
   fan-out. Telegram bot switched to subscribe. *(Building now.)*
2. **Discord adapter + supervision** — `discord_bot.py` (subscribes `minder/alerts`
   as `minder-discord`), entrypoint supervises it when `MINDER_DISCORD_TOKEN` set.
   *(Building now, with phase 1.)*
3. **Provider selection UI** — onboarding Messaging step + Settings → Channels
   (enable/configure/status).
4. **WhatsApp Baileys sidecar** — Node sidecar + thin adapter + the honest
   risk-acknowledgement gate.
5. **Native app adapter** — when the mobile app exists.

Phases 1+2 ship Discord alongside Telegram (the immediate need); 3 makes it
self-service; 4 adds WhatsApp; 5 is the future.

## Test plan

- **Fan-out:** unit tests — two channels each get every alert exactly once;
  prune-when-all-delivered; TTL; legacy no-param path unchanged. (standalone, like
  `test_*.py`.)
- **Discord adapter:** mock the Discord client + ops API; assert message→ops→render
  and alert→post; resilience like `test_bot_resilience.py`. Live test needs a bot
  token (user-provided).
- **Config:** enabling a provider writes config + starts the adapter; status
  reflects reachability.

## Demo plan

- Run Telegram **and** Discord together: trigger a person alert → it arrives on
  **both** (fan-out), and a message in either gets the same answer.
- During the Telegram block: Discord keeps working; Settings shows Telegram
  "blocked"; when the block lifts, Telegram auto-resumes — no restart.

## Open questions

1. **Primary channel for two-way.** Alerts fan out to all; but if a user messages
   from channel A, the reply goes to A (already true — `source` in the envelope).
   No "primary" needed; confirm.
2. **Alert TTL / queue cap** when a channel is enabled-but-down for a long time
   (blocked Telegram) — prune by count + age so it can't grow unbounded.
3. **WhatsApp ToS gate** — enabling WhatsApp must require an explicit
   acknowledgement of the ban/fragility risk in the UI.
4. **Per-channel media limits** (size/format differ by platform) — adapters clamp.
