"""Notification plugin system — webhook-based alerts for runtime events.

Notifications run outside the SwarmKit runtime process. The runtime emits
structured events; notification providers consume them via configured
endpoints. Multiple providers can be configured in parallel.

Built-in providers:
  - terminal: prints to stdout (local dev)
  - webhook: generic HTTP POST with configurable URL + payload template
  - slack: Slack incoming webhook
  - discord: Discord webhook with embeds
  - telegram: Telegram Bot API

Fires on:
  - hitl_requested: human approval needed
  - run_ended_error: topology run failed
  - skill_gap_surfaced: new skill gap detected

See design/details/human-interaction-model.md.
"""

from swarmkit_runtime.notifications._provider import (
    NotificationEvent,
    NotificationProvider,
    NotificationRegistry,
)
from swarmkit_runtime.notifications._providers import (
    DiscordNotificationProvider,
    SlackNotificationProvider,
    TelegramNotificationProvider,
    TerminalNotificationProvider,
    WebhookNotificationProvider,
)

__all__ = [
    "DiscordNotificationProvider",
    "NotificationEvent",
    "NotificationProvider",
    "NotificationRegistry",
    "SlackNotificationProvider",
    "TelegramNotificationProvider",
    "TerminalNotificationProvider",
    "WebhookNotificationProvider",
]
