"""Helper utilities shared across compiler modules.

Small functions for progress logging, text extraction, truncation,
governance checks, trust checks, audit recording, and JSON parsing.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.model_providers import CompletionResponse, ContentBlock, Message
from swarmkit_runtime.resolver import ResolvedAgent
from swarmkit_runtime.telemetry import record_governance_decision

_MAX_RESULT_CHARS = int(os.environ.get("SWARMKIT_MAX_RESULT_CHARS", "3000"))
_TRUST_DENY_THRESHOLD = 0.2


@dataclass
class ToolCallResult:
    """A single tool call and its result."""

    tool_use_id: str
    tool_name: str
    result: str
    image_blocks: list[ContentBlock] = field(default_factory=list)


# Progress listeners are scoped to the current async context, NOT process-global — so
# concurrent `serve` conversations don't cross-emit each other's progress. A request task
# registers its listener via `progress_listener(...)`; `asyncio.create_task` copies the
# context, so the run inside sees only that conversation's listener.
_progress_listeners_var: ContextVar[tuple[Callable[[str], None], ...]] = ContextVar(
    "_progress_listeners", default=()
)


@contextlib.contextmanager
def progress_listener(callback: Callable[[str], None]) -> Iterator[None]:
    """Register a progress listener for the current context (and tasks it spawns), then
    remove it on exit. Isolated per async context — see `_progress_listeners_var`."""
    token = _progress_listeners_var.set((*_progress_listeners_var.get(), callback))
    try:
        yield
    finally:
        _progress_listeners_var.reset(token)


def _progress(msg: str) -> None:
    """Print user-facing progress. Always shown unless SWARMKIT_QUIET is set."""
    if not os.environ.get("SWARMKIT_QUIET"):
        print(msg, file=sys.stderr)
    for listener in _progress_listeners_var.get():
        with contextlib.suppress(Exception):
            listener(msg)


def _log_verbose_request(
    agent_id: str,
    model_name: str,
    tools: list[Any],
    messages: list[Message],
) -> None:
    """Log request details when SWARMKIT_VERBOSE is set."""
    print(f"\n--- [{agent_id}] calling {model_name} ---", file=sys.stderr)
    print(f"  tools: {[t.name for t in tools]}", file=sys.stderr)
    print(f"  input: {messages[-1].content[:200]}...", file=sys.stderr)


def _log_verbose_response(response: CompletionResponse) -> None:
    """Log response details when SWARMKIT_VERBOSE is set."""
    tool_calls = [b.tool_name for b in response.content if hasattr(b, "tool_name") and b.tool_name]
    text_parts = [b.text[:100] for b in response.content if hasattr(b, "text") and b.text]
    print(f"  tool_calls: {tool_calls}", file=sys.stderr)
    print(f"  text: {text_parts}", file=sys.stderr)


def _truncate_result(text: str, max_chars: int = 0) -> str:
    """Truncate a tool result to keep context manageable.

    Keeps the first and last portions so the model sees both the
    beginning (often headers/structure) and end (often summary/totals).
    """
    limit = max_chars or _MAX_RESULT_CHARS
    if len(text) <= limit:
        return text
    half = limit // 2
    return (
        text[:half]
        + f"\n\n... ({len(text)} chars total, truncated for context) ...\n\n"
        + text[-half:]
    )


def _make_result(agent_id: str, result_text: str) -> dict[str, Any]:
    """Build the standard return dict for a completed agent."""
    return {
        "current_agent": agent_id,
        "agent_results": {agent_id: result_text},
        "messages": [AIMessage(content=result_text, name=agent_id)],
        "output": result_text,
    }


def _safe_parse_json(tool_name: str, response: Any, agent: Any) -> dict[str, object]:
    """Extract tool call inputs from the response for audit logging."""
    for block in getattr(response, "content", ()):
        if getattr(block, "tool_name", None) == tool_name:
            tool_input = getattr(block, "tool_input", None)
            if isinstance(tool_input, dict):
                return dict(tool_input)
            if isinstance(tool_input, str):
                try:
                    parsed = json.loads(tool_input)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    pass
                return {"raw_input": tool_input[:1000]}
    return {}


def _extract_text(response: CompletionResponse) -> str:
    parts: list[str] = []
    for block in response.content:
        if block.type == "text" and block.text:
            parts.append(block.text)
    return "\n".join(parts) or "(no response)"


async def _check_governance(
    agent_id: str,
    agent: ResolvedAgent,
    governance: GovernanceProvider,
) -> dict[str, Any] | None:
    """Evaluate governance policy; return denial state dict or None if allowed."""
    iam = agent.iam or {}
    scopes_required = frozenset(iam.get("base_scope", []))

    decision = await governance.evaluate_action(
        agent_id=agent_id,
        action="agent:execute",
        scopes_required=scopes_required,
    )
    record_governance_decision(
        decision="allow" if decision.allowed else "deny", scope="agent:execute"
    )

    if not decision.allowed:
        await governance.record_event(
            AuditEvent(
                event_type="policy.denied",
                agent_id=agent_id,
                timestamp=datetime.now(tz=UTC),
                payload={
                    "action": "agent:execute",
                    "reason": decision.reason,
                    "scopes_denied": sorted(decision.scopes_denied),
                },
            )
        )
        return {
            "current_agent": agent_id,
            "agent_results": {agent_id: f"DENIED: {decision.reason}"},
            "messages": [
                AIMessage(
                    content=f"[{agent_id}] DENIED: {decision.reason}",
                    name=agent_id,
                )
            ],
            "output": f"DENIED: {decision.reason}",
        }
    return None


async def _check_trust(
    agent_id: str,
    agent: ResolvedAgent,
    governance: GovernanceProvider,
) -> dict[str, Any] | None:
    """Evaluate trust score; return denial state dict or None if trusted."""
    trust = await governance.get_trust_score(agent_id=agent_id)
    if trust.score < _TRUST_DENY_THRESHOLD:
        await governance.record_event(
            AuditEvent(
                event_type="trust.denied",
                agent_id=agent_id,
                timestamp=datetime.now(tz=UTC),
                payload={
                    "score": trust.score,
                    "tier": trust.tier,
                },
            )
        )
        return {
            "current_agent": agent_id,
            "agent_results": {agent_id: f"DENIED: trust score {trust.score} below threshold"},
            "messages": [
                AIMessage(
                    content=f"[{agent_id}] DENIED: trust score too low ({trust.score})",
                    name=agent_id,
                )
            ],
            "output": f"DENIED: trust score {trust.score} below threshold",
        }
    if trust.tier == "degraded":
        await governance.record_event(
            AuditEvent(
                event_type="trust.degraded",
                agent_id=agent_id,
                timestamp=datetime.now(tz=UTC),
                payload={"score": trust.score, "tier": trust.tier},
            )
        )
    return None


async def _record_completion(
    governance: GovernanceProvider,
    agent_id: str,
    role: str,
    result_text: str,
    start: datetime,
) -> None:
    """Record the agent.completed audit event."""
    end = datetime.now(tz=UTC)
    duration_ms = int((end - start).total_seconds() * 1000)
    await governance.record_event(
        AuditEvent(
            event_type="agent.completed",
            agent_id=agent_id,
            timestamp=end,
            payload={
                "result_length": len(result_text),
                "duration_ms": duration_ms,
                "role": role,
            },
            topology_id=agent_id,
        )
    )
