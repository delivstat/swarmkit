"""Standalone test for bot startup resilience.

The bot once died at startup because post_init's set_my_commands (a cosmetic
Telegram call) timed out, the exception was unhandled, and nothing restarted it.
This pins that a failing set_my_commands no longer aborts startup — the alert
poll + backup jobs still get scheduled.

Run in-container:  docker compose exec -T minder python /app/test_bot_resilience.py
"""

import asyncio

import bot


class _FakeBot:
    async def set_my_commands(self, _commands):
        raise TimeoutError("simulated telegram.error.TimedOut")


class _FakeJobQueue:
    def __init__(self):
        self.scheduled = []

    def run_repeating(self, fn, interval, first):
        self.scheduled.append(fn.__name__)


class _FakeApp:
    def __init__(self):
        self.bot = _FakeBot()
        self.job_queue = _FakeJobQueue()


def test_post_init_survives_set_my_commands_failure():
    app = _FakeApp()
    asyncio.run(bot.post_init(app))  # must NOT raise even though set_my_commands does
    # the critical startup work still happened
    assert "poll_alerts" in app.job_queue.scheduled, app.job_queue.scheduled
    assert "daily_backup" in app.job_queue.scheduled, app.job_queue.scheduled
    print("ok  post_init survives set_my_commands timeout + still schedules jobs")


if __name__ == "__main__":
    test_post_init_survives_set_my_commands_failure()
    print("\nALL BOT-RESILIENCE TESTS PASSED")
