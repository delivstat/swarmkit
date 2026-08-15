"""A skill can be declared a precondition of another, and the runtime refuses until it is met.

`design/details/skill-prerequisites.md`. The evidence for enforcing rather than asking is a
controlled comparison from one workspace: in a **single run, same agent, same prompt**, an ack-gated
tool was called 4 times and a merely-requested one 0 times. The variable is not the prompt; it is
whether the tool refuses service. So ordering has to be mechanism, and the mechanism belongs at the
permission seam both executors already dispatch through rather than in seven MCP server files
behind an HMAC handshake.

What these tests pin:

* the refusal happens, and its **message is actionable** — that is the part doing the work;
* the agent **recovers inside one loop**, asserted through the real tool-call handler;
* only a *successful* call satisfies, and only for the *same* `(run, agent)`;
* the model path and the harness gateway enforce identically — one seam, asserted twice;
* a refusal is auditable as a deny, so a working gate is distinguishable from one never reached;
* the rules are validated at resolution, because a rule naming a skill the agent lacks guards
  nothing, and a cycle blocks an agent forever with no error anywhere.
"""

from __future__ import annotations

# The doubles duck-type MCPClientManager / GovernanceProvider / CompletionResponse without
# inheriting them, as `test_mcp_gateway.py` does.
# mypy: disable-error-code="arg-type"
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime import prerequisites
from swarmkit_runtime.langgraph_compiler._skill_executor import (
    DENIED_MARK,
    execute_skill,
    is_refusal,
)
from swarmkit_runtime.langgraph_compiler._tool_loop import _handle_skill_tool_calls
from swarmkit_runtime.mcp._gateway import GatewayTool, build_gateway_tools
from swarmkit_runtime.mcp._governed import MCPCallDenied, check_mcp_permission, governed_mcp_call
from swarmkit_runtime.resolver import resolve_workspace

RUN = "run-1"
REQUIRES: dict[str, tuple[str, ...]] = {"get-build-convention": ("list-build-conventions",)}


@pytest.fixture(autouse=True)
def _clean_ledger() -> Any:
    """The ledger is process-wide by necessity (the gateway serves off the run's context), so each
    test starts from nothing."""
    prerequisites.forget_run(RUN)
    prerequisites.forget_run(None)
    yield
    prerequisites.forget_run(RUN)
    prerequisites.forget_run(None)


# ---- the refusal, and the message ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_guarded_skill_is_refused_until_its_prerequisite_has_run() -> None:
    allowed, reason = await check_mcp_permission(
        None,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="get_build_convention",
        skill_id="get-build-convention",
        requires=REQUIRES,
        run_id=RUN,
    )

    assert allowed is False
    assert "list-build-conventions" in reason


@pytest.mark.asyncio
async def test_the_refusal_says_what_to_do_next() -> None:
    """A generic "permission denied" invites give-up or thrash. The message naming the missing
    prerequisite is what makes the agent recover, so it is specified rather than left to
    implementation."""
    _, reason = await check_mcp_permission(
        None,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="get_build_convention",
        skill_id="get-build-convention",
        requires=REQUIRES,
        run_id=RUN,
    )

    assert reason == (
        "get-build-convention requires list-build-conventions, which has not been called in "
        "this session. Call list-build-conventions first, then retry."
    )


@pytest.mark.asyncio
async def test_an_unguarded_skill_is_untouched() -> None:
    allowed, reason = await check_mcp_permission(
        None,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="list_build_conventions",
        skill_id="list-build-conventions",
        requires=REQUIRES,
        run_id=RUN,
    )

    assert (allowed, reason) == (True, "")


@pytest.mark.asyncio
async def test_a_satisfied_prerequisite_opens_the_gate() -> None:
    prerequisites.note_satisfied(run_id=RUN, agent_id="builder", skill_id="list-build-conventions")

    allowed, _ = await check_mcp_permission(
        None,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="get_build_convention",
        skill_id="get-build-convention",
        requires=REQUIRES,
        run_id=RUN,
    )

    assert allowed is True


# ---- scope: per (run, agent) --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_agent_does_not_satisfy_this_agent_s_prerequisite() -> None:
    """A prerequisite is about what is in THIS agent's context. Run-scoped, a parallel sibling
    would satisfy a prerequisite it never saw."""
    prerequisites.note_satisfied(
        run_id=RUN, agent_id="researcher", skill_id="list-build-conventions"
    )

    allowed, _ = await check_mcp_permission(
        None,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="get_build_convention",
        skill_id="get-build-convention",
        requires=REQUIRES,
        run_id=RUN,
    )

    assert allowed is False


