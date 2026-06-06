"""Memory decision skill hooks — extract insights and retrieve context.

Called by the compiler at pre_input and post_output trigger points
when memory decision skills are configured. The memory-writer extracts
structured insights from agent output via LLM and saves them. The
memory-reader searches for relevant prior context and injects it.

See design/details/workspace-memory.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from swarmkit_runtime.memory._store import MemoryEntry, MemoryStore
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol

logger = logging.getLogger("swarmkit.memory")

_EXTRACT_PROMPT = """\
You are a memory extraction system. Given a conversation turn (user input + agent output), \
extract structured insights worth remembering for future conversations.

Focus on:
- What topic was discussed
- What the user's context/situation is
- Key points that resolved the query or were important
- Emotional state or reaction if relevant
- Tags for semantic retrieval

Return JSON:
{
  "topic": "short topic label",
  "context": "what the user was asking about and why",
  "key_points": ["point 1", "point 2"],
  "tags": ["tag1", "tag2"],
  "worth_saving": true/false
}

Set worth_saving=false for trivial exchanges (greetings, clarifications, \
off-topic) that have no future value. Only save substantive conversations.

USER INPUT:
%s

AGENT OUTPUT:
%s"""

_CONTEXT_TEMPLATE = """\
WORKSPACE MEMORY — relevant prior conversations for this user:

%s

Use this context naturally. Reference prior conversations when relevant \
("As we discussed previously..." or "Building on what you shared about..."). \
Do not explicitly mention "memory" or "database" — treat it as your own recollection."""


async def extract_and_save(
    *,
    user_input: str,
    agent_output: str,
    agent_id: str,
    session_id: str | None,
    user: str | None,
    store: MemoryStore,
    model_provider: ModelProviderProtocol,
    model_name: str,
    config: dict[str, Any] | None = None,
) -> MemoryEntry | None:
    """Extract insights from a conversation turn and save to memory.

    Returns the saved MemoryEntry, or None if the turn wasn't worth saving.
    """
    cfg = config or {}
    min_output_length = cfg.get("min_output_length", 50)

    if len(agent_output) < min_output_length:
        return None

    prompt = _EXTRACT_PROMPT % (user_input, agent_output)

    try:
        from swarmkit_runtime.model_providers import CompletionRequest  # noqa: PLC0415
        from swarmkit_runtime.model_providers._types import Message  # noqa: PLC0415

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
            logger.debug("Memory extraction: not worth saving (agent=%s)", agent_id)
            return None

        entry = MemoryEntry(
            id="",
            user=user,
            session_id=session_id,
            topic=data.get("topic", ""),
            context=data.get("context", ""),
            key_points=data.get("key_points", []),
            tags=data.get("tags", []),
            source_agent=agent_id,
        )
        store.add(entry)
        return entry

    except Exception:
        logger.warning("Memory extraction failed for agent=%s", agent_id, exc_info=True)
        return None


def retrieve_context(
    *,
    user_input: str,
    user: str | None,
    store: MemoryStore,
    config: dict[str, Any] | None = None,
) -> str | None:
    """Search memory for relevant prior context and format as a prompt prefix.

    Returns a formatted context string to prepend to the agent's system
    message, or None if no relevant memories found.
    """
    cfg = config or {}
    max_results = cfg.get("max_results", 5)
    min_score = cfg.get("similarity_threshold", 0.1)
    search_scope = cfg.get("search_scope", "user")

    search_user = user if search_scope in ("user", "both") else None
    results = store.search(
        user_input, user=search_user, max_results=max_results, min_score=min_score
    )

    if not results:
        return None

    blocks: list[str] = []
    for entry, _score in results:
        parts = [f"Topic: {entry.topic}"]
        if entry.context:
            parts.append(f"Context: {entry.context}")
        if entry.key_points:
            parts.append("Key points:")
            for kp in entry.key_points:
                parts.append(f"  - {kp}")
        if entry.session_id:
            parts.append(f"(from session {entry.session_id})")
        blocks.append("\n".join(parts))

    combined = "\n\n---\n\n".join(blocks)
    return _CONTEXT_TEMPLATE % combined


__all__ = ["extract_and_save", "retrieve_context"]
