"""Resolve the workspace `channels:` block into constructed notification providers.

This is the piece whose absence made 548 lines of channel transport unreachable: the providers
were complete and tested, and nothing could address them because there was no configuration shape
and no code that turned one into a provider. See `design/details/channel-skills.md`.

Tokens never appear literally. `credentials_ref` names an entry in the workspace `credentials`
block and resolves through the same path every other secret uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from swarmkit_runtime.notifications._provider import NotificationProvider
from swarmkit_runtime.notifications._providers import build_provider

#: Providers a human can answer on. Telegram's Bot API offers `getUpdates`, which long-polls over
#: outbound HTTPS — no inbound port, no public URL, works behind NAT. Discord needs a gateway
#: WebSocket held open and Slack needs Socket Mode; neither fits a request-shaped runtime yet.
INBOUND_CAPABLE = frozenset({"telegram"})

#: The secret each provider needs, and the config key the provider constructor wants it under.
_SECRET_KEY = {
    "telegram": "bot_token",
    "discord": "webhook_url",
    "slack": "webhook_url",
    "webhook": "url",
}


class ChannelConfigError(RuntimeError):
    """A channel cannot be constructed. Carries what to fix, not just what failed."""


@dataclass(frozen=True)
class Channel:
    """One named destination, resolved and ready to send on."""

    id: str
    provider_type: str
    provider: NotificationProvider
    inbound: bool
    config: dict[str, Any]
    secret: str

    @property
    def supports_inbound(self) -> bool:
        return self.provider_type in INBOUND_CAPABLE


def _resolve_secret(ref: str, credentials: dict[str, Any], channel_id: str) -> str:
    """The token or webhook URL behind `credentials_ref`.

    Delegates to the MCP credential resolver rather than reading the env directly, so a channel
    reaches a Vault or file-backed secret the same way a remote MCP server does — one resolver,
    one set of semantics.
    """
    if ref not in credentials:
        msg = (
            f"channel {channel_id!r} references credential {ref!r}, which is not in the workspace "
            f"`credentials` block. Add it, or point credentials_ref at an existing entry."
        )
        raise ChannelConfigError(msg)

    from swarmkit_runtime.mcp._credentials import CredentialError, substitute  # noqa: PLC0415

    try:
        # `substitute` raises when a credential resolves to nothing, rather than yielding "". An
        # empty token would fail later as a platform auth error that names Telegram instead of
        # naming the missing secret.
        return substitute(f"{{credential.{ref}}}", credentials)
    except CredentialError as exc:
        raise ChannelConfigError(f"channel {channel_id!r}: {exc}") from exc


def load_channels(workspace_path: Path) -> dict[str, Channel]:
    """Build every channel declared in a workspace. Raises rather than skipping a broken one.

    A channel that silently fails to load is worse than one that refuses: the swarm carries on
    believing it can reach somebody.
    """
    ws_file = workspace_path / "workspace.yaml"
    if not ws_file.exists():
        return {}
    doc = yaml.safe_load(ws_file.read_text()) or {}
    declared = doc.get("channels") or {}
    credentials = doc.get("credentials") or {}

    channels: dict[str, Channel] = {}
    for channel_id, raw in declared.items():
        provider_type = raw.get("provider", "")
        inbound = bool(raw.get("inbound", False))
        if inbound and provider_type not in INBOUND_CAPABLE:
            # The schema also refuses this. Checked again here because the schema is not in the
            # path when a workspace is hand-assembled in a test or by another tool.
            msg = (
                f"channel {channel_id!r}: inbound is only supported on "
                f"{sorted(INBOUND_CAPABLE)}, not {provider_type!r}."
            )
            raise ChannelConfigError(msg)

        config = dict(raw.get("config") or {})
        secret = ""
        if provider_type != "terminal":
            secret = _resolve_secret(raw["credentials_ref"], credentials, channel_id)
            key = _SECRET_KEY.get(provider_type)
            if key is None:
                msg = f"channel {channel_id!r}: unknown provider {provider_type!r}."
                raise ChannelConfigError(msg)
            config[key] = secret

        try:
            provider = build_provider(provider_type, config)
        except (KeyError, ValueError) as exc:
            msg = f"channel {channel_id!r}: {exc}"
            raise ChannelConfigError(msg) from exc

        channels[channel_id] = Channel(
            id=channel_id,
            provider_type=provider_type,
            provider=provider,
            inbound=inbound,
            config=config,
            secret=secret,
        )
    return channels
