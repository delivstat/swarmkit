"""Output governance: schema validation and auto-correction.

Validates agent output against JSON Schema, retries with targeted
correction prompts, and falls back to human review on exhaustion.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.model_providers import CompletionRequest, CompletionResponse, Message
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.resolver import ResolvedAgent
from swarmkit_runtime.review._hitl import prompt_human_review
from swarmkit_runtime.skills._output_validator import (
    format_correction_prompt,
    validate_all_skill_output,
)

from ._helpers import _extract_text

_MAX_OUTPUT_RETRIES = 2


def _get_outputs_schema(agent: ResolvedAgent) -> dict[str, Any] | None:
    """Return the JSON Schema for output validation.

    Priority: agent-level output_schema (archetype/platform default)
    then skill-level outputs.
    """
    from ._output_schema import get_effective_output_schema  # noqa: PLC0415

    effective = get_effective_output_schema(agent)
    if effective is not None:
        return effective

    for skill in agent.skills:
        outputs = getattr(skill.raw, "outputs", None)
        if outputs is not None:
            return dict(outputs) if not isinstance(outputs, dict) else outputs
    return None


async def _validate_and_correct(
    result_text: str,
    outputs_schema: dict[str, Any],
    *,
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str | None,
    messages: list[Message],
    governance: GovernanceProvider,
    agent_id: str,
) -> str:
    """Validate skill output against JSON Schema; re-prompt on failure.

    Tries to parse the result as JSON and validate against the schema.
    On failure, sends field-specific errors back to the model for
    targeted correction (up to ``_MAX_OUTPUT_RETRIES`` attempts).
    """
    for attempt in range(_MAX_OUTPUT_RETRIES + 1):
        try:
            parsed = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            if attempt == _MAX_OUTPUT_RETRIES:
                return result_text
            correction = (
                "Your response must be valid JSON matching the output schema. "
                "Please return a valid JSON object."
            )
            result_text = await _retry_with_correction(
                correction, model_provider, model_name, system_prompt, messages
            )
            continue

        errors = validate_all_skill_output(parsed, outputs_schema)
        if not errors:
            await governance.record_event(
                AuditEvent(
                    event_type="output.validated",
                    agent_id=agent_id,
                    timestamp=datetime.now(tz=UTC),
                    payload={"attempt": attempt + 1, "valid": True},
                )
            )
            return result_text

        if attempt == _MAX_OUTPUT_RETRIES:
            await governance.record_event(
                AuditEvent(
                    event_type="output.validation_failed",
                    agent_id=agent_id,
                    timestamp=datetime.now(tz=UTC),
                    payload={
                        "attempts": attempt + 1,
                        "errors": [{"field": e.field, "message": e.message} for e in errors],
                    },
                )
            )

            if sys.stdin.isatty():
                decision = prompt_human_review(
                    agent_id=agent_id,
                    skill_id="output-validation",
                    output=parsed,
                    verdict=None,
                    reason=f"Validation failed after {attempt + 1} attempts: "
                    + "; ".join(f"{e.field}: {e.message}" for e in errors),
                )
                if decision == "approved":
                    return result_text

            return result_text

        correction = format_correction_prompt(errors)
        result_text = await _retry_with_correction(
            correction, model_provider, model_name, system_prompt, messages
        )

    return result_text


async def _retry_with_correction(
    correction: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str | None,
    messages: list[Message],
) -> str:
    """Re-prompt the model with a correction message."""
    retry_messages = [*messages, Message(role="user", content=correction)]
    response = await model_provider.complete(
        CompletionRequest(
            model=model_name,
            messages=tuple(retry_messages),
            system=system_prompt,
        )
    )
    return _extract_text(response)


async def _finalize_text_result(
    response: CompletionResponse,
    messages: list[Message],
    agent: ResolvedAgent,
    agent_id: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
    system_prompt: str | None,
    governance: GovernanceProvider,
    agent_results: dict[str, Any],
    completed_children: set[str],
) -> str:
    """Validate output, apply child fallback, return final text."""
    result_text = _extract_text(response)
    outputs_schema = _get_outputs_schema(agent)
    if outputs_schema and result_text != "(no response)":
        result_text = await _validate_and_correct(
            result_text,
            outputs_schema,
            model_provider=model_provider,
            model_name=model_name,
            system_prompt=system_prompt,
            messages=messages,
            governance=governance,
            agent_id=agent_id,
        )
        result_text = _auto_fill_sources(result_text, messages)

    if result_text == "(no response)" and completed_children:
        child_texts = [
            str(agent_results[cid]) for cid in completed_children if cid in agent_results
        ]
        result_text = "\n\n".join(child_texts)

    return result_text


def _auto_fill_sources(result_text: str, messages: list[Message]) -> str:
    """Auto-fill empty source fields from tool call provenance in messages."""
    from ._output_schema import (  # noqa: PLC0415
        validate_and_fill_sources,
    )

    try:
        parsed = json.loads(result_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return result_text

    if not isinstance(parsed, dict) or "findings" not in parsed:
        return result_text

    known_sources = _extract_sources_from_messages(messages)
    if not known_sources:
        return result_text

    validate_and_fill_sources(parsed, known_sources)
    return json.dumps(parsed)


def _extract_sources_from_messages(messages: list[Message]) -> set[str]:
    """Scan conversation messages for provenance source tags."""
    from ._output_schema import _SOURCE_TAG_RE  # noqa: PLC0415

    sources: set[str] = set()
    for msg in messages:
        if isinstance(msg.content, str):
            for match in _SOURCE_TAG_RE.finditer(msg.content):
                sources.add(match.group(1).strip())
        else:
            for block in msg.content:
                text = getattr(block, "tool_result", None) or getattr(block, "text", None)
                if isinstance(text, str):
                    for match in _SOURCE_TAG_RE.finditer(text):
                        sources.add(match.group(1).strip())
    return sources
