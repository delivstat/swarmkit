# Minder Discord QR Invite (Design Note)

**Scope:** `examples/minder` — give Discord the same QR-based "add to Minder"
onboarding Telegram already has.
**References:** the Telegram QR flow (`/api/qrcode`, the `t.me/<bot>?startgroup=`
deep link in `index.html`); the multi-channel adapters ([[project_minder_channels]]).
**Status:** proposal → implemented.

## Goal

Today Telegram setup is: scan a QR to open BotFather (create bot) → paste token →
scan a second QR (`t.me/<bot>?startgroup=minder`) that opens the Telegram app and
**adds the bot to a group in one tap**. Discord setup, by contrast, makes the user
hand-build an OAuth2 invite URL in the Developer Portal's URL Generator — the
fiddliest step. Replace that with a QR, matching the Telegram experience.

## What actually maps

The Telegram QR's value is the **deep link** it encodes, not the QR. Two links:

| Telegram | Discord analog |
| --- | --- |
| `t.me/BotFather?start=start` (create the bot) | **No analog** — Discord bot creation is a desktop Developer-Portal task; there is no mobile BotFather. The "paste token" step stays. |
| `t.me/<bot>?startgroup=minder` (add to group) | `https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot&permissions=<perms>` — opens the Discord app's **Add to Server** picker. **This is what we QR.** |

So we automate the *add-to-server* step (the painful one), and keep token paste.

## The key enabler

The OAuth invite URL needs the application/client_id. We don't ask the user for it:
a Discord bot token's identity is fetchable via `GET https://discord.com/api/v10/users/@me`
with `Authorization: Bot <token>`, and **the returned bot user `id` equals the
application id**. So verifying the token (the Discord equivalent of Telegram's
`getMe`) yields the client_id for free — same shape as `_verify_telegram_token`.

## Permissions

The invite requests exactly what Minder needs (bitfield `117760`):
View Channel (`1024`) + Send Messages (`2048`) + Embed Links (`16384`) +
Attach Files (`32768`) + Read Message History (`65536`). Message Content Intent
is a gateway intent set in the portal (step 2), not an OAuth permission.

## Changes

- **`app.py`**: `_verify_discord_token(token)` (→ `users/@me`); a
  `DISCORD_BOT_PERMS` constant + `_discord_invite_url(client_id)` helper; in
  `configure_channel`, verify the discord token up front (like telegram), store
  `config["discord_bot"]`, return `bot` + `invite_url`; `list_channels` surfaces
  `invite_url` for a configured Discord channel.
- **`index.html`**: `connectDiscord` renders the add-to-server QR + link on success;
  onboarding step-3 text drops the manual URL-Generator instructions; Settings →
  Channels shows the invite QR for Discord (mirrors the Telegram group QR card).

## Test plan

- Unit (standalone): `_verify_discord_token` parses `users/@me` (mock urlopen),
  raises on a bad token; `_discord_invite_url` builds the URL with the client_id and
  the exact permissions integer + `scope=bot`.
- Live: paste a real Discord bot token in onboarding → QR renders → scan on phone →
  Discord "Add to Server" → bot joins → @mention it → reply + alerts flow.

## Demo plan

Onboarding → Messaging → Discord tab: paste token → "Discord Connected" + an
**Add Minder to your server** QR. Scan it on a phone, pick a server, the bot
appears in the member list; @mention it and it replies.
