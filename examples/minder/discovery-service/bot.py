"""Minder Telegram Bot — conversational interface to the home AI agent.

Each Minder installation runs its own bot instance. The homeowner creates
a bot via BotFather and provides the token during setup.

Commands:
  /start          — welcome + auto-discover cameras
  /cameras        — list discovered cameras with thumbnails
  /snap <name>    — fresh snapshot from a named camera
  /check <query>  — ask a question about what all cameras see
  /scan           — re-scan network for new cameras/devices

Any text message is treated as a vision query across all cameras.
"""

import json
import logging
import os
import time
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from discover import Camera, capture_snapshot, discover_all, probe_onvif, scan_subnet
from vision import analyse_frame, check_scene, describe_scene, SNAPSHOT_DIR

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("minder")

DATA_DIR = Path("/data")
CAMERAS_FILE = DATA_DIR / "cameras.json"

SUBNET = os.environ.get("MINDER_SUBNET", "192.168.0")
CAM_USER = os.environ.get("MINDER_CAM_USER", "admin")
CAM_PASS = os.environ.get("MINDER_CAM_PASS", "admin123")
VISION_MODEL = os.environ.get("MINDER_VISION_MODEL", "gemma4:e2b")


def load_cameras() -> list[Camera]:
    if not CAMERAS_FILE.exists():
        return []
    data = json.loads(CAMERAS_FILE.read_text())
    return [Camera(**c) for c in data]


def save_cameras(cameras: list[Camera]) -> None:
    from dataclasses import asdict
    CAMERAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAMERAS_FILE.write_text(json.dumps([asdict(c) for c in cameras], indent=2))


def camera_by_name(cameras: list[Camera], name: str) -> Camera | None:
    name_lower = name.lower().strip()
    for cam in cameras:
        if cam.name.lower() == name_lower:
            return cam
        if cam.ip.endswith(f".{name_lower}") or cam.ip == name_lower:
            return cam
    for cam in cameras:
        if name_lower in cam.name.lower():
            return cam
    return None


def fresh_snapshot(cam: Camera) -> Path | None:
    return capture_snapshot(cam, CAM_USER, CAM_PASS)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! I'm Minder, your home AI agent.\n\n"
        "Let me scan your network for cameras..."
    )

    cameras = discover_all(SUBNET, CAM_USER, CAM_PASS)
    cameras = [c for c in cameras if c.rtsp_url]
    save_cameras(cameras)

    if not cameras:
        await update.message.reply_text(
            "No cameras found on the network. Check that your cameras "
            "are on the same subnet and ONVIF is enabled."
        )
        return

    lines = [f"Found {len(cameras)} cameras:\n"]
    for i, cam in enumerate(cameras, 1):
        label = cam.name or cam.ip
        lines.append(f"  {i}. {label} — {cam.manufacturer} {cam.model}")

    lines.append(
        "\nName your cameras by replying with numbers:\n"
        "  1=front gate, 2=backyard, 3=porch\n\n"
        "Or just ask me anything:\n"
        "  \"Is anyone at the front door?\"\n"
        "  \"Show me the backyard\""
    )
    await update.message.reply_text("\n".join(lines))

    for cam in cameras[:3]:
        snap_path = SNAPSHOT_DIR / f"{cam.ip.replace('.', '_')}.jpg"
        if snap_path.exists():
            label = cam.name or cam.ip
            await update.message.reply_photo(
                photo=snap_path.open("rb"),
                caption=f"{label} — {cam.manufacturer} {cam.model}",
            )


