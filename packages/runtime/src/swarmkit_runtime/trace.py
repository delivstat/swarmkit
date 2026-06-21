"""Run trace — structured record of agent calls, tool usage, and token counts.

Captures the full call graph for a topology run: which agents called
which, what tools they used, how many tokens they consumed, and whether
they succeeded or failed.

Persisted to .swarmkit/traces/<run-id>.json for post-run analysis
via ``swarmkit trace <run-id>``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolCall:
    """A single tool/skill invocation within an agent step."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result_length: int = 0
    error: str | None = None
    duration_ms: int = 0
    cached: bool = False


@dataclass
class AgentStep:
    """One invocation of an agent (one LLM call + tool loop)."""

    agent_id: str
    model: str = ""
    parent_agent: str | None = None
    role: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    delegations: list[str] = field(default_factory=list)
    result_length: int = 0
    error: str | None = None
    forced_synthesis: bool = False


@dataclass
class RunTrace:
    """Complete trace of a topology run."""

    run_id: str = ""
    topology: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    agent_steps: list[AgentStep] = field(default_factory=list)
    token_by_agent: dict[str, dict[str, int]] = field(default_factory=dict)
    token_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    # Context compression savings (read-side). bytes are characters.
    compression_bytes_in: int = 0
    compression_bytes_out: int = 0
    compression_calls: int = 0
    compression_by_backend: dict[str, dict[str, int]] = field(default_factory=dict)

    def record_compression(
        self, tool_name: str, backend: str, bytes_in: int, bytes_out: int
    ) -> None:
        """Record one read-side compression of a tool result."""
        self.compression_bytes_in += bytes_in
        self.compression_bytes_out += bytes_out
        self.compression_calls += 1
        by = self.compression_by_backend.setdefault(
            backend, {"calls": 0, "bytes_in": 0, "bytes_out": 0}
        )
        by["calls"] += 1
        by["bytes_in"] += bytes_in
        by["bytes_out"] += bytes_out

    def record_llm_call(
        self,
        agent_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record tokens from any LLM call (tool loop, synthesis, etc)."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += input_tokens + output_tokens
        self.llm_calls += 1

        agent_tokens = self.token_by_agent.setdefault(
            agent_id, {"input": 0, "output": 0, "total": 0}
        )
        agent_tokens["input"] += input_tokens
        agent_tokens["output"] += output_tokens
        agent_tokens["total"] += input_tokens + output_tokens

        if model:
            model_tokens = self.token_by_model.setdefault(
                model, {"input": 0, "output": 0, "total": 0}
            )
            model_tokens["input"] += input_tokens
            model_tokens["output"] += output_tokens
            model_tokens["total"] += input_tokens + output_tokens

    def start(self, run_id: str, topology: str) -> None:
        self.run_id = run_id
        self.topology = topology
        self.start_time = time.time()

    def add_step(self, step: AgentStep) -> None:
        self.agent_steps.append(step)
        self.total_input_tokens += step.input_tokens
        self.total_output_tokens += step.output_tokens
        self.total_tokens += step.total_tokens

        agent_tokens = self.token_by_agent.setdefault(
            step.agent_id, {"input": 0, "output": 0, "total": 0}
        )
        agent_tokens["input"] += step.input_tokens
        agent_tokens["output"] += step.output_tokens
        agent_tokens["total"] += step.total_tokens

        if step.model:
            model_tokens = self.token_by_model.setdefault(
                step.model, {"input": 0, "output": 0, "total": 0}
            )
            model_tokens["input"] += step.input_tokens
            model_tokens["output"] += step.output_tokens
            model_tokens["total"] += step.total_tokens

    def finish(self) -> None:
        self.end_time = time.time()
        self.duration_ms = int((self.end_time - self.start_time) * 1000)

    def save(self, workspace_root: Path) -> Path:
        traces_dir = workspace_root / ".swarmkit" / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        path = traces_dir / f"{self.run_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> RunTrace:
        data = json.loads(path.read_text())
        trace = cls()
        for k, v in data.items():
            if k == "agent_steps":
                trace.agent_steps = [
                    AgentStep(
                        **{
                            **s,
                            "tool_calls": [ToolCall(**tc) for tc in s.get("tool_calls", [])],
                        }
                    )
                    for s in v
                ]
            elif hasattr(trace, k):
                setattr(trace, k, v)
        return trace

    def render_text(self) -> str:
        """Render the trace as a human-readable text summary."""
        lines: list[str] = []
        lines.append(f"Run: {self.run_id}")
        lines.append(f"Topology: {self.topology}")
        dur_s = self.duration_ms / 1000
        lines.append(f"Duration: {dur_s:.1f}s")
        total_calls = self.llm_calls + len(self.agent_steps)
        lines.append(
            f"Total tokens: {self.total_tokens:,} "
            f"(input: {self.total_input_tokens:,} / output: {self.total_output_tokens:,})"
            f" across {total_calls} LLM call(s)"
        )
        lines.append("")

        lines.append("Agent Call Graph:")
        parent_map: dict[str, list[AgentStep]] = {}
        for step in self.agent_steps:
            parent = step.parent_agent or "__root__"
            parent_map.setdefault(parent, []).append(step)

        _visited: set[tuple[str, int]] = set()

        def _render_agent(agent_id: str, indent: int) -> None:
            key = (agent_id, indent)
            if key in _visited or indent > 20:
                return
            _visited.add(key)
            children = parent_map.get(agent_id, [])
            for step in children:
                prefix = "  " * indent + ("└─→ " if indent > 0 else "")
                tokens = f"{step.total_tokens:,} tokens"
                model = f" ({step.model})" if step.model else ""
                lines.append(f"{prefix}{step.agent_id}{model}, {tokens}")
                for tc in step.tool_calls:
                    tc_prefix = "  " * (indent + 1) + "├── "
                    status = "✓" if not tc.error else f"✗ {tc.error}"
                    cache = " [cached]" if tc.cached else ""
                    dur = f" ({tc.duration_ms}ms)" if tc.duration_ms else ""
                    lines.append(f"{tc_prefix}{tc.tool_name} {status}{cache}{dur}")
                for d in step.delegations:
                    d_prefix = "  " * (indent + 1) + "├── "
                    lines.append(f"{d_prefix}→ delegated to {d}")
                if step.forced_synthesis:
                    fs_prefix = "  " * (indent + 1) + "├── "
                    lines.append(f"{fs_prefix}⚠ forced synthesis (tool limit)")
                _render_agent(step.agent_id, indent + 1)

        _render_agent("__root__", 0)

        lines.append("")
        lines.append("Tokens by agent:")
        for agent_id, tokens in sorted(self.token_by_agent.items()):
            lines.append(
                f"  {agent_id:30s} {tokens['total']:>8,} "
                f"(in: {tokens['input']:,} / out: {tokens['output']:,})"
            )

        lines.append("")
        lines.append("Tokens by model:")
        for model, tokens in sorted(self.token_by_model.items()):
            lines.append(
                f"  {model:40s} {tokens['total']:>8,} "
                f"(in: {tokens['input']:,} / out: {tokens['output']:,})"
            )

        return "\n".join(lines)


def list_traces(workspace_root: Path, limit: int = 10) -> list[dict[str, Any]]:
    """List recent run traces."""
    traces_dir = workspace_root / ".swarmkit" / "traces"
    if not traces_dir.exists():
        return []
    files = sorted(traces_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    results = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text())
            results.append(
                {
                    "run_id": data.get("run_id", f.stem),
                    "topology": data.get("topology", ""),
                    "duration_ms": data.get("duration_ms", 0),
                    "total_tokens": data.get("total_tokens", 0),
                    "agents": len(data.get("agent_steps", [])),
                    "path": str(f),
                }
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return results