@pytest.mark.asyncio
async def test_a_later_run_starts_closed() -> None:
    prerequisites.note_satisfied(run_id=RUN, agent_id="builder", skill_id="list-build-conventions")

    allowed, _ = await check_mcp_permission(
        None,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="get_build_convention",
        skill_id="get-build-convention",
        requires=REQUIRES,
        run_id="run-2",
    )

    assert allowed is False
    prerequisites.forget_run("run-2")


def test_a_finished_run_is_forgotten() -> None:
    """Nothing else drops these entries, so a long-lived `swarmkit serve` would keep one per
    (run, agent) of every run it ever ran."""
    prerequisites.note_satisfied(run_id=RUN, agent_id="builder", skill_id="a")
    assert prerequisites.satisfied_for(RUN, "builder") == {"a"}

    prerequisites.forget_run(RUN)

    assert prerequisites.satisfied_for(RUN, "builder") == frozenset()


# ---- peers are order-independent ----------------------------------------------------------------


@pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
def test_two_prerequisites_are_satisfied_in_either_order(order: tuple[str, str]) -> None:
    requires = {"guarded": ("a", "b")}
    for skill_id in order:
        prerequisites.note_satisfied(run_id=RUN, agent_id="builder", skill_id=skill_id)

    assert prerequisites.missing(requires, run_id=RUN, agent_id="builder", skill_id="guarded") == ()


def test_one_of_two_is_not_enough() -> None:
    prerequisites.note_satisfied(run_id=RUN, agent_id="builder", skill_id="a")

    unmet = prerequisites.missing(
        {"guarded": ("a", "b")}, run_id=RUN, agent_id="builder", skill_id="guarded"
    )

    assert unmet == ("b",)


# ---- only a SUCCESSFUL call satisfies -----------------------------------------------------------


class _Result:
    def __init__(self, *, is_error: bool = False) -> None:
        self.content = [type("B", (), {"text": "conventions"})()]
        self.isError = is_error


class _Resp:
    def __init__(self, *, is_error: bool = False) -> None:
        self.data = _Result(is_error=is_error)
        self.metadata = type("M", (), {"source": "wms", "duration_ms": 1})()


class _Mgr:
    """Duck-types MCPClientManager. `raises` and `is_error` are the two ways a call can fail to
    teach the agent anything."""

    def __init__(self, *, raises: bool = False, is_error: bool = False) -> None:
        self._raises, self._is_error = raises, is_error
        self.calls: list[tuple[str, str]] = []
        self._cache_hits = self._cache_misses = 0

    def get_permission(self, server_id: str, tool_name: str) -> str:
        return "open"

    def get_tool_input_schema(self, *_a: Any) -> dict[str, Any]:
        return {"type": "object"}

    def get_server_cwd(self, *_a: Any) -> str | None:
        return None

    def get_cached_result(self, *_a: Any) -> str | None:
        return None

    def cache_result(self, *_a: Any) -> None: ...

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> _Resp:
        self.calls.append((server_id, tool_name))
        if self._raises:
            raise RuntimeError("server down")
        return _Resp(is_error=self._is_error)


@pytest.mark.asyncio
async def test_a_call_that_raised_does_not_satisfy() -> None:
    manager = _Mgr(raises=True)

    with pytest.raises(RuntimeError):
        await governed_mcp_call(
            manager,
            None,
            agent_id="builder",
            server_id="wms",
            tool_name="list_build_conventions",
            skill_id="list-build-conventions",
            run_id=RUN,
        )

    assert prerequisites.satisfied_for(RUN, "builder") == frozenset()


@pytest.mark.asyncio
async def test_a_server_flagged_error_does_not_satisfy() -> None:
    """`isError` is the honest boundary the seam can see. A tool that returns prose saying "not
    found" as a SUCCESSFUL result will satisfy its prerequisite — stated in the design rather than
    implied away, because the runtime cannot read a payload's meaning."""
    manager = _Mgr(is_error=True)

    await governed_mcp_call(
        manager,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="list_build_conventions",
        skill_id="list-build-conventions",
        run_id=RUN,
    )

    assert prerequisites.satisfied_for(RUN, "builder") == frozenset()


@pytest.mark.asyncio
async def test_a_successful_call_satisfies() -> None:
    manager = _Mgr()

    await governed_mcp_call(
        manager,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="list_build_conventions",
        skill_id="list-build-conventions",
        run_id=RUN,
    )

    assert prerequisites.satisfied_for(RUN, "builder") == {"list-build-conventions"}


