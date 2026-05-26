"""IntentObserver — tracks drift from the original goal per agent.

Created per-agent per-run. The compiler calls observe() after each
agent step and acts on the DriftResult based on the configured strategy.

Important: only agent *reasoning* output should be passed to observe().
Tool responses, error passthroughs (e.g. "Error: ...", "Tool error: ..."),
and intermediate system messages are NOT suitable inputs — they do not
represent the agent's reasoning trajectory and would distort the drift
signal. The compiler enforces this by filtering before calling observe().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from swarmkit_runtime.drift._config import IntentMonitoringConfig
from swarmkit_runtime.drift._embeddings import cosine_similarity, embed


@dataclass(frozen=True)
class DriftResult:
    """Result of a drift observation."""

    score: float
    threshold: float
    exceeded: bool
    action_taken: Literal["log", "warn", "nudge"] | None


class IntentObserver:
    """Tracks semantic drift from the original goal for one agent.

    Usage:
        observer = IntentObserver(config)
        observer.set_anchor("Review code for security issues")
        result = observer.observe(step=1, output="Found SQL injection in auth.py")
        if result.exceeded:
            # apply strategy (log/warn/nudge)
    """

    def __init__(self, config: IntentMonitoringConfig) -> None:
        self._config = config
        self._anchor_embedding: list[float] | None = None
        self._anchor_text: str = ""
        self._history: list[DriftResult] = []

    @property
    def config(self) -> IntentMonitoringConfig:
        return self._config

    @property
    def history(self) -> list[DriftResult]:
        return list(self._history)

    @property
    def anchor_text(self) -> str:
        return self._anchor_text

    def set_anchor(self, goal: str) -> None:
        """Set the reference goal that drift is measured against."""
        self._anchor_text = goal
        self._anchor_embedding = embed(goal)

    def observe(self, step: int, output: str) -> DriftResult:
        """Observe an agent step's output and compute drift from the anchor.

        Returns a DriftResult with the score and whether the threshold
        was exceeded. The caller is responsible for acting on the result.
        """
        if not self._config.enabled or self._anchor_embedding is None:
            result = DriftResult(
                score=0.0,
                threshold=self._config.threshold,
                exceeded=False,
                action_taken=None,
            )
            self._history.append(result)
            return result

        output_embedding = embed(output)
        similarity = cosine_similarity(self._anchor_embedding, output_embedding)
        drift_score = 1.0 - similarity

        exceeded = drift_score > self._config.threshold
        action = self._config.on_drift if exceeded else None

        result = DriftResult(
            score=round(drift_score, 4),
            threshold=self._config.threshold,
            exceeded=exceeded,
            action_taken=action,
        )
        self._history.append(result)
        return result

    def get_nudge_message(self) -> str:
        """Generate a nudge message reminding the agent of the original goal."""
        return (
            f"You are drifting from your original goal. "
            f"Refocus on: {self._anchor_text}\n"
            f"Do not introduce unrelated topics or tasks."
        )
