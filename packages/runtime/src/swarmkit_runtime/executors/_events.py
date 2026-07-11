"""Normalized executor events — ``ExecEvent`` (design executor-abstraction §5.1).

Every executor (adapter) translates its vendor's native stream into this vocabulary. The cockpit,
cost meter, OTel tracer, audit log, and checkpoint store consume **only** these — a new harness is
observed identically to the last, and to a ``model`` node. Frozen dataclasses; a union alias
``ExecEvent`` covers them all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

# exec.result terminal statuses (§5.1 / §6.1). "success" requires typed output + an artifact
# manifest matching the declared profile — never the exit code alone.
ExecResultStatus = Literal[
    "success",
    "failure",
    "budget_exceeded",
    "cancelled",
    "needs_approval",
    "stalled",
]


@dataclass(frozen=True)
class ExecStarted:
    """Run begun — the resolved config the adapter actually launched with."""

    run_id: str
    kind: str
    ref: str | None = None
    config_hash: str | None = None


@dataclass(frozen=True)
class ExecMessage:
    """An assistant/user/system message or thought summary the vendor exposes."""

    role: str
    text: str


@dataclass(frozen=True)
class ExecToolCall:
    """A tool invocation inside the harness (its own tools, not SwarmKit skills)."""

    tool: str
    input_summary: str = ""
    status: str = ""


@dataclass(frozen=True)
class ExecArtifact:
    """A produced artifact — the node's output surface (a diff, a file, a media asset, a record)."""

    artifact_kind: Literal["file_change", "media", "structured"]
    path: str | None = None
    ref: str | None = None
    mime: str | None = None


@dataclass(frozen=True)
class ExecUsage:
    """Unit-typed consumption. Tokens for LLM harnesses; vendor-native units otherwise. ``cost_usd``
    is nullable — vendor-reported when present, else computed downstream from the price table."""

    unit: str = "tokens"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    amount: float | None = None  # for non-token units
    cost_usd: float | None = None


@dataclass(frozen=True)
class ExecApprovalRequested:
    """A *permission* question ("may I?") — a capability outside the launch grant (§6.2)."""

    run_id: str
    capability: str
    rationale: str | None = None


@dataclass(frozen=True)
class ExecApprovalResponse:
    """Resolution of an approval request; scoped to the single action."""

    granted: bool
    responder: Literal["policy", "operator"]
    scope: str = "this-action-only"


@dataclass(frozen=True)
class ExecInputRequested:
    """A *judgment* question ("what do you want?") — a domain decision (§6.3)."""

    question: str
    options: Sequence[str] = field(default_factory=tuple)
    free_text_allowed: bool = True
    question_class: str | None = None


@dataclass(frozen=True)
class ExecInputResponse:
    """Answer to an input request; may be memoized for re-runs."""

    answer: str
    responder: Literal["lead", "operator", "memoized"]


@dataclass(frozen=True)
class ExecResult:
    """Terminal event. ``status`` derives from the structured result, not the exit code."""

    status: ExecResultStatus
    output: Any = None
    artifacts: Sequence[ExecArtifact] = field(default_factory=tuple)
    exit_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecRaw:
    """Passthrough of an untranslated vendor line — retained when telemetry.retain_raw."""

    line: str


ExecEvent = (
    ExecStarted
    | ExecMessage
    | ExecToolCall
    | ExecArtifact
    | ExecUsage
    | ExecApprovalRequested
    | ExecApprovalResponse
    | ExecInputRequested
    | ExecInputResponse
    | ExecResult
    | ExecRaw
)
