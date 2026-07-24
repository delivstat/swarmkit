"""Webhook → pipeline ingress helpers (design/details/pipeline-triggering.md §"Structured webhook").

A ``Trigger`` whose target is a ``pipeline_target`` turns a signed webhook into a structured
``(correlation_id, event)`` the orchestrator sequences on — the "front door" for CI/Jira/Git/SAST
events. This module holds the two HTTP-agnostic pieces that path needs:

- :func:`extract_correlation_id` — resolve the opaque correlation id out of the JSON body with the
  simple ``$.a.b.c`` dotted path the trigger schema documents (no external jsonpath dependency).
- :func:`find_pipeline_webhook_trigger` — resolve a webhook ``{name}`` to a pipeline-event trigger
  config (matched by trigger id), or ``None`` when the name is an ordinary topology webhook.

Domain-neutral throughout: ``correlation_id`` is an opaque handle, never a business id. The actual
authorize → audit → deliver guardrail is the shared ``_ingress_pipeline_event`` in the server
package; this module only prepares its arguments.
"""

from __future__ import annotations

from typing import Any

#: The default dotted path when a ``pipeline_target`` declares no ``correlation_id`` extractor:
#: read ``correlation_id`` off the top level of the JSON body.
DEFAULT_CORRELATION_PATH = "$.correlation_id"


def extract_correlation_id(payload: dict[str, Any], path: str) -> str | None:
    """Resolve an opaque correlation id out of a JSON body with a simple ``$.a.b.c`` dotted path.

    Supports the JSONPath-ish subset the trigger schema documents — a leading ``$`` root, then
    ``.``-separated object keys (``$.body.correlation_id``). Deliberately tiny: no wildcards, no
    array indexing, no filters, no external dependency. The resolved value is coerced to ``str``
    (a numeric id is still an opaque handle). Returns ``None`` when the path does not resolve to a
    scalar — the receiver treats that as "could not extract" (a 400), never a silent drop.
    """
    if not path:
        return None
    expr = path[2:] if path.startswith("$.") else path[1:] if path.startswith("$") else path
    if not expr:
        return None
    current: Any = payload
    for key in expr.split("."):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    if current is None or isinstance(current, (dict, list, bool)):
        return None
    return str(current)


def find_pipeline_webhook_trigger(
    trigger_configs: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    """Resolve a webhook path segment ``name`` to an enabled pipeline-event webhook trigger config.

    A pipeline webhook is addressed by its **trigger id** (there is no topology to name), so the
    ``POST /hooks/{name}`` receiver looks the trigger up by id and routes to the pipeline ingress
    only when the matched trigger is a ``webhook`` carrying at least one ``pipeline_target``.
    Returns the trigger config dict (including its ``pipeline_targets``), or ``None`` when ``name``
    is an ordinary topology webhook (back-compat) or no such trigger exists.
    """
    for tc in trigger_configs:
        if tc.get("id") != name:
            continue
        if tc.get("type") != "webhook":
            continue
        if not tc.get("enabled", True):
            continue
        if tc.get("pipeline_targets"):
            return tc
    return None


__all__ = [
    "DEFAULT_CORRELATION_PATH",
    "extract_correlation_id",
    "find_pipeline_webhook_trigger",
]