# ---- one seam: the harness gateway enforces the same rule ---------------------------------------


@pytest.mark.asyncio
async def test_the_gateway_path_refuses_the_same_call() -> None:
    """The model path and the harness path dispatch through one function, so a rule that held on
    one executor and not the other would make the guarantee depend on how a node happens to run."""
    manager = _Mgr()

    with pytest.raises(MCPCallDenied) as exc:
        await governed_mcp_call(
            manager,
            None,
            agent_id="builder",
            server_id="wms",
            tool_name="get_build_convention",
            skill_id="get-build-convention",
            requires=REQUIRES,
            run_id=RUN,
        )

    assert "list-build-conventions" in str(exc.value)
    assert manager.calls == [], "the server is never touched — enforcement is at dispatch"


@pytest.mark.asyncio
async def test_the_gateway_path_recovers_the_same_way() -> None:
    manager = _Mgr()

    await governed_mcp_call(
        manager,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="list_build_conventions",
        skill_id="list-build-conventions",
        requires=REQUIRES,
        run_id=RUN,
    )
    await governed_mcp_call(
        manager,
        None,
        agent_id="builder",
        server_id="wms",
        tool_name="get_build_convention",
        skill_id="get-build-convention",
        requires=REQUIRES,
        run_id=RUN,
    )

    assert manager.calls == [("wms", "list_build_conventions"), ("wms", "get_build_convention")]


def test_a_gateway_tool_carries_the_skill_it_was_granted_as() -> None:
    """`requires:` names skills and this seam sees tools. Without the mapping the harness path
    would advertise guarded tools and enforce nothing."""

    class _Schemas:
        def get_tool_input_schema(self, *_a: Any) -> dict[str, Any]:
            return {"type": "object"}

    tools = build_gateway_tools(
        [("wms", "get_build_convention", "", "get-build-convention")],
        _Schemas(),
    )

    assert tools == [
        GatewayTool(
            name="wms__get_build_convention",
            server_id="wms",
            tool_name="get_build_convention",
            description="get_build_convention on wms",
            input_schema={"type": "object"},
            skill_id="get-build-convention",
        )
    ]


