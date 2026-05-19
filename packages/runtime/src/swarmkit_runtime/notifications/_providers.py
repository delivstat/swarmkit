"""Provider factory — builds notification providers from workspace config."""

from __future__ import annotations

from typing import Any

from swarmkit_runtime.notifications._discord import DiscordNotificationProvider
from swarmkit_runtime.notifications._provider import NotificationProvider
from swarmkit_runtime.notifications._slack import SlackNotificationProvider
from swarmkit_runtime.notifications._telegram import TelegramNotificationProvider
from swarmkit_runtime.notifications._terminal import TerminalNotificationProvider
from swarmkit_runtime.notifications._webhook import WebhookNotificationProvider


def build_provider(provider_type: str, config: dict[str, Any]) -> NotificationProvider:
    """Factory for notification providers from workspace config."""
    if provider_type == "terminal":
        return TerminalNotificationProvider()
    if provider_type == "webhook":
        return WebhookNotificationProvider(
            url=config["url"],
            headers=config.get("headers"),
            timeout=config.get("timeout", 10.0),
        )
    if provider_type == "slack":
        return SlackNotificationProvider(
            webhook_url=config["webhook_url"],
            channel=config.get("channel"),
        )
    if provider_type == "discord":
        return DiscordNotificationProvider(
            webhook_url=config["webhook_url"],
            username=config.get("username", "SwarmKit"),
        )
    if provider_type == "telegram":
        return TelegramNotificationProvider(
            bot_token=config["bot_token"],
            chat_id=config["chat_id"],
        )
    msg = (
        f"Unknown notification provider: {provider_type}. "
        f"Available: terminal, webhook, slack, discord, telegram."
    )
    raise ValueError(msg)
