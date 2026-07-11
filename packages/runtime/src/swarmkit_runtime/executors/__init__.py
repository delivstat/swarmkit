"""Executor abstraction — the node-execution provider seam (design/details/executor-abstraction.md).

`model` (default, today's behavior) and, from P2, `harness`. Public surface:

- :class:`Executor` — a registered executor kind (config validation + the P2 execution hooks).
- :class:`ModelExecutor` — the default kind.
- :class:`ExecutorRegistry` / :func:`default_executor_registry` — resolve `executor.kind`.
- :class:`ResolvedExecutor` — the resolved result carried into node execution.
- :class:`ExecutorError` — unknown kind / invalid config.
- ``ExecEvent`` + variants — the normalized event vocabulary adapters emit (§5.1).
- :class:`TaskSpec` / :class:`SandboxHandle` / :class:`BudgetEnvelope` / :class:`PreflightReport` /
  :class:`ResumeToken` — the run-side value types (§5, §6.0).
"""

from swarmkit_runtime.executors._adapter_spec import AdapterSpec, parse_adapter_spec
from swarmkit_runtime.executors._budget import enforce_budget
from swarmkit_runtime.executors._declarative import DeclarativeExecutor, load_adapter_specs
from swarmkit_runtime.executors._event_map import AdapterInterpreter, build_command
from swarmkit_runtime.executors._events import (
    ExecApprovalRequested,
    ExecApprovalResponse,
    ExecArtifact,
    ExecEvent,
    ExecInputRequested,
    ExecInputResponse,
    ExecMessage,
    ExecRaw,
    ExecResult,
    ExecResultStatus,
    ExecStarted,
    ExecToolCall,
    ExecUsage,
)
from swarmkit_runtime.executors._model import ModelExecutor
from swarmkit_runtime.executors._protocol import Executor, ExecutorError, ResolvedExecutor
from swarmkit_runtime.executors._registry import ExecutorRegistry, default_executor_registry
from swarmkit_runtime.executors._run import (
    BudgetEnvelope,
    PreflightReport,
    ResumeToken,
    SandboxHandle,
    TaskSpec,
)
from swarmkit_runtime.executors._sandbox import SandboxError, collect_diff, worktree_sandbox

__all__ = [
    "AdapterInterpreter",
    "AdapterSpec",
    "BudgetEnvelope",
    "DeclarativeExecutor",
    "ExecApprovalRequested",
    "ExecApprovalResponse",
    "ExecArtifact",
    "ExecEvent",
    "ExecInputRequested",
    "ExecInputResponse",
    "ExecMessage",
    "ExecRaw",
    "ExecResult",
    "ExecResultStatus",
    "ExecStarted",
    "ExecToolCall",
    "ExecUsage",
    "Executor",
    "ExecutorError",
    "ExecutorRegistry",
    "ModelExecutor",
    "PreflightReport",
    "ResolvedExecutor",
    "ResumeToken",
    "SandboxError",
    "SandboxHandle",
    "TaskSpec",
    "build_command",
    "collect_diff",
    "default_executor_registry",
    "enforce_budget",
    "load_adapter_specs",
    "parse_adapter_spec",
    "worktree_sandbox",
]
