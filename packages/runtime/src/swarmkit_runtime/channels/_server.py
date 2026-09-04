"""Channels MCP server — a swarm reaches a human, and hears the answer.

The transports are the notification providers already in the tree; this server makes them
addressable by an agent. See `design/details/channel-skills.md`.

Three tools, and the middle one is why the other two exist:

- ``channel_send``    announce something
- ``channel_ask``     ask, and block on the answer up to a bounded timeout
- ``channel_replies`` what has come back since a cursor

**A reply is information, never authority.** Invariant #6 reserves ``approvals:resolve`` for human
identity, and a Telegram ``chat_id`` is not an identity assertion — it says a message arrived from
a chat, not that a particular person authenticated. So nothing here resolves a gate; a human who
wants to approve still does it where identity is established.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import httpx

from swarmkit_runtime.channels._config import (
    INBOUND_CAPABLE,
    Channel,
    ChannelConfigError,
    load_channels,
)
from swarmkit_runtime.mcp._sdk_compat import MCPServerClass
from swarmkit_runtime.notifications._provider import NotificationEvent

server = MCPServerClass("swarmkit-channels")

#: A bound is never infinite (command-packs.md applies the same rule to a subprocess). An agent
#: waiting forever on a human who has gone to lunch is a hung run, not a patient one.
DEFAULT_TIMEOUT_S = 300.0
MAX_TIMEOUT_S = 3600.0
_POLL_INTERVAL_S = 2.0

_channels: dict[str, Channel] = {}
_workspace: Path = Path()

#: Telegram getUpdates is SINGLE-CONSUMER: two pollers on one token steal each other's updates and
#: the loser silently sees nothing. The offset is held here, per token, and a second poller for a
#: token already being polled is refused by name rather than quietly losing messages.
_offsets: dict[str, int] = {}
_poll_owner: dict[str, str] = {}


def _set_workspace(path: Path) -> None:
    global _workspace, _channels  # noqa: PLW0603
    _workspace = path.resolve()
    _channels = load_channels(_workspace)


def _get(channel: str) -> Channel:
    if not _channels:
        msg = (
            "no channels are configured. Add a `channels:` block to workspace.yaml — without one "
            "the transports exist but nothing can address them."
        )
        raise ChannelConfigError(msg)
    found = _channels.get(channel)
    if found is None:
        msg = f"unknown channel {channel!r}. Configured: {sorted(_channels)}."
        raise ChannelConfigError(msg)
    return found


async def _send(channel: Channel, text: str) -> bool:
    """Deliver through the notification provider, reusing its formatting and error handling."""
    event = NotificationEvent(
        event_type="hitl_requested",
        run_id=os.environ.get("SWARMKIT_RUN_ID", "-"),
        topology_id=os.environ.get("SWARMKIT_TOPOLOGY_ID", "-"),
        summary=text,
    )
    return await channel.provider.notify(event)


async def _fetch_telegram(channel: Channel, *, timeout_s: float) -> list[dict[str, Any]]:
    """One `getUpdates` long-poll. Returns the messages it saw and advances the offset."""
    token = channel.secret
    offset = _offsets.get(token, 0)
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params: dict[str, Any] = {"timeout": int(min(timeout_s, 50)), "offset": offset or None}
    async with httpx.AsyncClient(timeout=timeout_s + 10) as client:
        resp = await client.get(url, params={k: v for k, v in params.items() if v is not None})
        if resp.status_code >= 400:
            return []
        payload = resp.json()
    out: list[dict[str, Any]] = []
    for update in payload.get("result", []):
        _offsets[token] = max(_offsets.get(token, 0), int(update.get("update_id", 0)) + 1)
        message = update.get("message") or update.get("channel_post") or {}
        text = message.get("text")
        if not text:
            continue
        want = str(channel.config.get("chat_id", ""))
        got = str((message.get("chat") or {}).get("id", ""))
        # A bot may be in several chats. Messages from a chat this channel does not name are not
        # this channel's replies, and treating them as such would answer a question with a
        # stranger's sentence.
        if want and got != want:
            continue
        out.append(
            {
                "id": int(update.get("update_id", 0)),
                "text": text,
                "from": (message.get("from") or {}).get("username", ""),
                "at": int(message.get("date", 0)),
            }
        )
    return out


def _claim_poller(channel: Channel) -> None:
    owner = _poll_owner.get(channel.secret)
    if owner is not None and owner != channel.id:
        msg = (
            f"channel {channel.id!r} cannot poll: bot token already polled by channel {owner!r}. "
            f"Telegram's getUpdates is single-consumer — a second poller would steal updates and "
            f"both would silently miss messages. Use one inbound channel per bot token."
        )
        raise ChannelConfigError(msg)
    _poll_owner[channel.secret] = channel.id


def _bound(timeout_s: float) -> float:
    """Clamp a requested wait into [1s, MAX_TIMEOUT_S].

    The ceiling stops a run hanging on a human who has gone to lunch; the floor stops a caller
    passing 0 and reading the instant "nobody answered" as a real answer.
    """
    return max(1.0, min(float(timeout_s), MAX_TIMEOUT_S))


@server.tool()
async def channel_send(channel: str, text: str) -> dict[str, Any]:
    """Post a message to a configured channel.

    Use this to tell a human something. Use ``channel_ask`` when you need an answer back.
    """
    target = _get(channel)
    delivered = await _send(target, text)
    return {"delivered": delivered, "channel": channel}


@server.tool()
async def channel_ask(
    channel: str,
    question: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Ask a human a question and wait for their reply, up to ``timeout_s`` seconds.

    Returns ``{"answered": false}`` when nobody replies in time. That is a normal outcome, not an
    error: an agent that could not reach anyone should say so and carry on, the way an unanswered
    gate does.

    The answer is information. It does not approve anything — a chat message is not an identity
    assertion, so it cannot resolve a governance gate no matter what it says.
    """
    target = _get(channel)
    if not target.supports_inbound:
        return {
            "answered": False,
            "reason": "unsupported",
            "detail": (
                f"{target.provider_type} is send-only in this runtime, so the question was NOT "
                f"sent — asking on a channel that cannot answer strands the human as well as the "
                f"agent. Inbound is available on: {sorted(INBOUND_CAPABLE)}."
            ),
        }
    _claim_poller(target)

    bounded = _bound(timeout_s)
    # Drain whatever is already queued, so a reply sent before the question is not mistaken for
    # an answer to it.
    await _fetch_telegram(target, timeout_s=0)

    if not await _send(target, question):
        return {"answered": False, "reason": "send_failed"}

    deadline = time.monotonic() + bounded
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        replies = await _fetch_telegram(target, timeout_s=min(remaining, 30))
        if replies:
            first = replies[0]
            return {
                "answered": True,
                "text": first["text"],
                "from": first["from"],
                "channel": channel,
            }
        await asyncio.sleep(min(_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))
    return {"answered": False, "reason": "timeout", "waited_s": bounded}


@server.tool()
async def channel_replies(channel: str, limit: int = 20) -> dict[str, Any]:
    """Messages received on a channel since the last read.

    On a send-only channel this reports ``unsupported`` rather than an empty list. An empty list
    would read as *nobody answered*, which is a different and much worse claim than *we cannot
    hear*.
    """
    target = _get(channel)
    if not target.supports_inbound:
        return {
            "supported": False,
            "channel": channel,
            "detail": f"{target.provider_type} has no inbound path configured in this runtime.",
        }
    _claim_poller(target)
    replies = await _fetch_telegram(target, timeout_s=0)
    return {"supported": True, "channel": channel, "messages": replies[:limit]}


@server.tool()
def channels_list() -> list[dict[str, Any]]:
    """The configured channels, and which of them a human can answer on."""
    return [
        {
            "id": c.id,
            "provider": c.provider_type,
            "inbound": c.inbound and c.supports_inbound,
            "can_receive": c.supports_inbound,
        }
        for c in _channels.values()
    ]


def run_server(workspace_path: Path | None = None) -> None:
    """Entry point for the CLI launcher."""
    _set_workspace(workspace_path or Path(os.environ.get("SWARMKIT_WORKSPACE", ".")))
    server.run()
