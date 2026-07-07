"""Task plan management for structured delegation.

See ``design/details/structured-delegation.md``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from swarmkit_runtime.langgraph_compiler._sentinels import TaskStatus
from swarmkit_runtime.langgraph_compiler._state import DEFAULT_SYNTHESIS_ROLES
from swarmkit_runtime.model_providers import ToolSpec
from swarmkit_runtime.resolver import ResolvedAgent


@dataclass
class Task:
    id: str
    agent: str
    instruction: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    started_at: float | None = None
    completed_at: float | None = None
    duration_s: float | None = None
    tool_calls: int = 0
    key_findings: list[str] = field(default_factory=list)
    result_path: str | None = None
    error: str | None = None
    delegation_count: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        """Build a Task from a persisted/state dict, defaulting every optional field.

        The one place dict→Task reconstruction lives — three call sites previously inlined this
        loop and one had drifted (dropped started_at/completed_at/duration_s/tool_calls on reload).
        """
        return cls(
            id=raw["id"],
            agent=raw["agent"],
            instruction=raw.get("instruction", ""),
            depends_on=raw.get("depends_on", []),
            status=TaskStatus(raw.get("status", TaskStatus.PENDING)),
            started_at=raw.get("started_at"),
            completed_at=raw.get("completed_at"),
            duration_s=raw.get("duration_s"),
            tool_calls=raw.get("tool_calls", 0),
            key_findings=raw.get("key_findings", []),
            result_path=raw.get("result_path"),
            error=raw.get("error"),
            delegation_count=raw.get("delegation_count", 0),
        )


@dataclass
class TaskPlan:
    run_id: str = ""
    topology: str = ""
    created_at: float = 0.0
    tasks: list[Task] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TaskPlan:
        """Build a TaskPlan from a persisted/state dict (empty/None → an empty plan)."""
        data = data or {}
        return cls(
            run_id=data.get("run_id", ""),
            topology=data.get("topology", ""),
            created_at=data.get("created_at", 0.0),
            tasks=[Task.from_dict(raw) for raw in data.get("tasks", [])],
        )

    def add_tasks(self, raw_tasks: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        existing_ids = {t.id for t in self.tasks}
        for raw in raw_tasks:
            task_id = raw.get("id", "")
            if not task_id:
                errors.append("Task missing 'id' field")
                continue
            if task_id in existing_ids:
                errors.append(f"Duplicate task id: {task_id}")
                continue
            agent = raw.get("agent", "")
            if not agent:
                errors.append(f"Task '{task_id}' missing 'agent' field")
                continue
            instruction = raw.get("instruction", "")
            if not instruction:
                errors.append(f"Task '{task_id}' missing 'instruction' field")
                continue
            depends_on = raw.get("depends_on", [])
            self.tasks.append(
                Task(
                    id=task_id,
                    agent=agent,
                    instruction=instruction,
                    depends_on=depends_on if isinstance(depends_on, list) else [],
                )
            )
            existing_ids.add(task_id)
        return errors

    def validate_agents(self, valid_agents: set[str]) -> list[str]:
        errors: list[str] = []
        for task in self.tasks:
            if task.agent != "self" and task.agent not in valid_agents:
                errors.append(
                    f"Task '{task.id}' references unknown agent '{task.agent}'. "
                    f"Valid agents: {', '.join(sorted(valid_agents))}, self"
                )
        return errors

    def validate_dependencies(
        self, synthesis_roles: tuple[str, ...] = DEFAULT_SYNTHESIS_ROLES
    ) -> list[str]:
        errors: list[str] = []
        task_ids = {t.id for t in self.tasks}
        _synthesis_agents = set(synthesis_roles)
        for task in self.tasks:
            for dep in task.depends_on:
                if dep not in task_ids:
                    errors.append(f"Task '{task.id}' depends on '{dep}' which doesn't exist")
            if task.id in task.depends_on:
                errors.append(f"Task '{task.id}' depends on itself")
            if task.agent in _synthesis_agents and not task.depends_on:
                other_tasks = [t.id for t in self.tasks if t.id != task.id]
                if other_tasks:
                    errors.append(
                        f"Task '{task.id}' (agent={task.agent}) has no "
                        f"depends_on but should depend on research tasks. "
                        f"Without dependencies it runs in parallel and "
                        f"won't have data to work with."
                    )
        return errors

    def auto_fix_dependencies(
        self, synthesis_roles: tuple[str, ...] = DEFAULT_SYNTHESIS_ROLES
    ) -> list[str]:
        """Fix missing dependencies for synthesis and output tasks.

        If self-tasks or synthesis-role tasks (``synthesis_roles``, default self +
        document-writer) have no depends_on, automatically make them depend on all other tasks.
        This prevents them from running in parallel with research tasks.
        """
        fixes: list[str] = []
        _synthesis_agents = set(synthesis_roles)
        all_ids = [t.id for t in self.tasks]

        for task in self.tasks:
            if task.agent not in _synthesis_agents:
                continue
            if task.depends_on:
                continue
            other_ids = [tid for tid in all_ids if tid != task.id]
            if not other_ids:
                continue
            # Self depends on all non-self/non-doc tasks
            research_ids = [
                t.id for t in self.tasks if t.id != task.id and t.agent not in _synthesis_agents
            ]
            if not research_ids:
                research_ids = other_ids
            task.depends_on = research_ids
            fixes.append(
                f"Auto-fixed: task '{task.id}' ({task.agent}) now "
                f"depends on [{', '.join(research_ids)}]"
            )

        # Auto-add synthesis task if no self-tasks exist.
        # Skip when there's only 1 research task — that's likely Phase 1
        # of a two-phase plan where the architect will add tasks at checkpoint.
        has_self = any(t.agent == "self" for t in self.tasks)
        research_ids = [t.id for t in self.tasks if t.agent not in _synthesis_agents]
        if not has_self and len(research_ids) >= 2:
            self.tasks.append(
                Task(
                    id="__auto_synthesize__",
                    agent="self",
                    instruction=(
                        "Synthesize all findings from completed tasks. "
                        "Cross-validate data across sources. Write "
                        "the final comprehensive answer."
                    ),
                    depends_on=research_ids,
                )
            )
            fixes.append(
                f"Auto-added: synthesis task '__auto_synthesize__' "
                f"depends on [{', '.join(research_ids)}]"
            )

        # Also fix non-self synthesis roles (e.g. document-writer) to depend on self if both exist
        self_tasks = [t for t in self.tasks if t.agent == "self"]
        doc_tasks = [t for t in self.tasks if t.agent in _synthesis_agents and t.agent != "self"]
        for dt in doc_tasks:
            for st in self_tasks:
                if st.id not in dt.depends_on:
                    dt.depends_on.append(st.id)
                    fixes.append(f"Auto-fixed: '{dt.id}' now depends on '{st.id}' (self-task)")
        return fixes

    def get_runnable_tasks(self) -> list[Task]:
        completed_ids = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        runnable = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            deps_met = all(d in completed_ids for d in task.depends_on)
            if deps_met:
                runnable.append(task)
        return runnable

    def get_task(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def mark_started(self, task_id: str) -> None:
        task = self.get_task(task_id)
        if task:
            task.status = TaskStatus.IN_PROGRESS
            task.started_at = time.time()
            task.delegation_count += 1

    def mark_completed(
        self,
        task_id: str,
        key_findings: list[str] | None = None,
        result_path: str | None = None,
        tool_calls: int = 0,
    ) -> None:
        task = self.get_task(task_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            if task.started_at:
                task.duration_s = round(task.completed_at - task.started_at, 1)
            task.key_findings = key_findings or []
            task.result_path = result_path
            task.tool_calls = tool_calls

    def mark_failed(self, task_id: str, error: str) -> None:
        task = self.get_task(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            task.error = error

    def remove_tasks(self, task_ids: list[str]) -> list[str]:
        errors: list[str] = []
        removable = set()
        for tid in task_ids:
            task = self.get_task(tid)
            if not task:
                errors.append(f"Task '{tid}' not found")
            elif task.status != TaskStatus.PENDING:
                errors.append(f"Cannot remove task '{tid}' — status is '{task.status}'")
            else:
                removable.add(tid)
        self.tasks = [t for t in self.tasks if t.id not in removable]
        return errors

    def update_tasks(self, updates: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for upd in updates:
            tid = upd.get("id", "")
            task = self.get_task(tid)
            if not task:
                errors.append(f"Task '{tid}' not found")
                continue
            if task.status != TaskStatus.PENDING:
                errors.append(f"Cannot update task '{tid}' — status is '{task.status}'")
                continue
            if "instruction" in upd:
                task.instruction = upd["instruction"]
            if "depends_on" in upd:
                task.depends_on = upd["depends_on"]
        return errors

    def all_done(self) -> bool:
        return all(t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED) for t in self.tasks)

    def render_status(self) -> str:
        lines = ["TASK PLAN STATUS:\n"]
        for task in self.tasks:
            if task.status == TaskStatus.COMPLETED:
                dur = f"{task.duration_s:.1f}s" if task.duration_s else ""
                calls = f"{task.tool_calls} tool calls" if task.tool_calls else ""
                meta = ", ".join(p for p in [dur, calls] if p)
                lines.append(f"completed: {task.id} ({task.agent}) — {meta}")
                if task.key_findings:
                    for finding in task.key_findings:
                        lines.append(f"   - {finding}")
                if task.result_path:
                    lines.append(f"   Full results: {task.result_path}")
            elif task.status == TaskStatus.IN_PROGRESS:
                lines.append(f"running: {task.id} ({task.agent})")
            elif task.status == TaskStatus.FAILED:
                lines.append(f"failed: {task.id} ({task.agent}) — {task.error}")
            else:
                deps = ""
                if task.depends_on:
                    dep_statuses = []
                    for d in task.depends_on:
                        dt = self.get_task(d)
                        st = dt.status if dt else "?"
                        dep_statuses.append(f"{d} [{st}]")
                    deps = f" (depends on: {', '.join(dep_statuses)})"
                lines.append(f"pending: {task.id} ({task.agent}){deps}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "topology": self.topology,
            "created_at": self.created_at,
            "tasks": [asdict(t) for t in self.tasks],
        }

    def save(self, run_state_dir: Path) -> None:
        run_state_dir.mkdir(parents=True, exist_ok=True)
        path = run_state_dir / "tasks.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> TaskPlan:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def build_create_task_plan_tool(agent: ResolvedAgent) -> ToolSpec:
    agent_lines = []
    for child in agent.children:
        desc = getattr(child, "description", "") or child.id
        agent_lines.append(f"  - {child.id}: {desc}")
    agent_lines.append(
        "  - self: You (the coordinator). Use for synthesis, "
        "diagrams, or tasks needing your own skills."
    )
    agent_list = "\n".join(agent_lines)

    return ToolSpec(
        name="create-task-plan",
        description=(
            "Create an execution plan for your workers and yourself. "
            "Each task is assigned to an available agent or to 'self' "
            "for tasks you handle.\n\n"
            "IMPORTANT: Use depends_on to set execution order. Tasks "
            "with NO depends_on run in parallel. Tasks that need "
            "results from other tasks MUST list those task IDs in "
            "depends_on. Self-tasks (synthesis, document writing) "
            "should ALWAYS depend on the research tasks.\n\n"
            "Example ordering:\n"
            '  {"id":"research","agent":"researcher","depends_on":[]}\n'
            '  {"id":"analyze","agent":"analyst","depends_on":["research"]}\n'
            '  {"id":"synthesize","agent":"self","depends_on":["config"]}\n'
            '  {"id":"write-doc","agent":"document-writer","depends_on":["synthesize"]}\n\n'
            f"Available agents:\n{agent_list}"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Unique task identifier",
                            },
                            "agent": {
                                "type": "string",
                                "description": "Agent ID or 'self'",
                            },
                            "instruction": {
                                "type": "string",
                                "description": "What the agent should do",
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Task IDs that must complete first",
                            },
                        },
                        "required": ["id", "agent", "instruction"],
                    },
                },
            },
            "required": ["tasks"],
        },
    )


def build_update_task_plan_tool() -> ToolSpec:
    return ToolSpec(
        name="update-task-plan",
        description=(
            "Modify the execution plan after reviewing results. "
            "Add new tasks, remove pending tasks, or update instructions "
            "for pending tasks. Cannot modify completed or in-progress tasks."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "add": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "agent": {"type": "string"},
                            "instruction": {"type": "string"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["id", "agent", "instruction"],
                    },
                    "description": "New tasks to add",
                },
                "remove": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs to remove (pending only)",
                },
                "update": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "instruction": {"type": "string"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["id"],
                    },
                    "description": "Updates to pending tasks",
                },
                "complete": {
                    "type": "boolean",
                    "description": "Set true to end planning and synthesize",
                },
            },
        },
    )


def build_read_task_result_tool() -> ToolSpec:
    return ToolSpec(
        name="read-task-result",
        description=(
            "Read the full result from a completed task. Use when the "
            "key_findings summary isn't enough detail."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID from the plan (must be completed)",
                },
            },
            "required": ["task_id"],
        },
    )


_SCOPE_PROPERTIES: dict[str, Any] = {
    "source": {
        "type": "string",
        "description": "Where this scope comes from (ticket ID, doc path, brief)",
    },
    "requirements": {
        "type": "array",
        "items": {"type": "string"},
        "description": "What must be satisfied — acceptance criteria, deliverables",
    },
    "constraints": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Non-negotiable rules that override default assumptions",
    },
    "authoritative_sources": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Documents/pages that are the source of truth",
    },
    "excluded": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Explicitly out of scope — prevents scope inflation",
    },
    "decisions": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "by": {"type": "string"},
                "date": {"type": "string"},
                "decision": {"type": "string"},
            },
        },
        "description": "Stakeholder decisions that constrain the design",
    },
    "related": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Related items — only explicitly linked, not inferred",
    },
    "solution_approach": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "component": {"type": "string"},
                "change": {"type": "string"},
                "rationale": {"type": "string"},
                "verified": {"type": "boolean"},
            },
        },
        "description": (
            "The actual solution — what changes, where, and why. "
            "Each entry is one component change with rationale "
            "derived from research findings. This is the architect's "
            "reasoned design, not a summary of findings."
        ),
    },
    "open_questions": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Questions that research could not resolve — flagged for human review",
    },
}


def build_create_scope_tool() -> ToolSpec:
    return ToolSpec(
        name="create-scope",
        description=(
            "Define the scope contract AND solution approach for this run. "
            "Call after reading ALL research results to establish: "
            "(1) what must be satisfied (requirements), "
            "(2) what constraints apply, "
            "(3) YOUR SOLUTION — the specific changes to make, where, and why. "
            "The solution_approach field is where you do your reasoning as "
            "an architect. Each entry should name a component, describe the "
            "change, explain why based on research findings, and flag if "
            "verified. This becomes the authoritative design that the "
            "synthesizer uses to write the document."
        ),
        input_schema={
            "type": "object",
            "properties": _SCOPE_PROPERTIES,
            "required": ["source", "requirements", "solution_approach"],
        },
    )


def build_update_scope_tool() -> ToolSpec:
    return ToolSpec(
        name="update-scope",
        description=(
            "Update the scope with new information from research. Use "
            "when Phase 2 tasks reveal constraints, requirements, or "
            "sources not in the original ticket. Can ADD requirements, "
            "constraints, sources, and exclusions. Cannot remove existing "
            "requirements or loosen existing constraints."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "add_requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New requirements discovered during research",
                },
                "add_constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New constraints discovered during research",
                },
                "add_authoritative_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New authoritative sources found",
                },
                "add_excluded": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New exclusions identified",
                },
                "add_decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "by": {"type": "string"},
                            "date": {"type": "string"},
                            "decision": {"type": "string"},
                        },
                    },
                    "description": "New stakeholder decisions found",
                },
                "add_related": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New related items discovered",
                },
                "add_solution_approach": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "component": {"type": "string"},
                            "change": {"type": "string"},
                            "rationale": {"type": "string"},
                            "verified": {"type": "boolean"},
                        },
                    },
                    "description": "New solution design entries from follow-up research",
                },
                "add_open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Questions that research could not resolve",
                },
            },
        },
    )


def build_read_scope_tool() -> ToolSpec:
    return ToolSpec(
        name="read-scope",
        description=(
            "Read the current scope contract. Use before synthesis to "
            "review all requirements, constraints, and exclusions that "
            "the output must satisfy."
        ),
        input_schema={
            "type": "object",
            "properties": {},
        },
    )
