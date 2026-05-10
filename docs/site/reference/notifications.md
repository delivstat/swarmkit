# Notifications

SwarmKit persists and delivers notifications for runtime events. Notifications are stored locally and optionally delivered to external services (Slack, Discord, Telegram, webhooks).

## Architecture

```
Runtime fires event (HITL request, error, skill gap)
    │
    ├─→ NotificationStore (SQLite) — persisted, queryable
    │       ↕
    │   CLI: swarmkit notifications --last 10
    │   Web UI: GET /notifications?status=pending
    │
    └─→ External providers (best-effort delivery)
            Slack / Discord / Telegram / Webhook
            Delivery status tracked in store
```

The primary record lives in the NotificationStore. External delivery is a side effect — if Slack is down, the notification is still queryable via CLI and web UI.

## Configuration

Add a `notifications:` block to `workspace.yaml`:

```yaml
notifications:
  - provider: slack
    config:
      webhook_url: https://hooks.slack.com/services/T.../B.../xxx
      channel: "#swarmkit-alerts"
    events: [hitl_requested, run_ended_error]

  - provider: discord
    config:
      webhook_url: https://discord.com/api/webhooks/123/abc
    events: [hitl_requested]

  - provider: telegram
    config:
      bot_token: "123456:ABC-DEF"
      chat_id: "-100123456"

  - provider: terminal
    events: [hitl_requested]
```

## Providers

| Provider | Config | Description |
|---|---|---|
| `terminal` | (none) | Prints to stderr. Default in TTY mode. |
| `webhook` | `url`, `headers`, `timeout` | Generic HTTP POST with JSON payload |
| `slack` | `webhook_url`, `channel` | Slack incoming webhook |
| `discord` | `webhook_url`, `username` | Discord webhook with color-coded embeds |
| `telegram` | `bot_token`, `chat_id` | Telegram Bot API with Markdown |

## Events

| Event | Fires when |
|---|---|
| `hitl_requested` | A human approval gate is triggered |
| `run_ended_error` | A topology run fails with an error |
| `skill_gap_surfaced` | A new skill gap is detected |

## Event filtering

Each provider can subscribe to specific event types via the `events` list. If omitted, the provider receives all events.

## Notification store

All notifications are persisted to `.swarmkit/notifications.sqlite` with delivery tracking:

| Field | Description |
|---|---|
| `id` | Unique notification ID |
| `event_type` | hitl_requested / run_ended_error / skill_gap_surfaced |
| `run_id` | Associated run |
| `topology_id` | Topology that generated the event |
| `summary` | Human-readable summary |
| `status` | pending / delivered / failed |
| `provider` | Which provider delivered (or failed) |
| `delivered_at` | Timestamp of successful delivery |
| `error` | Error message if delivery failed |
| `created_at` | When the notification was created |

## CLI access (planned)

```bash
swarmkit notifications --last 10        # recent notifications
swarmkit notifications --pending        # undelivered
swarmkit notifications --run <run-id>   # for a specific run
```

## Web UI access (planned)

The web UI reads from the same NotificationStore via the WorkspaceRuntime service layer. Same data, same API — no separate notification backend needed.

## Webhook payload format

The generic webhook provider sends:

```json
{
  "event_type": "hitl_requested",
  "run_id": "run-001",
  "topology_id": "code-review",
  "summary": "Deploy approval needed for high-risk change",
  "metadata": {
    "agent_id": "resolution-agent",
    "review_queue_id": "rq-42"
  }
}
```

## Adding custom providers

Implement the `NotificationProvider` ABC:

```python
from swarmkit_runtime.notifications import NotificationProvider, NotificationEvent

class MyProvider(NotificationProvider):
    provider_id = "my-service"

    async def notify(self, event: NotificationEvent) -> bool:
        # Send to your service
        # Return True if delivered, False otherwise
        ...
```

Provider errors are caught by the registry — a failed delivery never crashes the runtime.
