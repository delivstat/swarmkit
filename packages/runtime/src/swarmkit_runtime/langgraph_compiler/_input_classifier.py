"""The shared input-request classifier (executor-input-escalation-plan.md, RFC §6.3 / decision 7).

Domain-judgment questions ("which of these three implementations?") have **no structured event** the
way §6.2 permissions do — they are prose in the harness's final message. Detection is therefore a
**shared core classifier, not per-adapter regex**: a cheap pre-filter gates one small
structured-output model call that decides *is this a request for input?* and extracts the question +
options. Language-agnostic by construction (harnesses answer in the task's language; punctuation
heuristics fail outside English).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from swarmkit_runtime.executors import ExecInputRequested
from swarmkit_runtime.model_providers import CompletionRequest, Message

if TYPE_CHECKING:
    from swarmkit_runtime.model_providers._registry import ModelProviderProtocol

_CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["is_request"],
    "additionalProperties": False,
    "properties": {
        "is_request": {
            "type": "boolean",
            "description": "True only if the message asks the caller to make a decision or provide "
            "input before it can proceed (not a status update or a completed answer).",
        },
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "free_text_allowed": {"type": "boolean"},
        "question_class": {"type": "string"},
    },
}

_SYSTEM = (
    "You classify the FINAL message of an autonomous coding agent. Decide whether it is asking "
    "the caller for a decision or input it needs before it can continue (e.g. 'which of these "
    "approaches should I use?', 'what should I name this?'). A message that reports work done, "
    "states an assumption it already acted on, or asks nothing is NOT a request. When it is a "
    "request, extract the question, any options, and whether free-text is acceptable."
)


def should_classify(*, artifact_present: bool) -> bool:
    """Pre-filter (no LLM, no language heuristics): only classify when the run took **no action**
    (no artifact / empty diff). Purely structural — did it produce output? — never a check of the
    message text. If the harness did the work, it wasn't asking, so skip the model call. All actual
    question-detection is the LLM classifier below (decision 7)."""
    return not artifact_present


async def classify_input_request(
    final_message: str,
    *,
    model_provider: ModelProviderProtocol,
    model: str,
    max_tokens: int = 400,
) -> ExecInputRequested | None:
    """One structured-output call: is ``final_message`` a request for input? If so, return an
    :class:`ExecInputRequested` with the extracted question + options; else ``None``. Misfires are
    harmless (resolved as a no-op answer downstream). Any model/parse error ⇒ ``None`` (fail open —
    a classifier hiccup must never dead-end a run)."""
    text = final_message.strip()
    if not text:
        return None
    request = CompletionRequest(
        model=model,
        system=_SYSTEM,
        messages=[Message(role="user", content=text)],
        temperature=0.0,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "input_request", "schema": _CLASSIFIER_SCHEMA},
        },
    )
    try:
        response = await model_provider.complete(request)
        data = json.loads(response.text)
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("is_request"):
        return None
    return ExecInputRequested(
        question=str(data.get("question", "")),
        options=tuple(str(o) for o in (data.get("options") or ())),
        free_text_allowed=bool(data.get("free_text_allowed", True)),
        question_class=data.get("question_class"),
    )
