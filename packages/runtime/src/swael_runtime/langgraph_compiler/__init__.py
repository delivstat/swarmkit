"""Topology → LangGraph ``StateGraph`` compilation (design §14.3).

See ``design/details/langgraph-compiler.md`` for the interaction model
and translation rules.

Entry point: :func:`compile_topology` takes a ``ResolvedTopology`` plus
provider instances and returns a compiled LangGraph ``CompiledGraph``
ready for invocation.
"""

from __future__ import annotations

from ._compiler import compile_topology
from ._state import SwarmState

__all__ = ["SwarmState", "compile_topology"]
