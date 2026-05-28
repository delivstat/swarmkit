"""Memory gate integration for the compiler.

Called alongside decision skill gates at pre_input and post_output
trigger points. Handles memory-reader (context injection) and
memory-writer (insight extraction) without going through the
governance decision evaluator.

See design/details/workspace-memory.md.
"""

from __future__ import annotations

import logging
from typing import Any

from swarmkit_runtime.governance import DecisionSkillBinding, DecisionSkillResult
from swarmkit_runtime.memory._hooks import extract_and_save, retrieve_context
from swarmkit_runtime.memory._store import MemoryStore
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol

logger = logging.getLogger("swarmkit.memory")

MEMORY_READER_ID = "memory-reader"
MEMORY_WRITER_ID = "memory-writer"


def is_memory_binding(binding: DecisionSkillBinding) -> bool:
    return binding.id in (MEMORY_READER_ID, MEMORY_WRITER_ID)


def get_memory_config(bindings: list[DecisionSkillBinding], skill_id: str) -> dict[str, Any] | None:
    for b in bindings:
        if b.id == skill_id:
            return b.config
    return None


async def memory_pre_input(
    *,
    agent_id: str,
    user_input: str,
    bindings: list[DecisionSkillBinding],
    store: MemoryStore,
    user: str | None = None,
) -> str | None:
    """Search memory and return context to inject, or None."""
    reader = next(
        (
            b
            for b in bindings
            if b.id == MEMORY_READER_ID and b.trigger == "pre_input" and b.applies_to(agent_id)
        ),
        None,
    )
    if reader is None:
        return None

    context = retrieve_context(
        user_input=user_input,
        user=user,
        store=store,
        config=reader.config,
    )

    if context:
        logger.info(
            "Memory context injected for agent=%s (user=%s, query=%s...)",
            agent_id,
            user,
            user_input[:50],
        )
    return context


async def memory_post_output(
    *,
    agent_id: str,
    user_input: str,
    agent_output: str,
    bindings: list[DecisionSkillBinding],
    store: MemoryStore,
    model_provider: ModelProviderProtocol,
    model_name: str,
    session_id: str | None = None,
    user: str | None = None,
) -> DecisionSkillResult:
    """Extract insights from output and save to memory. Always returns pass."""
    writer = next(
        (
            b
            for b in bindings
            if b.id == MEMORY_WRITER_ID and b.trigger == "post_output" and b.applies_to(agent_id)
        ),
        None,
    )
    if writer is None:
        return DecisionSkillResult(
            skill_id=MEMORY_WRITER_ID,
            verdict="pass",
            confidence=1.0,
            reasoning="No memory-writer binding for this agent",
        )

    entry = await extract_and_save(
        user_input=user_input,
        agent_output=agent_output,
        agent_id=agent_id,
        session_id=session_id,
        user=user,
        store=store,
        model_provider=model_provider,
        model_name=model_name,
        config=writer.config,
    )

    if entry:
        return DecisionSkillResult(
            skill_id=MEMORY_WRITER_ID,
            verdict="pass",
            confidence=1.0,
            reasoning=f"Saved memory: {entry.topic}",
            raw={"memory_saved": True, "topic": entry.topic, "memory_id": entry.id},
        )

    return DecisionSkillResult(
        skill_id=MEMORY_WRITER_ID,
        verdict="pass",
        confidence=1.0,
        reasoning="Turn not worth saving to memory",
        raw={"memory_saved": False},
    )


__all__ = [
    "MEMORY_READER_ID",
    "MEMORY_WRITER_ID",
    "is_memory_binding",
    "memory_post_output",
    "memory_pre_input",
]
