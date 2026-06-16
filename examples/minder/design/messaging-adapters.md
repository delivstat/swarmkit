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
      └───────────────┴───────┬───────┴─────────────────────┘
                              │  receive → POST /api/ops/message
                              │  poll    → GET  /api/ops/alerts?channel=X
                              ▼
                    ops API (minder_ops) — CHANNEL-NEUTRAL BRAIN
                    router → deterministic execution → envelope
```

Each adapter:
- receives a message → strips channel cruft → `POST /api/ops/message {text, source, sender}`
  → renders the returned envelope (text + media) in that channel's idioms.
- delivers alerts → `GET /api/ops/alerts?channel=<id>` → posts text + snapshot + clip.
- registers its own target (the group/channel/chat to talk in) under
  `/data/channels/<id>.json` (generalises today's `telegram_group.json`).

The entrypoint supervises one process per enabled adapter (the `supervise` helper
already added), each gated on its credential.

## The one real backend change: alert fan-out

Today `GET /api/ops/alerts` is **read-and-clear** — fine for one adapter, but with
two adapters they'd **race**: whoever polls first clears the alert, the other
never sees it. Multi-channel needs each alert delivered to **every enabled
channel**.

Design: per-channel delivery tracking on the alert queue.
- Each `pending_alerts.json` entry gains `delivered: [channel_ids]`.
- `GET /api/ops/alerts?channel=X` returns entries where `X ∉ delivered`, then adds
  `X` to each returned entry's `delivered`.
- An entry is pruned once `delivered ⊇ enabled_channels`, or after a TTL
  (e.g. keep ≤200 entries / 1 h) so a disabled/blocked channel can't pin the queue
  forever.
- Back-compat: no `channel` param → legacy read-and-clear (until all adapters pass
  their id).

`write_alert` is unchanged (one entry); fan-out lives entirely in the read path.
This stays deterministic — no LLM, flock-guarded like today.

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
POST /api/ops/message                     (unchanged) any adapter → brain
GET  /api/ops/alerts?channel=<id>         per-channel fan-out (new param)
GET/POST /api/ops/channels                list / enable / configure providers
POST /api/ops/channels/<id>/register      set the target group/channel/chat
GET  /api/ops/channels/<id>/status        connected | blocked | unconfigured
```

## Phased plan (each its own PR)

1. **Alert fan-out + per-channel registration** — `?channel` delivery tracking,
   `/data/channels/<id>.json`; Telegram bot passes `channel=telegram`. (Backend;
   unblocks running two adapters at once.)
2. **Discord adapter + supervision** — `discord_bot.py`, entrypoint supervises it
   when `MINDER_DISCORD_TOKEN` is set. *(Building now.)*
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
