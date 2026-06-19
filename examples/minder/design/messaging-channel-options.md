# Minder Messaging Channel Options (Survey / Design Note)

**Scope:** `examples/minder` — a survey of communication platforms Minder could use
to deliver alerts (and optionally take queries), beyond today's Telegram + Discord.
**References:** the multi-channel adapter architecture (`messaging-adapters.md`,
[[project_minder_channels]]); the active health monitor (`health-monitor.md`);
the Discord QR onboarding (`discord-qr-invite.md`).
**Status:** survey for later — **no channel chosen yet**. Baileys/WhatsApp is
**paused** (see below). This note records the options + trade-offs so we can pick
when we come back to it.

## Context (what any new channel must fit)

Minder is a **self-hosted, individual appliance** — every user runs *their own*
instance for *their own* home/site, with *their own* account/number. There is **no
centralized company bot**. That single fact drives everything below: no business
onboarding, no per-message billing, "bring your own account", and onboarding that's
a **QR scan / token paste**, exactly like Telegram and Discord today.

Every channel plugs into the existing adapter pattern with no new architecture:
a **durable MQTT subscriber** on `minder/alerts` + an **ops-API client**
(`POST /api/ops/message`) + `channel_token`/`wait_for_token` config resolution +
QR/token onboarding. Adapters run side by side; the MQTT bus fans every alert to
all of them.

**Honest "local" framing:** Minder's *monitoring/decision loop* is fully local. The
*output channel* is not — Telegram, Discord, WhatsApp, Signal, email, and public
ntfy all transit a server. A **self-hosted** channel (ntfy on the box, Matrix
homeserver, local SMS) is the only way the output also stays local. We should never
imply the messaging hop is local for the cloud-backed ones.

## Why WhatsApp/Baileys is paused (the anchor decision)

The official **WhatsApp Cloud/Business API is structurally incompatible** with
Minder, not just heavier: Minder's core job is **pushing alerts**, which are
*business-initiated* messages. Meta gates those behind **pre-approved templates** +
**per-conversation billing** + a Business Manager account + a dedicated business
number + app review. A dynamic security alert ("person at the gate") can't be a
fixed template, and no homeowner will do business onboarding.

**Baileys** (unofficial WhatsApp Web multi-device library) removes all of that —
link your own account via QR/pairing-code, free, free-form messages — and is the
*correct* shape for an individual appliance. But it carries real caveats that paused
it: (1) **violates WhatsApp ToS → ban risk on the user's real number** (mitigate:
mandate a *dedicated* secondary number, never the primary); (2) **fragility** —
breaks on WhatsApp protocol changes, needs library bumps (mitigate: the health
monitor alerts over another channel when it goes dark); (3) **session expiry** —
companion devices log out (~14 days of primary offline), needs persisted auth +
a re-pair flow; (4) a **Node/TS sidecar** (our adapters are Python). Revisit when
we're ready to own those caveats; the design note for it is still to be written.

## The two strategies

Nothing matches WhatsApp's ubiquity in India, so "instead of WhatsApp" splits into:

- **A — a different chat app** people already (or will) install → full two-way
  conversation + alerts.
- **B — a dedicated push channel** → user installs one small app once, Minder just
  pushes alerts. Sidesteps the "must already have it" problem entirely. Best match
  for what Minder *is* (an alerting appliance); two-way query stays on Telegram/Discord.

## Candidate matrix

