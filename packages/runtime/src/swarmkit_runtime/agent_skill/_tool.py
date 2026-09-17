"""The tool schema the model sees for an ``agent`` skill."""

from __future__ import annotations

from typing import Any

from swarmkit_runtime.skills import impl_get


def agent_tool_schema(impl: Any) -> dict[str, Any]:
    """``{input, context?}`` — plus ``{task_id, answer}`` when the calling agent may answer the
    other agent's questions, so the model learns the follow-up shape from the schema."""
    props: dict[str, Any] = {
        "input": {"type": "string", "description": "What to ask the other agent to do."},
        "context": {
            "type": "object",
            "description": "Optional structured context handed along with the request.",
        },
    }
    policy = impl_get(impl, "on_unanswerable", None)
    policy = getattr(policy, "value", policy)
    if str(policy or "agent") == "agent" and not impl_get(impl, "topology", None):
        props["task_id"] = {
            "type": "string",
            "description": "To answer a question the agent asked: the task_id from its "
            "input_required result.",
        }
        props["answer"] = {"type": "string", "description": "Your answer to that question."}
    return {"type": "object", "properties": props}
