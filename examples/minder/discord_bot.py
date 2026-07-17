"""Minder Discord Bot — thin channel adapter (mirrors bot.py).

Same contract as the Telegram adapter: ALL logic lives in the Minder ops API
(webapp/minder_ops.py). This adapter only:
  1. Handles Discord specifics (channel registration, mention filtering)
  2. Forwards addressed text to POST /api/ops/message
  3. Renders the structured envelope (text + media) for Discord
  4. Subscribes to the minder/alerts MQTT topic and posts alerts to the channel

Runs only if MINDER_DISCORD_TOKEN is set (entrypoint supervises it). Telegram,
Discord, and any future adapter run side by side against the same ops API; the
MQTT topic fans alerts out to each one (durable per-subscriber sessions).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import discord
import httpx

logging.basicConfig(format="%(asctime)s [%(name)s] %(message)s", level=logging.INFO)
log = logging.getLogger("minder-discord")

API_URL = os.environ.get("MINDER_API_URL", "http://localhost:80")
DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
CHANNEL_FILE = DATA_DIR / "channels" / "discord.json"
INTERNAL_TOKEN_FILE = DATA_DIR / "internal_token"
MAX_LEN = 2000  # Discord message limit


def _internal_headers() -> dict[str, str]:
    token = INTERNAL_TOKEN_FILE.read_text().strip() if INTERNAL_TOKEN_FILE.exists() else ""
    return {"X-Minder-Internal": token}


async def ops_post(path: str, payload: dict, timeout: float = 200.0) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=timeout)) as client:
        resp = await client.post(f"{API_URL}{path}", json=payload, headers=_internal_headers())
        resp.raise_for_status()
        return resp.json()


def _load_channel() -> dict[str, Any]:
    if CHANNEL_FILE.exists():
        try:
            return json.loads(CHANNEL_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _save_channel(channel_id: int, name: str) -> None:
    CHANNEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHANNEL_FILE.write_text(json.dumps({"channel_id": channel_id, "name": name}, indent=2))
    log.info("Discord channel registered: %s (%s)", name, channel_id)


def _split(text: str) -> list[str]:
    out, rem = [], text
    while rem:
        if len(rem) <= MAX_LEN:
            out.append(rem)
            break
        cut = rem.rfind("\n", 0, MAX_LEN)
        cut = cut if cut > 0 else MAX_LEN
        out.append(rem[:cut])
        rem = rem[cut:]
    return out


async def _render(channel: discord.abc.Messageable, result: dict) -> None:
    """Render an ops envelope: text chunks + media files (same envelope the
    Telegram adapter renders)."""
    text = (result.get("text") or "").strip()
    for chunk in _split(text):
        if chunk.strip():
            await channel.send(chunk)
    for item in result.get("media", []):
        path = item.get("path") or ""
        if path and Path(path).exists():
            await channel.send(file=discord.File(path))


intents = discord.Intents.default()
intents.message_content = True  # required to read message text
client = discord.Client(intents=intents)


@client.event
async def on_ready() -> None:
    log.info("Minder Discord adapter connected as %s (ops API: %s)", client.user, API_URL)
    # Alerts arrive over MQTT (durable per-subscriber session), not polling.
    try:
        from alert_bus import start_alert_subscriber

        start_alert_subscriber("minder-discord", client.loop, _deliver_alert)
        log.info("Alert subscriber started (MQTT minder/alerts)")
    except Exception as e:
        log.warning("alert subscriber failed to start: %s", e)


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot or message.author == client.user:
        return
    is_dm = message.guild is None
    mentioned = client.user in message.mentions
    # Respond to DMs, or to @mentions / replies in a server channel. First channel
    # we're addressed in becomes the registered alert channel (like the TG group).
    if not is_dm and not mentioned:
        return
    text = message.content
    if client.user:
        text = text.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "")
    text = text.strip()
    if not text:
        return
    if not is_dm and not _load_channel().get("channel_id"):
        _save_channel(message.channel.id, getattr(message.channel, "name", "channel"))
    sender = getattr(message.author, "display_name", "")
    log.info("%s: %s", sender, text[:100])
    async with message.channel.typing():
        try:
            result = await ops_post(
                "/api/ops/message", {"text": text, "source": "discord", "sender": sender}
            )
        except Exception as e:
            await message.channel.send(f"⚠️ {e}")
            return
    await _render(message.channel, result)


async def _deliver_alert(alert: dict) -> None:
    """Post one alert (from the MQTT bus) to the registered Discord channel."""
    chan_id = _load_channel().get("channel_id")
    if not chan_id:
        return
    channel = client.get_channel(chan_id)
    if channel is None:
        return
    try:
        msg = alert.get("message")
        if msg:
            await channel.send(f"🚨 {msg}")
        # Guard against empty paths: Path("") == Path(".") and Path(".").exists()
        # is True, so an empty snapshot_path/video_path would try to upload the cwd
        # (raising) — and on a clip-only alert that raise happened BEFORE the clip
        # was sent, so clips never arrived. Skip empty strings explicitly.
        for key in ("snapshot_path", "video_path"):
            path = alert.get(key) or ""
            if path and Path(path).exists():
                await channel.send(file=discord.File(path))
    except Exception as e:
        log.error("alert delivery error: %s", e)


def main() -> None:
    import threading

    from alert_bus import wait_for_token, watch_token

    # Idle until a token is configured (dashboard or env) — no restart to enable.
    token = wait_for_token("discord")
    # ...and exit (to idle) if the channel is later disabled/replaced — no restart
    # to disable, symmetric with enabling.
    threading.Thread(target=watch_token, args=("discord", token), daemon=True).start()
    log.info("Minder Discord bot starting as thin adapter...")
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