| Platform | Fit | Reach | Two-way? | Ban/ToS | Local-capable | Effort |
| --- | --- | --- | --- | --- | --- | --- |
| **ntfy** (self-hostable push) | ⭐ excellent | install 1 app | push + basic replies/actions | none | **yes** (self-host) | low — HTTP POST |
| **Signal** (signal-cli) | strong | high, growing | yes | low (more legit than Baileys) | no | medium — number + Java sidecar |
| **Email / SMTP** | good fallback | universal | one-way (IMAP poll for replies) | none | partial (own SMTP) | low |
| **Local SMS** (GSM dongle / old Android) | niche, very on-brand | universal, **works offline** | one-way | none | **yes** (LAN) | medium — hardware/SIM |
| **Matrix** (Element) | clean, open | low adoption | yes | none | **yes** (homeserver) | medium — Python `matrix-nio` |
| **Slack / Mattermost / Rocket.Chat** | workplace-shaped | low for households | yes | none | yes (self-host MM/RC) | medium |
| **Pushover** | solid push | install 1 app | push-only | none | no (closed, paid once) | low |
| **Own PWA + Web Push (VAPID)** | long-term "own channel" | install/visit our app | yes (it's our UI) | none | mostly (own server) | high |

## ntfy deep-dive (the leading candidate) — and "does it work over the internet?"

Yes; *how* depends on topology — and remote delivery matters because a security app
must alert most when you're **away** (not on home WiFi):

1. **Public ntfy.sh (easiest, works remotely out of the box).** Minder POSTs to
   `https://ntfy.sh/<secret-topic>`; the phone subscribes to the same topic. Both
   need only **outbound** internet — no port-forwarding, static IP, or DDNS. Alerts
   transit ntfy's public server, so the topic name is the secret → use a long random
   topic + an access token. Same "output touches a server" reality as Telegram/Discord.
2. **Self-hosted ntfy + mesh VPN (best "fully local + works anywhere").** Run ntfy
   on the box; put box + phone on **Tailscale/WireGuard** (free, no port-forwarding,
   encrypted). Phone reaches the appliance's ntfy from anywhere; data never leaves
   your server. A bit more setup; the privacy-clean answer.
3. **Self-hosted, LAN-only (not for security).** Works only on home WiFi — alerts
   stop the moment you leave. Wrong for the use case.

**iOS caveat:** instant *background* push on iPhone needs Apple APNs, wired to
ntfy.sh's infrastructure. So fully-self-hosted instant push on iOS needs ntfy's
"upstream forwarding" (self-host pings ntfy.sh → APNs). **Android self-hosted is
fully independent.** iPhone-heavy households nudge toward option 1.

**Why it leads:** open-source, **self-hostable** (can be truly local), free, **zero
ban/ToS risk**, images + priority + tap-actions, integrates as a trivial HTTP POST —
**no sidecar, no phone number, no library fragility**. Onboarding = "install app,
scan QR to subscribe to your topic", matching the existing pattern. Limitation:
push-first, not rich chat — fine, since Telegram/Discord cover conversational queries.

## Recommendation (for when we resume)

- **Default: ntfy via ntfy.sh** — zero-setup remote alerts, only outbound
  connectivity needed.
- **Privacy/local toggle: self-hosted ntfy + Tailscale** — surfaced as a provider
  option in settings (same "pick your provider" pattern as the other channels).
- **If a two-way WhatsApp substitute is the real need: Signal via signal-cli** —
  lower ban risk than Baileys, privacy-aligned, but needs a dedicated number + a
  Java daemon sidecar (similar shape to what Baileys would have been).
- **Email/SMTP** is the boring, universal *fallback* worth having regardless.

## Open questions (decide before building)

1. **iPhone vs Android** in the target household — decides whether self-hosted ntfy
   instant push is viable on iOS or we lead with ntfy.sh.
2. **Push-only vs two-way priority** — if alerts-only is acceptable, ntfy/email win
   and we skip a sidecar; if two-way matters, Signal/Matrix come into play.
3. **Self-host appetite** — willingness to run Tailscale (ntfy local) or a homeserver
   (Matrix), vs. accept a public server (ntfy.sh) for zero setup.
4. **Hardware appetite** — a dedicated number (Signal) or GSM dongle/old phone
   (local SMS, the only truly offline-capable option).

## Test / demo plan (per chosen channel, when built)

- Adapter unit test (durable MQTT subscriber receives `minder/alerts`, renders an
  alert envelope incl. media) — same standalone style as `test_alert_bus.py` /
  `test_channels.py`.
- Live: configure the channel via onboarding/settings → fire a test alert → confirm
  it arrives on the phone **off home WiFi** (mobile data) for the remote-path proof.
- Health-monitor coverage: kill the adapter → monitor reports it `down` over another
  channel.
