"""ModelExecutor — the default executor kind (design/details/executor-abstraction.md §4.2).

A direct model completion: exactly today's behavior, now named behind the executor seam. Its
``config`` is the model-call params (temperature, etc.), kept permissive since the model config is
not tightly schema-constrained.
"""

from __future__ import annotations

from typing import Any

from swarmkit_runtime.executors._protocol import Executor


class ModelExecutor(Executor):
    """The default executor: a chat-completion call to the archetype's configured model."""

    kind = "model"

    def config_schema(self) -> dict[str, Any]:
        # Model-call params are open (temperature, top_p, provider-specific knobs).
        return {"type": "object", "additionalProperties": True}