async def cmd_cameras(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cameras = load_cameras()
    if not cameras:
        await update.message.reply_text("No cameras discovered yet. Send /start to scan.")
        return

    for cam in cameras:
        snap = fresh_snapshot(cam)
        label = cam.name or cam.ip
        if snap and snap.exists():
            await update.message.reply_photo(
                photo=snap.open("rb"),
                caption=f"{label} — {cam.manufacturer} {cam.model}",
            )
        else:
            await update.message.reply_text(f"{label} — {cam.manufacturer} {cam.model} (no snapshot)")


async def cmd_snap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cameras = load_cameras()
    if not cameras:
        await update.message.reply_text("No cameras discovered yet. Send /start to scan.")
        return

    if not ctx.args:
        names = [cam.name or cam.ip for cam in cameras]
        await update.message.reply_text(
            "Which camera? Usage: /snap <name>\n\n"
            f"Available: {', '.join(names)}"
        )
        return

    name = " ".join(ctx.args)
    cam = camera_by_name(cameras, name)
    if not cam:
        names = [cam.name or cam.ip for cam in cameras]
        await update.message.reply_text(
            f"Camera '{name}' not found.\nAvailable: {', '.join(names)}"
        )
        return

    await update.message.reply_text(f"Capturing from {cam.name or cam.ip}...")
    snap = fresh_snapshot(cam)
    if snap and snap.exists():
        result = describe_scene(snap, VISION_MODEL)
        await update.message.reply_photo(
            photo=snap.open("rb"),
            caption=f"{cam.name or cam.ip}: {result.answer[:900]}",
        )
    else:
        await update.message.reply_text("Failed to capture snapshot.")


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cameras = load_cameras()
    if not cameras:
        await update.message.reply_text("No cameras discovered yet. Send /start to scan.")
        return

    if not ctx.args:
        await update.message.reply_text(
            "What should I check? Usage: /check <question>\n\n"
            "Examples:\n"
            '  /check is there a person\n'
            '  /check is there a car in the driveway\n'
            '  /check is the gate open'
        )
        return

    condition = " ".join(ctx.args)
    await update.message.reply_text(f"Checking {len(cameras)} cameras: \"{condition}\"...")

    matches = []
    for cam in cameras:
        snap = fresh_snapshot(cam)
        if not snap or not snap.exists():
            continue
        result = check_scene(snap, condition, VISION_MODEL)
        label = cam.name or cam.ip
        if result.match:
            matches.append((cam, result, snap))
            await update.message.reply_photo(
                photo=snap.open("rb"),
                caption=f"YES — {label}: {result.answer[:900]}",
            )

    if not matches:
        await update.message.reply_text(f"No cameras matched: \"{condition}\"")
    else:
        await update.message.reply_text(
            f"{len(matches)} camera(s) matched: "
            + ", ".join(m[1].camera_name for m in matches)
        )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Scanning network for new devices...")
    old_cameras = load_cameras()
    old_ips = {c.ip for c in old_cameras}

    cameras = discover_all(SUBNET, CAM_USER, CAM_PASS)
    cameras = [c for c in cameras if c.rtsp_url]

    for cam in cameras:
        old = next((c for c in old_cameras if c.ip == cam.ip), None)
        if old and old.name:
            cam.name = old.name

    save_cameras(cameras)

    new_ips = {c.ip for c in cameras} - old_ips
    if new_ips:
        lines = ["New devices found:"]
        for cam in cameras:
            if cam.ip in new_ips:
                lines.append(f"  {cam.ip} — {cam.manufacturer} {cam.model}")
                snap_path = SNAPSHOT_DIR / f"{cam.ip.replace('.', '_')}.jpg"
                if snap_path.exists():
                    await update.message.reply_photo(
                        photo=snap_path.open("rb"),
                        caption=f"NEW: {cam.ip} — {cam.manufacturer} {cam.model}",
                    )
        await update.message.reply_text("\n".join(lines))
    else:
        await update.message.reply_text(f"No new devices. {len(cameras)} cameras online.")


async def handle_naming(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle camera naming: '1=front gate, 2=backyard'"""
    text = update.message.text.strip()

    if "=" in text and any(c.isdigit() for c in text.split("=")[0]):
        cameras = load_cameras()
        if not cameras:
            return

        assignments = [part.strip() for part in text.split(",")]
        named = []
        for assignment in assignments:
            if "=" not in assignment:
                continue
            idx_str, name = assignment.split("=", 1)
            try:
                idx = int(idx_str.strip()) - 1
                if 0 <= idx < len(cameras):
                    cameras[idx].name = name.strip()
                    named.append(f"  {cameras[idx].ip} → {name.strip()}")
            except ValueError:
                continue

        if named:
            save_cameras(cameras)
            await update.message.reply_text("Cameras named:\n" + "\n".join(named))
            return

    return None


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text messages as vision queries."""
    text = update.message.text.strip()

    if "=" in text and text[0].isdigit():
        await handle_naming(update, ctx)
        return

    cameras = load_cameras()
    if not cameras:
        await update.message.reply_text(
            "No cameras set up yet. Send /start to discover cameras on your network."
        )
        return

    target_cam = None
    text_lower = text.lower()
    for cam in cameras:
        if cam.name and cam.name.lower() in text_lower:
            target_cam = cam
            break

    if target_cam:
        await update.message.reply_text(f"Checking {target_cam.name}...")
        snap = fresh_snapshot(target_cam)
        if snap and snap.exists():
            result = check_scene(snap, text, VISION_MODEL)
            status = "YES" if result.match else "NO"
            await update.message.reply_photo(
                photo=snap.open("rb"),
                caption=f"{status} — {target_cam.name}: {result.answer[:900]}",
            )
        else:
            await update.message.reply_text(f"Couldn't capture from {target_cam.name}")
    else:
        await update.message.reply_text(f"Checking all {len(cameras)} cameras...")
        any_match = False
        for cam in cameras:
            snap = fresh_snapshot(cam)
            if not snap or not snap.exists():
                continue
            result = check_scene(snap, text, VISION_MODEL)
            label = cam.name or cam.ip
            if result.match:
                any_match = True
                await update.message.reply_photo(
                    photo=snap.open("rb"),
                    caption=f"YES — {label}: {result.answer[:900]}",
                )

        if not any_match:
            await update.message.reply_text(f"None of the cameras matched: \"{text}\"")


def main() -> None:
    token = os.environ.get("MINDER_TELEGRAM_TOKEN")
    if not token:
        log.error("MINDER_TELEGRAM_TOKEN not set")
        raise SystemExit(1)

    log.info("Starting Minder bot...")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cameras", cmd_cameras))
    app.add_handler(CommandHandler("snap", cmd_snap))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot ready. Polling for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