def test_the_gateway_enforces_with_the_run_captured_at_registration() -> None:
    """The gateway serves tool calls on uvicorn's tasks, which do not inherit the run scope. Read
    at call time it would be empty, and the ledger would key on "" across concurrent runs."""
    src = (Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/mcp/_gateway.py").read_text()

    assert "requires=self._requires" in src
    assert "run_id=self._run_id" in src


# ---- the agent recovers inside one loop ---------------------------------------------------------


class _Block:
    def __init__(self, tool_name: str) -> None:
        self.type = "tool_use"
        self.tool_name = tool_name
        self.tool_use_id = f"call-{tool_name}"
        self.tool_input: dict[str, Any] = {}


class _Response:
    """Duck-types CompletionResponse's `content` — the only field the tool-call handler reads."""

    def __init__(self, *tool_names: str) -> None:
        self.content = [_Block(n) for n in tool_names]


def _skill(skill_id: str, tool: str) -> Any:
    from swarmkit_schema.models import SwarmKitSkill  # noqa: PLC0415

    raw = SwarmKitSkill.model_validate(
        {
            "apiVersion": "swarmkit/v1",
            "kind": "Skill",
            "metadata": {
                "id": skill_id,
                "name": skill_id,
                "description": f"The {skill_id} capability, for the prerequisite tests.",
            },
            "category": "capability",
            "implementation": {"type": "mcp_tool", "server": "wms", "tool": tool},
            "provenance": {"authored_by": "human", "version": "1.0.0"},
        }
    )
    from swarmkit_runtime.skills import ResolvedSkill  # noqa: PLC0415

    return ResolvedSkill(id=skill_id, raw=raw, source_path=Path("skills") / f"{skill_id}.yaml")


def _agent(requires: dict[str, tuple[str, ...]]) -> Any:
    from swarmkit_runtime.resolver import ResolvedAgent  # noqa: PLC0415

    return ResolvedAgent(
        id="builder",
        role="worker",
        model={"provider": "mock", "name": "mock"},
        prompt=None,
        skills=(
            _skill("list-build-conventions", "list_build_conventions"),
            _skill("get-build-convention", "get_build_convention"),
        ),
        iam=None,
        requires=requires,
    )


class _Gov:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def evaluate_action(self, **_: Any) -> Any:
        from swarmkit_runtime.governance import PolicyDecision  # noqa: PLC0415

        return PolicyDecision(allowed=True, reason="", tier=1)

    async def record_event(self, event: Any) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_the_agent_recovers_within_one_loop() -> None:
    """The whole feature, through the real tool-call handler: the agent reaches for the guarded
    skill first, is refused with an actionable message, calls the prerequisite, retries, succeeds.

    Asserted here rather than as three separate unit calls because recoverability — not refusal —
    is what makes the gate usable.
    """
    manager, governance = _Mgr(), _Gov()

    results = await _handle_skill_tool_calls(
        _Response("get-build-convention", "list-build-conventions", "get-build-convention"),
        _agent(REQUIRES),
        model_provider=None,
        model_name="mock",
        mcp_manager=manager,
        governance=governance,
    )

    assert results is not None
    refused, prerequisite, retried = results
    assert "requires list-build-conventions" in refused.result
    assert "conventions" in prerequisite.result
    assert not is_refusal(retried.result), "the retry succeeds — this is the recovery"
    assert manager.calls == [
        ("wms", "list_build_conventions"),
        ("wms", "get_build_convention"),
    ], "the refused call never reached the server"


@pytest.mark.asyncio
async def test_a_refusal_is_audited_as_a_deny() -> None:
    """Until refusals are recorded, a gate that is working looks exactly like a gate that is never
    reached — only calls that happened could be counted."""
    governance = _Gov()

    await _handle_skill_tool_calls(
        _Response("get-build-convention", "list-build-conventions"),
        _agent(REQUIRES),
        model_provider=None,
        model_name="mock",
        mcp_manager=_Mgr(),
        governance=governance,
    )

    decisions = [(e.skill_id, e.policy_decision) for e in governance.events]
    assert decisions == [
        ("get-build-convention", "deny"),
        ("list-build-conventions", "allow"),
    ]


@pytest.mark.asyncio
async def test_an_agent_with_no_requires_is_unaffected() -> None:
    manager = _Mgr()

    results = await _handle_skill_tool_calls(
        _Response("get-build-convention"),
        _agent({}),
        model_provider=None,
        model_name="mock",
        mcp_manager=manager,
        governance=_Gov(),
    )

    assert results is not None
    assert not is_refusal(results[0].result)
    assert manager.calls == [("wms", "get_build_convention")]


@pytest.mark.asyncio
async def test_the_model_path_marks_a_refusal_the_reader_looks_for() -> None:
    """The produced mark and the read mark are one constant, or the audit record drifts back to
    recording every refusal as an allow."""
    result = await execute_skill(
        _skill("get-build-convention", "get_build_convention"),
        input_text="{}",
        model_provider=None,
        model_name="mock",
        mcp_manager=_Mgr(),
        governance=None,
        agent_id="builder",
        requires=REQUIRES,
    )

    assert isinstance(result, str)
    assert DENIED_MARK in result
    assert is_refusal(result)


# ---- validation: the reviewable half ------------------------------------------------------------


def _workspace(tmp_path: Path, requires: dict[str, list[str]], *, skills: list[str]) -> Path:
    root = tmp_path / "ws"
    for sub in ("topologies", "skills"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {"apiVersion": "swarmkit/v1", "kind": "Workspace", "metadata": {"id": "w", "name": "w"}}
        )
    )
    for skill_id in ("list-build-conventions", "get-build-convention", "search-solution-code"):
        (root / "skills" / f"{skill_id}.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "swarmkit/v1",
                    "kind": "Skill",
                    "metadata": {
                        "id": skill_id,
                        "name": skill_id,
                        "description": f"The {skill_id} capability, for the prerequisite tests.",
                    },
                    "category": "capability",
                    "implementation": {
                        "type": "mcp_tool",
                        "server": "wms",
                        "tool": skill_id.replace("-", "_"),
                    },
                    "provenance": {"authored_by": "human", "version": "1.0.0"},
                }
            )
        )
    (root / "topologies" / "build.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Topology",
                "metadata": {"name": "build", "version": "0.1.0"},
                "agents": {
                    "root": {
                        "id": "builder",
                        "role": "root",
                        "model": {"provider": "mock", "name": "mock"},
                        "skills": skills,
                        "requires": requires,
                    }
                },
            }
        )
    )
    return root


def _errors(root: Path) -> list[Any]:
    from swarmkit_runtime.errors import ResolutionErrors  # noqa: PLC0415

    try:
        resolve_workspace(root)
    except ResolutionErrors as exc:
        return list(exc.errors)
    return []


