"""Minder Telegram Bot — thin channel adapter.

ALL business logic lives in the Minder ops API (webapp/minder_ops.py,
served at MINDER_API_URL). This adapter only:
  1. Handles Telegram-specific concerns (group registration, mention
     filtering, privacy, stale messages)
  2. Forwards user text to POST /api/ops/message
  3. Renders the structured result envelope (text + media) for Telegram
  4. Polls GET /api/ops/alerts and posts monitoring alerts to the group

A WhatsApp (or any other) adapter is the same ~250 lines against the
same two endpoints.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("minder")

API_URL = os.environ.get("MINDER_API_URL", "http://localhost:80")
DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
GROUP_FILE = DATA_DIR / "telegram_group.json"
INTERNAL_TOKEN_FILE = DATA_DIR / "internal_token"
BOT_START_TIME = time.time()


# ---- Ops API client ----


def _internal_headers() -> dict[str, str]:
    token = ""
    if INTERNAL_TOKEN_FILE.exists():
        token = INTERNAL_TOKEN_FILE.read_text().strip()
    return {"X-Minder-Internal": token}


async def ops_post(path: str, payload: dict, timeout: float = 200.0) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=timeout)) as client:
        resp = await client.post(f"{API_URL}{path}", json=payload, headers=_internal_headers())
        resp.raise_for_status()
        return resp.json()


async def ops_get(path: str, timeout: float = 30.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{API_URL}{path}", headers=_internal_headers())
        resp.raise_for_status()
        return resp.json()


# ---- Group config (Telegram-specific) ----


def _load_group() -> dict[str, Any]:
    if GROUP_FILE.exists():
        try:
            return json.loads(GROUP_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _save_group(chat_id: int, title: str) -> None:
    GROUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    GROUP_FILE.write_text(json.dumps({"chat_id": chat_id, "title": title}, indent=2))
    log.info(f"Group registered: {title} ({chat_id})")


# ---- Rendering (envelope → Telegram) ----


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, max_len)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks


async def render_result(update: Update, thinking_msg: Any, result: dict) -> None:
    """Render an ops result envelope: delete status msg, send text + media."""
    with contextlib.suppress(Exception):
        await thinking_msg.delete()
    if not update.message:
        return

    text = result.get("text", "")
    if text.strip():
        for chunk in _split_message(text):
            await update.message.reply_text(chunk)

    for item in result.get("media", []):
        path = Path(item.get("path", ""))
        if not path.exists():
            continue
        caption = item.get("caption", "")[:1024]
        if item.get("type") == "photo":
            await update.message.reply_photo(photo=path.open("rb"), caption=caption)
        elif item.get("type") == "video":
            await update.message.reply_video(
                video=path.open("rb"), caption=caption, supports_streaming=True
            )


# ---- Handlers ----


async def message_handler(update: Update, context: Any) -> None:
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    if not chat:
        return

    # Drop stale messages queued while bot was offline
    if update.message.date.timestamp() < BOT_START_TIME - 30:
        log.info(f"Dropping stale message: {update.message.text[:50]}")
        return

    # Group registration / single-group enforcement
    if chat.type in ("group", "supergroup"):
        group = _load_group()
        if not group.get("chat_id"):
            _save_group(chat.id, chat.title or "Minder Group")
        elif group["chat_id"] != chat.id:
            await update.message.reply_text(
                "Minder is already connected to another group. "
                "Remove me from that group first to switch."
            )
            return

    # DMs redirect to the group
    if chat.type == "private":
        group = _load_group()
        if group.get("chat_id"):
            await update.message.reply_text(
                "Minder works in your family group. Send messages there instead."
            )
        else:
            await update.message.reply_text(
                "Add me to a family group to get started.\n\n"
                '1. Create a Telegram group (e.g. "Minder Home")\n'
                "2. Add me to the group\n"
                "3. Send /start in the group"
            )
        return

    # In groups: only respond to commands, @mentions, or replies to the bot
    text = update.message.text
    bot_username = context.bot.username or ""
    is_command = text.startswith("/")
    is_mention = f"@{bot_username}".lower() in text.lower()
    is_reply_to_bot = (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user
        and update.message.reply_to_message.from_user.id == context.bot.id
    )
    if not is_command and not is_mention and not is_reply_to_bot:
        return

    clean_text = text.replace(f"@{bot_username}", "").strip()
    if not clean_text:
        return

    user = update.effective_user
    sender = user.first_name if user else ""
    log.info(f"{sender}: {clean_text[:100]}")

    # /start gets channel-side progress messages (it takes minutes)
    if clean_text.lower().strip() in ("/start", "start", "/cameras", "/setup"):
        thinking = await update.message.reply_text("Setting up Home Assistant...")
        with contextlib.suppress(Exception):
            await ops_post("/api/ops/setup/ha", {}, timeout=90)
        await update.message.reply_text("Discovering cameras (this takes a minute)...")
        try:
            result = await ops_post("/api/ops/setup/cameras", {}, timeout=240)
        except Exception as e:
            await render_result(update, thinking, {"text": f"Setup failed: {e}"})
            return
        await render_result(update, thinking, result)
        return

    parts = clean_text.strip().split()
    cmd0 = parts[0].lower() if parts else ""
    if cmd0 in ("/health", "/status", "/approvals", "/approve", "/reject"):
        await _handle_recovery(update, cmd0, parts[1] if len(parts) > 1 else "")
        return

    thinking = await update.message.reply_text("Working on it...")
    try:
        result = await ops_post(
            "/api/ops/message",
            {
                "text": clean_text,
                "source": "telegram",
                "sender": sender,
            },
        )
    except httpx.ConnectError:
        await render_result(
            update, thinking, {"text": "Minder service is not running. Try again in a moment."}
        )
        return
    except Exception as e:
        log.exception("Ops call failed")
        await render_result(update, thinking, {"text": f"Something went wrong: {e}"})
        return

    await render_result(update, thinking, result)


# ---- Recovery commands ----


def _format_health(h: dict) -> str:
    tick = lambda ok: "OK" if ok else "FAIL"  # noqa: E731
    lines = ["Health: " + ("all good" if h.get("healthy") else "needs attention")]
    for name, st in h.get("files", {}).items():
        lines.append(f"  {name}: {st}")
    lines.append(f"  Home Assistant: {tick(h.get('ha'))}")
    lines.append(f"  Frigate: {tick(h.get('frigate'))}")
    lines.append(f"  backups: {h.get('backups')} (latest {h.get('latest_backup') or 'none'})")
    lines.append(f"  HA volume backups: {h.get('ha_volume_backups')}")
    return "\n".join(lines)


async def _handle_recovery(update: Update, cmd: str, arg: str = "") -> None:
    if cmd == "/approve":
        msg = await update.message.reply_text("Approving repair...")
        try:
            r = await ops_post("/api/ops/approvals/approve", {"id": arg}, timeout=180)
            if r.get("status") == "approved":
                fixed = r.get("repaired", []) + r.get("regenerated", [])
                text = "Approved. " + (
                    f"Repaired: {', '.join(fixed)}" if fixed else "Nothing needed fixing."
                )
            elif r.get("status") == "none":
                text = "Nothing is awaiting approval."
            else:
                text = "Could not approve."
        except Exception as e:
            text = f"Approve failed: {e}"
        await msg.edit_text(text)
        return
    if cmd == "/reject":
        try:
            r = await ops_post("/api/ops/approvals/reject", {"id": arg})
            text = "Dismissed." if r.get("status") == "rejected" else "Nothing to dismiss."
        except Exception as e:
            text = f"Reject failed: {e}"
        await update.message.reply_text(text)
        return
    if cmd == "/approvals":
        try:
            ap = (await ops_get("/api/ops/approvals")).get("approvals", [])
            text = (
                (
                    "Pending approvals:\n"
                    + "\n".join(f"  {a['short']}: {a['reason']}" for a in ap)
                    + "\nApprove with /approve <id>"
                )
                if ap
                else "No repairs awaiting approval."
            )
        except Exception as e:
            text = f"Couldn't read approvals: {e}"
        await update.message.reply_text(text)
        return
    try:
        h = await ops_get("/api/ops/health")
        await update.message.reply_text(_format_health(h))
    except Exception as e:
        await update.message.reply_text(f"Couldn't read health: {e}")


# ---- Alert polling ----


async def daily_backup(context: Any) -> None:
    """Periodic snapshot of precious state + the HA config volume to the host
    backups path (full-stack DR)."""
    try:
        res = await ops_post("/api/ops/backup", {})
        log.info(f"State backup: {res.get('files')} -> {res.get('ts')}")
    except Exception as e:
        log.error(f"Backup error: {e}")
    try:
        res = await ops_post("/api/ops/backup/ha", {})
        log.info(f"HA volume backup: {res.get('file')} ({res.get('size_kb')}kb)")
    except Exception as e:
        log.error(f"HA volume backup error: {e}")


async def poll_alerts(context: Any) -> None:
    group = _load_group()
    group_chat_id = group.get("chat_id")
    if not group_chat_id:
        return

    try:
        data = await ops_get("/api/ops/alerts")
        alerts = data.get("alerts", [])
        for alert in alerts:
            # Media-only follow-ups (snapshot/clip that arrive after the text
            # alert) carry an empty message — send just the media, no bare 🚨.
            if alert.get("message"):
                await context.bot.send_message(chat_id=group_chat_id, text=f"🚨 {alert['message']}")
            snap_path = alert.get("snapshot_path")
            if snap_path and Path(snap_path).exists():
                await context.bot.send_photo(
                    chat_id=group_chat_id,
                    photo=Path(snap_path).open("rb"),
                    caption=alert.get("camera", ""),
                )
            video_path = alert.get("video_path")
            if video_path and Path(video_path).exists():
                await context.bot.send_video(
                    chat_id=group_chat_id,
                    video=Path(video_path).open("rb"),
                    supports_streaming=True,
                )
        if alerts:
            log.info(f"Sent {len(alerts)} alert(s) to group")
    except Exception as e:
        log.error(f"Alert poll error: {e}")


# ---- Bot setup ----


async def post_init(app: Application) -> None:
    from telegram import BotCommand

    await app.bot.set_my_commands(
        [
            BotCommand("start", "Discover cameras on your network"),
            BotCommand("cameras", "Show all cameras with snapshots"),
            BotCommand("check", "Check a condition (e.g. /check is anyone at the gate)"),
            BotCommand("snap", "Snapshot from a camera (e.g. /snap porch)"),
            BotCommand("video", "Live video clip (e.g. /video main-door)"),
            BotCommand("health", "Show appliance health"),
            BotCommand("approvals", "Show repairs awaiting your approval"),
            BotCommand("reset", "Start a fresh conversation"),
        ]
    )
    log.info("Telegram command menu registered")

    app.job_queue.run_repeating(poll_alerts, interval=10, first=5)
    log.info("Alert polling started (every 10s)")

    app.job_queue.run_repeating(daily_backup, interval=86400, first=3600)
    log.info("Daily state backup scheduled")


def main() -> None:
    token = os.environ.get("MINDER_TELEGRAM_TOKEN")
    if not token:
        log.error("MINDER_TELEGRAM_TOKEN not set")
        raise SystemExit(1)

    # concurrent_updates: handle messages from multiple people at once instead
    # of strictly one-at-a-time. Non-model work (RTSP capture, YOLO, HA calls)
    # then overlaps; only the single-GPU LLM steps still queue at Ollama.
    app = Application.builder().token(token).concurrent_updates(True).post_init(post_init).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.COMMAND, message_handler))

    log.info("Minder bot starting as thin adapter, concurrent (ops API: %s)...", API_URL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
