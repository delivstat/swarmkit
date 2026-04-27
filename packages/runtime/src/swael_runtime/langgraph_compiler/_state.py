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


def _last_write_wins(left: str, right: str) -> str:
    return right


class SwarmState(TypedDict):
    """State flowing through the compiled swarm graph.

    Every key that any node can write must have a reducer so LangGraph
    accepts updates from multiple nodes across different graph steps.

    ``messages``: append-only conversation log (LangGraph's built-in reducer).
    ``agent_results``: per-agent results keyed by agent id (merge reducer).
    ``current_agent``: last agent that ran (last-write-wins).
    ``output``: the final response returned to the user (last-write-wins).
    """

    input: str
    messages: Annotated[list[BaseMessage], add_messages]
    agent_results: Annotated[dict[str, Any], _merge_dicts]
    current_agent: Annotated[str, _last_write_wins]
    output: Annotated[str, _last_write_wins]
