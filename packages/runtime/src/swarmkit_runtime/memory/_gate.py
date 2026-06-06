"""Memory gate integration for the compiler.

Called alongside decision skill gates at pre_input and post_output
trigger points. Handles memory-reader (context injection) and
memory-writer (insight extraction). Supports both MemoryStore
(local JSON + TF-IDF) and GBrainMemory (MCP-backed graph).

See design/details/workspace-memory.md.
"""

from __future__ import annotations

import logging
from typing import Any

from swarmkit_runtime.governance import DecisionSkillBinding, DecisionSkillResult
from swarmkit_runtime.memory._hooks import extract_and_save, retrieve_context
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol

logger = logging.getLogger("swarmkit.memory")

MEMORY_READER_ID = "memory-reader"
MEMORY_WRITER_ID = "memory-writer"

_CONTEXT_TEMPLATE = """\
WORKSPACE MEMORY — relevant prior conversations for this user:

%s

Use this context naturally. Reference prior conversations when relevant \
("As we discussed previously..." or "Building on what you shared about..."). \
Do not explicitly mention "memory" or "database" — treat it as your own recollection."""


def _is_gbrain(store: Any) -> bool:
    from swarmkit_runtime.memory._gbrain import GBrainMemory  # noqa: PLC0415

    return isinstance(store, GBrainMemory)


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
    store: Any,
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

    cfg = reader.config or {}

    if _is_gbrain(store):
        context = await _gbrain_retrieve(
            user_input=user_input,
            user=user,
            store=store,
            config=cfg,
        )
    else:
        context = retrieve_context(
            user_input=user_input,
            user=user,
            store=store,
            config=cfg,
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
    store: Any,
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

    if _is_gbrain(store):
        result = await _gbrain_extract_and_save(
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
        if result:
            return DecisionSkillResult(
                skill_id=MEMORY_WRITER_ID,
                verdict="pass",
                confidence=1.0,
                reasoning=f"Saved memory to GBrain: {result.get('topic', 'unknown')}",
                raw={"memory_saved": True, "backend": "gbrain", **result},
            )
    else:
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


# ---- GBrain-specific implementations ----


async def _gbrain_retrieve(
    *,
    user_input: str,
    user: str | None,
    store: Any,
    config: dict[str, Any],
) -> str | None:
    """Search GBrain memory for relevant prior context."""
    max_results = config.get("max_results", 5)

    try:
        results = await store.search_memory(
            user_input,
            user=user,
            max_results=max_results,
        )
    except Exception:
        logger.warning("GBrain memory search failed", exc_info=True)
        return None

    if not results:
        return None

    blocks: list[str] = []
    for item in results:
        content = item.get("content", "") if isinstance(item, dict) else str(item)
        if content:
            blocks.append(content[:500])

    if not blocks:
        return None

    combined = "\n\n---\n\n".join(blocks)
    return _CONTEXT_TEMPLATE % combined


async def _gbrain_extract_and_save(
    *,
    user_input: str,
    agent_output: str,
    agent_id: str,
    session_id: str | None,
    user: str | None,
    store: Any,
    model_provider: ModelProviderProtocol,
    model_name: str,
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract insights and save to GBrain memory."""
    import json  # noqa: PLC0415

    cfg = config or {}
    min_output_length = cfg.get("min_output_length", 50)

    if len(agent_output) < min_output_length:
        return None

    from swarmkit_runtime.memory._hooks import _EXTRACT_PROMPT  # noqa: PLC0415
    from swarmkit_runtime.model_providers import CompletionRequest  # noqa: PLC0415
    from swarmkit_runtime.model_providers._types import Message  # noqa: PLC0415

    prompt = _EXTRACT_PROMPT % (user_input, agent_output)

    try:
        response = await model_provider.complete(
            CompletionRequest(
                model=model_name,
                messages=[Message(role="user", content=prompt)],
                temperature=0.0,
                max_tokens=500,
            )
        )
        text = response.text.strip()

        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()

        data = json.loads(text)

        if not data.get("worth_saving", True):
            logger.debug("GBrain memory extraction: not worth saving (agent=%s)", agent_id)
            return None

        topic = data.get("topic", "")
        tags = data.get("tags", [])
        key_points = data.get("key_points", [])
        context = data.get("context", "")

        slug = await store.save_memory(
            topic=topic,
            context=context,
            key_points=key_points,
            tags=tags,
            user=user,
            session_id=session_id,
            agent_id=agent_id,
        )

        return {"topic": topic, "slug": slug, "tags": tags}

    except Exception:
        logger.warning("GBrain memory extraction failed for agent=%s", agent_id, exc_info=True)
        return None


__all__ = [
    "MEMORY_READER_ID",
    "MEMORY_WRITER_ID",
    "is_memory_binding",
    "memory_post_output",
    "memory_pre_input",
]
