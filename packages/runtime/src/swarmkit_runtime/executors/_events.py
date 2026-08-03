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


# A tool call's outcome, normalized across harnesses. Every vendor spells this differently —
# opencode says "completed"/"error", claude-code reports it out-of-band as `is_error` on a later
# tool_result, codex as an exit code on exec_command_end, gemini as its own status enum. Downstream
# consumers (trace, cockpit, cost meter) must not each learn four vocabularies, so adapters may emit
# whatever their vendor says and `normalize_tool_status` collapses it here.
#
# "" means *unknown*, and is deliberately distinct from "ok": a harness that never reports outcomes
# must not have its silence read as success. That conflation is what let a `view_image` that
# returned nothing render in the trace as a healthy call.
ExecToolStatus = Literal["", "ok", "error"]

_TOOL_STATUS_OK = frozenset({"ok", "success", "succeeded", "completed", "complete", "done"})
_TOOL_STATUS_ERROR = frozenset(
    {
        "error",
        "errored",
        "failure",
        "failed",
        "denied",
        "rejected",
        "timeout",
        "timed_out",
        "cancelled",
        "canceled",
        "aborted",
    }
)
# Deliberately mapped to unknown rather than ok — a call still in flight has no outcome yet.
_TOOL_STATUS_PENDING = frozenset({"pending", "running", "in_progress", "started", "begin"})


def normalize_tool_status(raw: str) -> ExecToolStatus:
    """Collapse a vendor's tool-outcome word into ``ok`` / ``error`` / ``""`` (unknown).

    Unrecognized values normalize to unknown, not to either pole: a harness whose vocabulary we have
    not learned yet should show up as *unreported*, never as a confident success or a false alarm.
    """
    token = raw.strip().lower().replace("-", "_")
    if token in _TOOL_STATUS_OK:
        return "ok"
    if token in _TOOL_STATUS_ERROR:
        return "error"
    if token in _TOOL_STATUS_PENDING or not token:
        return ""
    return ""


@dataclass(frozen=True)
class ExecToolCall:
    """A tool invocation inside the harness (its own tools, not SwarmKit skills).

    ``status`` is the normalized outcome (``ExecToolStatus``), not the vendor's raw word — see
    ``normalize_tool_status``. It is ``""`` when the harness reported no outcome.
    """

    tool: str
    input_summary: str = ""
    status: ExecToolStatus = ""


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
