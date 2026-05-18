"""Default output schema for structured inter-agent communication.

Workers produce structured JSON by default. The platform default schema
enforces ``{findings: [{fact, source}], not_found, raw_data}`` so that
coordinators receive atomic, sourced claims instead of prose.

See ``design/details/structured-inter-agent-communication.md``.
"""

from __future__ import annotations

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
