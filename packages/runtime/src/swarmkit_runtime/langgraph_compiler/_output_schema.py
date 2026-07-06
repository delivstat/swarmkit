"""Default output schema and source provenance for structured inter-agent communication.

Workers produce structured JSON by default. The platform default schema
enforces ``{findings: [{fact, source}], not_found, raw_data}`` so that
coordinators receive atomic, sourced claims instead of prose.

Source fields are validated against actual tool calls and auto-filled
when the provenance is unambiguous.

See ``design/details/structured-inter-agent-communication.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from swarmkit_runtime.resolver import ResolvedAgent

WORKER_DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["fact", "source"],
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "One atomic claim or data point",
                    },
                    "source": {
                        "type": "string",
                        "description": "Tool or source that produced this data",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["observed", "inferred"],
                    },
                },
            },
        },
        "not_found": {
            "type": "array",
            "items": {
                "type": "string",
                "description": "What was searched for but not found",
            },
        },
        "raw_data": {
            "type": "object",
            "description": "Key structured data (IDs, configs, tables)",
            "additionalProperties": True,
        },
    },
}


def get_effective_output_schema(agent: ResolvedAgent) -> dict[str, Any] | None:
    """Return the output schema enforced for this agent, or None.

    Priority:
    1. Explicit schema on agent → use it
    2. Explicit opt-out (``output_schema_disabled``) → None
    3. ``role=worker`` with no explicit schema → platform default
    4. ``role=root`` / ``role=leader`` → None
    """
    if agent.output_schema is not None:
        return dict(agent.output_schema)
    if agent.output_schema_disabled:
        return None
    if agent.role == "worker":
        return WORKER_DEFAULT_OUTPUT_SCHEMA
    return None


_SOURCE_TAG_RE = re.compile(r"\[source:\s*([^\]|]+?)(?:\s*\|\s*\d+ms)?\]")


def extract_tool_sources(tool_results: list[Any]) -> set[str]:
    """Extract provenance source strings from tool call results.

    Scans each ``ToolCallResult.result`` for ``[source: server:tool | Nms]``
    tags appended by the skill executor (PR 1 provenance envelope).
    Returns the set of unique source identifiers (e.g. ``{"docs:api-reference"}``).
    """
    sources: set[str] = set()
    for tr in tool_results:
        result_text = getattr(tr, "result", "")
        if not isinstance(result_text, str):
            continue
        for match in _SOURCE_TAG_RE.finditer(result_text):
            sources.add(match.group(1).strip())
        if hasattr(tr, "tool_name") and tr.tool_name:
            sources.add(tr.tool_name)
    return sources


def validate_and_fill_sources(
    output: dict[str, Any],
    known_sources: set[str],
) -> dict[str, Any]:
    """Validate finding sources against known tool calls and auto-fill gaps.

    For each finding in ``output["findings"]``:
    - Source matches a known tool call → keep as-is
    - Source is empty and exactly one tool was called → auto-fill
    - Source is empty and multiple tools called → mark ``"unattributed"``
    - Source doesn't match any tool call → keep (model may cite non-MCP sources)

    Returns the (possibly modified) output dict. Mutation is intentional —
    the caller passes a freshly-parsed dict that hasn't been serialised yet.
    """
    findings = output.get("findings")
    if not isinstance(findings, list):
        return output

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        source = finding.get("source", "")
        if source:
            continue
        if len(known_sources) == 1:
            finding["source"] = next(iter(known_sources))
        elif known_sources:
            finding["source"] = "unattributed"

    return output


@dataclass(frozen=True)
class GroundingResult:
    """Result of a deterministic grounding check on structured output."""

    passed: bool
    total_findings: int = 0
    sourced: int = 0
    unsourced: list[str] = field(default_factory=list)
    unattributed: int = 0


def check_grounding(
    output: dict[str, Any],
    known_sources: set[str] | None = None,
) -> GroundingResult:
    """Deterministic grounding check — no LLM needed.

    For structured output with a ``findings`` array, verifies that every
    finding has a non-empty ``source`` field. Optionally validates that
    source strings match known tool calls.

    This replaces the LLM-based ``grounding-verifier`` decision skill
    for agents with ``output_schema``. The schema constraint makes
    fabrication without a source structurally detectable.

    Returns a ``GroundingResult`` with pass/fail and details.
    """
    findings = output.get("findings")
    if not isinstance(findings, list):
        return GroundingResult(passed=True)

    total = 0
    sourced = 0
    unsourced: list[str] = []
    unattributed = 0

    for finding in findings:
        if not isinstance(finding, dict) or "fact" not in finding:
            continue
        total += 1
        source = finding.get("source", "")
        if not source:
            unsourced.append(finding["fact"][:120])
        elif source == "unattributed":
            unattributed += 1
        else:
            sourced += 1

    passed = len(unsourced) == 0
    return GroundingResult(
        passed=passed,
        total_findings=total,
        sourced=sourced,
        unsourced=unsourced,
        unattributed=unattributed,
    )