def test_a_valid_requires_resolves(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {"get-build-convention": ["list-build-conventions"]},
        skills=["list-build-conventions", "get-build-convention"],
    )

    workspace = resolve_workspace(root)

    assert workspace.topologies["build"].root.requires == {
        "get-build-convention": ("list-build-conventions",)
    }


def test_a_requires_naming_an_ungranted_skill_is_an_error(tmp_path: Path) -> None:
    """The check that makes the block trustworthy: a rule over a skill the agent does not hold
    guards nothing, and reads as though it does."""
    root = _workspace(
        tmp_path,
        {"get-build-convention": ["search-solution-code"]},
        skills=["list-build-conventions", "get-build-convention"],
    )

    codes = [e.code for e in _errors(root)]

    assert "agent.requires-unknown-skill" in codes


def test_a_cycle_is_an_error(tmp_path: Path) -> None:
    """`a requires b`, `b requires a` permanently blocks both and the agent can never recover —
    unchecked, this feature can render an agent unable to act with no error anywhere."""
    root = _workspace(
        tmp_path,
        {
            "get-build-convention": ["list-build-conventions"],
            "list-build-conventions": ["get-build-convention"],
        },
        skills=["list-build-conventions", "get-build-convention"],
    )

    errors = _errors(root)

    assert "agent.requires-cycle" in [e.code for e in errors]


def test_a_self_reference_is_an_error(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {"get-build-convention": ["get-build-convention"]},
        skills=["list-build-conventions", "get-build-convention"],
    )

    assert "agent.requires-cycle" in [e.code for e in _errors(root)]


def test_a_longer_cycle_is_an_error(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "get-build-convention": ["list-build-conventions"],
            "list-build-conventions": ["search-solution-code"],
            "search-solution-code": ["get-build-convention"],
        },
        skills=["list-build-conventions", "get-build-convention", "search-solution-code"],
    )

    assert "agent.requires-cycle" in [e.code for e in _errors(root)]


def test_a_topology_without_requires_resolves_to_no_rules(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {}, skills=["list-build-conventions"])

    assert resolve_workspace(root).topologies["build"].root.requires == {}


def test_the_schema_accepts_the_block_and_rejects_an_empty_rule() -> None:
    """The fixtures both languages validate against."""
    import jsonschema  # noqa: PLC0415
    from swarmkit_schema import validate  # noqa: PLC0415

    fixtures = Path(__file__).resolve().parents[3] / "packages/schema/tests/fixtures"
    valid = yaml.safe_load((fixtures / "topology/with-requires.yaml").read_text())
    invalid = yaml.safe_load((fixtures / "topology-invalid/requires-empty-list.yaml").read_text())

    validate("topology", valid)
    with pytest.raises(jsonschema.ValidationError):
        validate("topology", invalid)


def test_the_reference_workspace_is_unchanged_by_the_feature() -> None:
    """A topology with no `requires:` produces the same resolved agents it always did — asserted
    against the shipped reference workspace, not a fixture."""
    reference = Path(__file__).resolve().parents[3] / "reference"
    workspace = resolve_workspace(reference)

    def walk(agent: Any) -> list[Any]:
        return [agent, *[a for c in agent.children for a in walk(c)]]

    agents = [a for t in workspace.topologies.values() for a in walk(t.root)]

    assert agents, "the reference workspace has topologies"
    assert all(a.requires == {} for a in agents)


def test_the_serialised_shape_is_stable() -> None:
    """`requires` is a map, so a duplicate rule is impossible and the ordering rules read in one
    block — the reason shape B was chosen over inline entries on `skills`."""
    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "packages/schema/schemas/topology.schema.json"
        ).read_text()
    )

    requires = schema["$defs"]["agent"]["properties"]["requires"]

    assert requires["type"] == "object"
    assert requires["additionalProperties"]["minItems"] == 1
    assert requires["additionalProperties"]["x-swarmkit-ref"] == "skill"


def test_the_rules_reach_a_reader() -> None:
    """A user-authored field only YAML can show is an incomplete schema change — and this one
    changes what the agent is ALLOWED to do. The server projection carries it, and the composer
    renders it beside the grant."""
    services = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_services.py"
    ).read_text()
    composer = (
        Path(__file__).resolve().parents[3] / "packages/ui/app/composer/page.tsx"
    ).read_text()

    assert '"requires": {k: list(v) for k, v in dict(agent.requires).items()}' in services
    assert "requiresEntries" in composer
