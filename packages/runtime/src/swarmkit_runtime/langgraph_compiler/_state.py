"""Swarm execution state schema for LangGraph.

See ``design/details/langgraph-compiler.md`` §State schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    merged.update(right)
    return merged


def _last_write_wins(left: str, right: str) -> str:
    return right


def _keep_seeded(left: list[Any], right: list[Any]) -> list[Any]:
    """Reducer for values seeded once by the caller and never produced by a node.

    Right-wins like ``_last_write_wins``, but tolerates the empty update LangGraph hands back from
    a node that did not touch the key — without this, a node returning a partial state dict would
    blank the caller's attachments halfway through a run, and the symptom would be an agent
    answering about a file that was there a moment ago.
    """
    return right if right else left


#: Default synthesis/output roles — auto-wired to depend on research tasks so they run last.
#: ``self`` is structural (a task assigned to it runs inline in the coordinator);
#: ``document-writer`` is the conventional final-document role. Overridable per-workspace/topology
#: via ``planning.synthesis_roles`` (design/details/configurable-synthesis-roles.md).
DEFAULT_SYNTHESIS_ROLES: tuple[str, ...] = ("self", "document-writer")

#: Default role name for the automatic large-context synthesis step (``planning.synthesizer_role``).
DEFAULT_SYNTHESIZER_ROLE = "synthesizer"


@dataclass(frozen=True)
class PlanningConfig:
    """Controls task planning and scope behavior for leader agents."""

    scope_required: bool = False
    two_phase: bool = False
    synthesis_roles: tuple[str, ...] = DEFAULT_SYNTHESIS_ROLES
    synthesizer_role: str = DEFAULT_SYNTHESIZER_ROLE


@dataclass(frozen=True)
class SynthesisConfig:
    """Controls automatic synthesis when all tasks complete.

    When configured, the compiler bypasses the architect for the final
    document and invokes a large-context model directly with all raw
    results, scope, and original input prompt.

    Template and output paths come from the user's input prompt,
    not from config — different requests can use different templates.
    """

    provider: str = ""
    model: str = ""
    prompt: str = ""


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
    delegation_counts: Annotated[dict[str, int], _merge_dicts]
    task_plan: Annotated[dict[str, Any], _merge_dicts]
    current_agent: Annotated[str, _last_write_wins]
    output: Annotated[str, _last_write_wins]
    #: Node id -> why that node failed. A harness node cannot raise (a failed run is a normal
    #: terminal event, not an exception), so it used to report failure only as its output TEXT —
    #: and a caller cannot tell a failure string from a work product. Downstream of that, a failed
    #: stage's error was chained into the next stage as its input. Structured because the string
    #: was never reliably recognisable: `_is_error_passthrough` matches "Error:"/"Tool error:" and
    #: never matched "[harness:claude-code] failure: no result event" at all.
    node_errors: Annotated[dict[str, str], _merge_dicts]
    #: Agent id -> the unified diff that agent's harness produced. A harness node set
    #: `result["diff"]` for the funnel's deterministic validate layers, which read the return dict
    #: closure — but `diff` was never a state key, so it never reached the graph result and nothing
    #: downstream could persist it. The worktree is torn down on exit, so the agent's entire work
    #: product was unrecoverable while the run reported success. Keyed by agent, not a single
    #: last-write-wins string: two harness agents in one run would otherwise lose one's work.
    diffs: Annotated[dict[str, str], _merge_dicts]
    #: Files the CALLER attached to this run — never something a node produces, which is why it is
    #: seeded once and last-write-wins rather than merged. Read only by the entry agent's prompt
    #: builder: the M8 version of this key was broadcast to every node, so an image reached
    #: text-only supervisors and errored there (`43ed71e3` reverted it whole). A node that wants a
    #: file it was not handed asks for one through a skill.
    #: See ``design/details/images-on-both-executors.md``.
    #:
    #: ``NotRequired`` is load-bearing, not laziness. The sub-states built for child agents
    #: (``_dag``, ``_delegation``, ``_task_executor``) construct a fresh ``SwarmState`` each, and a
    #: required key would force every one of them to name attachments — inviting exactly the
    #: propagation this design removed. Absent means "not for you", which is the common case.
    attachments: NotRequired[Annotated[list[Any], _keep_seeded]]
