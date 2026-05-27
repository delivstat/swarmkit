"""Cron trigger scheduler for SwarmKit serve mode.

Reads cron trigger configs and fires topology runs on schedule.
See design §14.1 (persistent/scheduled mode) and §5.4 (triggers).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("swarmkit.triggers.scheduler")

# Callable type: receives topology name and a trigger source label.
FireFn = Callable[[str, str], Awaitable[None]]

_POLL_INTERVAL_SECONDS = 30
_croniter_available = importlib.util.find_spec("croniter") is not None


class TriggerScheduler:
    """Asyncio-based cron scheduler for workspace triggers.

    Parameters
    ----------
    triggers:
        Raw trigger config dicts. Each dict must have at minimum:
        ``type`` (str), ``targets`` (list[str]).
        Cron triggers additionally need ``config.expression`` (str).
    fire_fn:
        Async callback invoked when a trigger fires.
        Signature: ``async def fire(topology_name: str, source: str) -> None``.
    poll_interval:
        How often (in seconds) the scheduler checks for due triggers.
        Defaults to 30 s.
    """

    def __init__(
        self,
        triggers: list[dict[str, Any]],
        fire_fn: FireFn,
        *,
        poll_interval: int = _POLL_INTERVAL_SECONDS,
    ) -> None:
        self._triggers = triggers
        self._fire_fn = fire_fn
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        # Maps trigger_id -> last fire time (UTC-aware)
        self._last_fired: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background polling loop."""
        if not _croniter_available:
            logger.warning(
                "croniter is not installed; cron triggers will be skipped. "
                "Install with: pip install 'swarmkit-runtime[serve]'"
            )
        self._task = asyncio.create_task(self._loop(), name="trigger-scheduler")
        logger.info(
            "TriggerScheduler started (poll_interval=%ds, %d trigger(s) configured)",
            self._poll_interval,
            len(self._triggers),
        )

    async def stop(self) -> None:
        """Cancel the background polling loop and wait for it to finish."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        logger.info("TriggerScheduler stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            now = datetime.now(UTC)
            for trigger in self._triggers:
                try:
                    await self._maybe_fire(trigger, now)
                except Exception:
                    logger.warning(
                        "Error evaluating trigger %r",
                        trigger.get("id", "<unknown>"),
                        exc_info=True,
                    )
            await asyncio.sleep(self._poll_interval)

    async def _maybe_fire(self, trigger: dict[str, Any], now: datetime) -> None:
        trigger_type = trigger.get("type", "")
        trigger_id = trigger.get("id", "")
        enabled = trigger.get("enabled", True)

        if not enabled:
            return

        if trigger_type != "cron":
            # Only cron triggers are scheduled; webhook/manual/etc. fire on demand.
            return

        if not _croniter_available:
            return

        config = trigger.get("config") or {}
        expression = config.get("expression", "")
        if not expression:
            logger.warning("Cron trigger %r has no expression; skipping", trigger_id)
            return

        from croniter import croniter  # type: ignore[import-untyped]  # noqa: PLC0415

        last = self._last_fired.get(trigger_id)
        if last is None:
            # First poll: seed last_fired to now so we don't immediately fire
            # a trigger that was due at some point before the server started.
            self._last_fired[trigger_id] = now
            return

        it = croniter(expression, last)
        next_fire: datetime = it.get_next(datetime)

        if next_fire <= now:
            self._last_fired[trigger_id] = now
            targets: list[str] = trigger.get("targets", [])
            logger.info(
                "Cron trigger %r fired (expression=%r); targets=%r",
                trigger_id,
                expression,
                targets,
            )
            for topology_name in targets:
                try:
                    await self._fire_fn(topology_name, f"trigger:{trigger_id}")
                except Exception:
                    logger.warning(
                        "fire_fn failed for topology %r (trigger %r)",
                        topology_name,
                        trigger_id,
                        exc_info=True,
                    )


__all__ = ["TriggerScheduler"]
