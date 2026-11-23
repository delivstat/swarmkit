"""A harness agent receives its archetype's ``prompt.system`` (#1098).

`_task_spec` built the harness task from the run input, the output contract and the workspace
`CLAUDE.md`, and never read ``prompt.system``. An instruction there (tutorial 17's "You implement
exactly the change described, with tests.", or "call get_convention first") never reached the
agent, and nothing said so: seen live as `executor.mcp_usage calls: 0` with the instruction in
``prompt.system`` and `calls: 1` with the same words in the run input.

Now it always arrives, one of two ways, and the run records which:

* **native** — the adapter's launch template uses ``{task.system_prompt}`` (Claude Code:
  ``--append-system-prompt``);
* **statement** — every other adapter: the runtime puts it ahead of the task statement on the first
  launch, and not on a resume, whose session already has it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    DeclarativeExecutor,
    SandboxHandle,
    TaskSpec,
    load_adapter_specs,
    system_prompt_delivery,
    with_instructions,
)
from swarmkit_runtime.langgraph_compiler._harness_node import _task_spec

SYSTEM = "You implement exactly the change described, with tests. Call get_convention first."
STATEMENT = "Add a retry to the order client."


class _Agent:
    id = "dev"
    role = "root"
    skills: ClassVar[list[Any]] = []
    executor = None
    output_schema = None

    def __init__(self, system: str | None) -> None:
        self.prompt = {"system": system} if system is not None else None


def _spec(kind: str) -> Any:
    return load_adapter_specs()[kind]


def _argv(
    kind: str, task: TaskSpec, *, resume: str | None = None, answer: str | None = None
) -> list[str]:
    executor = DeclarativeExecutor(_spec(kind))
    argv, _env = executor._launch(
        task, SandboxHandle(root=Path("/tmp/sbx")), BudgetEnvelope(), resume, (), answer
    )
    return argv


def test_the_task_spec_carries_the_agents_system_prompt() -> None:
    state = cast("Any", {"input": STATEMENT})
    spec = _task_spec(cast("Any", _Agent(f"  {SYSTEM}\n")), state, None)
    assert spec.system_prompt == SYSTEM
    # the statement is still the task alone; delivery is the executor's decision
    assert spec.statement == STATEMENT


def test_no_system_prompt_is_no_system_prompt() -> None:
    state = cast("Any", {"input": STATEMENT})
    assert _task_spec(cast("Any", _Agent(None)), state, None).system_prompt == ""
    assert _task_spec(cast("Any", _Agent("")), state, None).system_prompt == ""


def test_claude_code_takes_it_natively_and_every_other_bundled_adapter_by_statement() -> None:
    assert system_prompt_delivery(_spec("claude-code")) == "native"
    for kind in ("codex", "copilot", "gemini-cli", "opencode"):
        assert system_prompt_delivery(_spec(kind)) == "statement", kind


def test_claude_code_appends_it_to_its_own_system_prompt() -> None:
    argv = _argv("claude-code", TaskSpec(statement=STATEMENT, system_prompt=SYSTEM))
    i = argv.index("--append-system-prompt")
    assert argv[i + 1] == SYSTEM
    # delivered once: not also folded into the statement
    assert argv[argv.index("-p") + 1] == STATEMENT


@pytest.mark.parametrize("kind", ["codex", "copilot", "gemini-cli", "opencode"])
def test_an_adapter_without_a_flag_gets_it_ahead_of_the_statement(kind: str) -> None:
    argv = _argv(kind, TaskSpec(statement=STATEMENT, system_prompt=SYSTEM))
    expected = with_instructions(SYSTEM, STATEMENT)
    assert expected in argv
    assert expected.startswith("## Instructions") and expected.index(SYSTEM) < expected.index(
        STATEMENT
    )
    assert "--append-system-prompt" not in argv


def test_a_resume_does_not_repeat_the_instructions() -> None:
    """A resumed session already holds them; the resumed message is the answer alone."""
    task = TaskSpec(statement=STATEMENT, system_prompt=SYSTEM)
    argv = _argv("codex", task, resume="tok", answer="use the v2 client")
    assert not any("## Instructions" in a for a in argv)


def test_without_a_system_prompt_nothing_changes() -> None:
    claude = _argv("claude-code", TaskSpec(statement=STATEMENT))
    assert "--append-system-prompt" not in claude
    codex = _argv("codex", TaskSpec(statement=STATEMENT))
    assert STATEMENT in codex and not any("## Instructions" in a for a in codex)


# ---- end to end: a real run through the harness node ---------------------------------------------

_FAKE_HARNESS = """#!/usr/bin/env bash
# Records each argument on its own line (NUL-free), then answers like `claude -p` stream-json.
for a in "$@"; do printf '%s\\n<<ARG>>\\n' "$a"; done > "$ARGV_LOG"
echo '{"type":"system","session_id":"s"}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}'
echo '{"type":"result","subtype":"success","result":"done","total_cost_usd":0.0}'
"""

_WS = {
    "workspace.yaml": (
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: sp, name: sp}\n"
        "governance: {provider: mock}\nmemory: {enabled: false}\n"
    ),
    "archetypes/developer.yaml": (
        "apiVersion: swarmkit/v1\nkind: Archetype\n"
        "metadata: {id: developer, name: Developer, description: A harness node.}\n"
        "role: root\nexecutor: {kind: harness, ref: claude-code}\n"
        f"defaults:\n  prompt: {{system: '{SYSTEM}'}}\n"
        "provenance: {authored_by: human, version: 1.0.0}\n"
    ),
    "topologies/change.yaml": (
        "apiVersion: swarmkit/v1\nkind: Topology\nmetadata: {name: change, version: 0.1.0}\n"
        "agents:\n  root: {id: dev, role: root, archetype: developer}\n"
    ),
}


@pytest.mark.asyncio
@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="needs git")
async def test_a_run_delivers_the_archetype_prompt_to_the_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess  # noqa: PLC0415

    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    ws = tmp_path / "ws"
    for rel, text in _WS.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
    for args in (["init", "-q"], [*ident, "add", "."], [*ident, "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(_FAKE_HARNESS)
    (bin_dir / "claude").chmod(0o755)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("ARGV_LOG", str(log))
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = await WorkspaceRuntime.from_workspace_path(ws).run("change", STATEMENT)

    argv = [a.strip("\n") for a in log.read_text().split("<<ARG>>\n") if a]
    assert argv[argv.index("--append-system-prompt") + 1] == SYSTEM
    assert argv[argv.index("-p") + 1] == STATEMENT
    started = [
        e
        for e in getattr(result, "events", [])
        if getattr(e, "event_type", "") == "executor.started"
    ]
    assert started, [getattr(e, "event_type", "") for e in getattr(result, "events", [])]
    payload = getattr(started[0], "payload", {}) or {}
    assert payload.get("system_prompt") == {"delivery": "native", "chars": len(SYSTEM)}
