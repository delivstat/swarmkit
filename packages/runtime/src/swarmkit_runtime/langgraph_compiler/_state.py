"""Swarm execution state schema for LangGraph.

See ``design/details/langgraph-compiler.md`` §State schema.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    merged.update(right)
    return merged


class SwarmState(TypedDict):
    """State flowing through the compiled swarm graph.

    ``messages``: append-only conversation log (LangGraph's built-in reducer).
    ``agent_results``: per-agent results keyed by agent id (merge reducer).
    ``output``: the final response returned to the user.
    """

    input: str
    messages: Annotated[list[BaseMessage], add_messages]
    agent_results: Annotated[dict[str, Any], _merge_dicts]
    current_agent: str
    output: str
