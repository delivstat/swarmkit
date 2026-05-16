"""Task plan management for structured delegation.

See ``design/details/structured-delegation.md``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from swarmkit_runtime.model_providers import ToolSpec
from swarmkit_runtime.resolver import ResolvedAgent


@dataclass
class Task:
    id: str
    agent: str
    instruction: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    started_at: float | None = None
    completed_at: float | None = None
    duration_s: float | None = None
    tool_calls: int = 0
    key_findings: list[str] = field(default_factory=list)
    result_path: str | None = None
    error: str | None = None
    delegation_count: int = 0


@dataclass
class TaskPlan:
    run_id: str = ""
    topology: str = ""
    created_at: float = 0.0
    tasks: list[Task] = field(default_factory=list)

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

    def validate_dependencies(self) -> list[str]:
        errors: list[str] = []
        task_ids = {t.id for t in self.tasks}
        _synthesis_agents = {"self", "document-writer"}
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

    def auto_fix_dependencies(self) -> list[str]:
        """Fix missing dependencies for synthesis and output tasks.

        If self-tasks or document-writer tasks have no depends_on,
        automatically make them depend on all other tasks. This
        prevents them from running in parallel with research tasks.
        """
        fixes: list[str] = []
        _synthesis_agents = {"self", "document-writer"}
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

        # Also fix document-writer to depend on self if both exist
        self_tasks = [t for t in self.tasks if t.agent == "self"]
        doc_tasks = [t for t in self.tasks if t.agent == "document-writer"]
        for dt in doc_tasks:
            for st in self_tasks:
                if st.id not in dt.depends_on:
                    dt.depends_on.append(st.id)
                    fixes.append(f"Auto-fixed: '{dt.id}' now depends on '{st.id}' (self-task)")
        return fixes

    def get_runnable_tasks(self) -> list[Task]:
        completed_ids = {t.id for t in self.tasks if t.status == "completed"}
        runnable = []
        for task in self.tasks:
            if task.status != "pending":
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
            task.status = "in_progress"
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
            task.status = "completed"
            task.completed_at = time.time()
            if task.started_at:
                task.duration_s = round(task.completed_at - task.started_at, 1)
            task.key_findings = key_findings or []
            task.result_path = result_path
            task.tool_calls = tool_calls

    def mark_failed(self, task_id: str, error: str) -> None:
        task = self.get_task(task_id)
        if task:
            task.status = "failed"
            task.completed_at = time.time()
            task.error = error

    def remove_tasks(self, task_ids: list[str]) -> list[str]:
        errors: list[str] = []
        removable = set()
        for tid in task_ids:
            task = self.get_task(tid)
            if not task:
                errors.append(f"Task '{tid}' not found")
            elif task.status != "pending":
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
            if task.status != "pending":
                errors.append(f"Cannot update task '{tid}' — status is '{task.status}'")
                continue
            if "instruction" in upd:
                task.instruction = upd["instruction"]
            if "depends_on" in upd:
                task.depends_on = upd["depends_on"]
        return errors

    def all_done(self) -> bool:
        return all(t.status in ("completed", "failed") for t in self.tasks)

    def render_status(self) -> str:
        lines = ["TASK PLAN STATUS:\n"]
        for task in self.tasks:
            if task.status == "completed":
                dur = f"{task.duration_s:.1f}s" if task.duration_s else ""
                calls = f"{task.tool_calls} tool calls" if task.tool_calls else ""
                meta = ", ".join(p for p in [dur, calls] if p)
                lines.append(f"completed: {task.id} ({task.agent}) — {meta}")
                if task.key_findings:
                    for finding in task.key_findings:
                        lines.append(f"   - {finding}")
                if task.result_path:
                    lines.append(f"   Full results: {task.result_path}")
            elif task.status == "in_progress":
                lines.append(f"running: {task.id} ({task.agent})")
            elif task.status == "failed":
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
        data = json.loads(path.read_text(encoding="utf-8"))
        plan = cls(
            run_id=data.get("run_id", ""),
            topology=data.get("topology", ""),
            created_at=data.get("created_at", 0.0),
        )
        for raw in data.get("tasks", []):
            plan.tasks.append(
                Task(
                    id=raw["id"],
                    agent=raw["agent"],
                    instruction=raw.get("instruction", ""),
                    depends_on=raw.get("depends_on", []),
                    status=raw.get("status", "pending"),
                    started_at=raw.get("started_at"),
                    completed_at=raw.get("completed_at"),
                    duration_s=raw.get("duration_s"),
                    tool_calls=raw.get("tool_calls", 0),
                    key_findings=raw.get("key_findings", []),
                    result_path=raw.get("result_path"),
                    error=raw.get("error"),
                    delegation_count=raw.get("delegation_count", 0),
                )
            )
        return plan


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
            '  {"id":"research","agent":"jira-researcher","depends_on":[]}\n'
            '  {"id":"config","agent":"config-analyst","depends_on":["research"]}\n'
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


def build_freeze_scope_tool() -> ToolSpec:
    return ToolSpec(
        name="freeze-scope",
        description=(
            "Define the scope contract for this run. Call after reading "
            "the source material (ticket, brief, requirements doc) to "
            "freeze what must be satisfied, what's excluded, and what "
            "constraints apply. The spec-conformance skill validates "
            "output against this frozen scope."
        ),
        input_schema={
            "type": "object",
            "properties": {
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
            },
            "required": ["source", "requirements"],
        },
    )
