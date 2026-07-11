"""Executor abstraction — the node-execution provider seam (design/details/executor-abstraction.md).

`model` (default, today's behavior) and, from P2, `harness`. Public surface:

- :class:`Executor` — a registered executor kind (owns its config validation).
- :class:`ModelExecutor` — the default kind.
- :class:`ExecutorRegistry` / :func:`default_executor_registry` — resolve `executor.kind`.
- :class:`ResolvedExecutor` — the resolved result carried into node execution.
- :class:`ExecutorError` — unknown kind / invalid config.
"""

from swarmkit_runtime.executors._model import ModelExecutor
from swarmkit_runtime.executors._protocol import Executor, ExecutorError, ResolvedExecutor
from swarmkit_runtime.executors._registry import ExecutorRegistry, default_executor_registry

__all__ = [
    "Executor",
    "ExecutorError",
    "ExecutorRegistry",
    "ModelExecutor",
    "ResolvedExecutor",
    "default_executor_registry",
]
