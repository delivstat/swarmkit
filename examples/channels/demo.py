#!/usr/bin/env python3
"""Demo: a swarm asks a human a question and waits for the answer.

Runs against a real Telegram bot when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. Without
them it falls back to the terminal channel, so the demo runs for somebody who has no bot — the
point being that the *wiring* is what is demonstrated, and that is identical either way.

    just demo-channels
    TELEGRAM_BOT_TOKEN=… TELEGRAM_CHAT_ID=… just demo-channels
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import yaml
from swarmkit_runtime.channels import _server as srv
from swarmkit_runtime.channels import load_channels

LIVE = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def _workspace(root: Path) -> Path:
    channels: dict[str, object] = {"console": {"provider": "terminal", "credentials_ref": "unused"}}
    credentials: dict[str, object] = {}
    if LIVE:
        credentials["tg"] = {"source": "env", "config": {"env": "TELEGRAM_BOT_TOKEN"}}
        channels["ops"] = {
            "provider": "telegram",
            "credentials_ref": "tg",
            "inbound": True,
            "config": {"chat_id": os.environ["TELEGRAM_CHAT_ID"]},
        }
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "channels-demo", "name": "Channels Demo"},
                "credentials": credentials,
                "channels": channels,
            }
        )
    )
    return root


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = _workspace(Path(tmp))
        srv._set_workspace(ws)

        print("── configured channels ──")
        for c in srv.channels_list():
            answerable = "can answer" if c["can_receive"] else "send-only"
            print(f"  {c['id']:10} {c['provider']:10} {answerable}")

        print("\n── channel_send ──")
        target = "ops" if LIVE else "console"
        print(" ", await srv.channel_send(target, "Deployment 1.206.0 finished."))

        print("\n── channel_replies on a send-only channel ──")
        print("  (an empty list would read as 'nobody answered' — a different, worse claim)")
        send_only = "console"
        print(" ", await srv.channel_replies(send_only))

        print("\n── channel_ask ──")
        if LIVE:
            print("  asking on Telegram; reply in that chat within 60s…")
            print(" ", await srv.channel_ask("ops", "Promote 1.206.0 to production?", timeout_s=60))
        else:
            print("  no TELEGRAM_BOT_TOKEN set — the terminal channel cannot receive,")
            print("  so ask reports `unsupported` and does NOT send the question:")
            print(" ", await srv.channel_ask("console", "Promote 1.206.0?", timeout_s=5))

        print("\n── the wiring assertion ──")
        loaded = load_channels(ws)
        print(f"  {len(loaded)} channel(s) declared in YAML became live providers:")
        for cid, ch in loaded.items():
            print(f"    {cid} -> {type(ch.provider).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
