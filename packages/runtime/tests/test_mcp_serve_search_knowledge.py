"""`mcp-serve`'s `search_knowledge` uses a declared search skill, through the governed path (#1133).

It called ``rt._mcp_manager.call_tool(tool.name, {"query": query})`` on the first MCP tool whose
name contained "search". The signature is ``call_tool(server_id, tool_name, arguments)``, so the
tool's name went in as the server id and the arguments dict as the tool name: every query failed.
And it skipped ``governed_mcp_call``, so a corrected call would have reached a workspace tool with
no policy check and no audit, the only MCP door that did.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from swarmkit_runtime.mcp import _governed, _serve


class _Text:
    def __init__(self, *, type: str, text: str) -> None:
        self.type, self.text = type, text


class _Manager:
    def __init__(self) -> None:
        self.direct_calls: list[tuple[Any, ...]] = []

    async def start_all(self) -> None:
        return None

    async def list_tools(self) -> list[Any]:
        return [SimpleNamespace(name="search_docs"), SimpleNamespace(name="search_tickets")]

    async def call_tool(self, *args: Any, **kwargs: Any) -> Any:
        self.direct_calls.append(args)
        raise AssertionError("search_knowledge must not call the manager directly")


def _skill(skill_id: str, server: str, tool: str, scopes: list[str] | None = None) -> Any:
    return SimpleNamespace(
        id=skill_id,
        raw=SimpleNamespace(
            implementation={"type": "mcp_tool", "server": server, "tool": tool},
            iam=SimpleNamespace(required_scopes=scopes or []),
        ),
    )


def _runtime(*skills: Any) -> Any:
    return SimpleNamespace(
        _mcp_manager=_Manager(),
        governance=object(),
        workspace=SimpleNamespace(skills={s.id: s for s in skills}),
    )


@pytest.fixture
def governed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake(manager: Any, governance: Any, **kw: Any) -> Any:
        calls.append(kw)
        block = SimpleNamespace(type="text", text=f"hits for {kw['arguments']['query']}")
        return SimpleNamespace(data=SimpleNamespace(content=[block]))

    monkeypatch.setattr(_governed, "governed_mcp_call", fake)
    return calls


async def _ask(rt: Any, **arguments: Any) -> str:
    out = await _serve._search(rt, arguments, _Text)
    return " ".join(b.text for b in out)


@pytest.mark.asyncio
async def test_one_declared_search_skill_is_called_through_the_governed_path(
    governed: list[dict[str, Any]],
) -> None:
    rt = _runtime(
        _skill("docs-search", "docs", "search_docs", ["knowledge:read"]),
        _skill("read-file", "fs", "read_file"),
    )
    answer = await _ask(rt, query="escalation")

    assert answer == "hits for escalation"
    (call,) = governed
    assert call["server_id"] == "docs" and call["tool_name"] == "search_docs"
    assert call["arguments"] == {"query": "escalation"}
    assert call["skill_id"] == "docs-search"
    assert call["scopes"] == frozenset({"knowledge:read"})
    assert rt._mcp_manager.direct_calls == []


@pytest.mark.asyncio
async def test_several_search_skills_need_a_choice_and_never_a_guess(
    governed: list[dict[str, Any]],
) -> None:
    rt = _runtime(
        _skill("tickets-search", "tickets", "search_tickets"),
        _skill("docs-search", "docs", "search_docs"),
    )
    answer = await _ask(rt, query="refund")
    assert "several search skills (docs-search, tickets-search)" in answer
    assert governed == []

    assert await _ask(rt, query="refund", skill="tickets-search") == "hits for refund"
    assert governed[0]["server_id"] == "tickets"
    assert "Unknown search skill" in await _ask(rt, query="x", skill="nope")


@pytest.mark.asyncio
async def test_no_search_skill_says_so(governed: list[dict[str, Any]]) -> None:
    rt = _runtime(_skill("read-file", "fs", "read_file"))
    assert "No knowledge search is configured" in await _ask(rt, query="x")
    assert governed == []


@pytest.mark.asyncio
async def test_a_policy_refusal_is_reported_not_bypassed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def deny(manager: Any, governance: Any, **kw: Any) -> Any:
        raise _governed.MCPCallDenied("readonly tier refuses an unknown effect")

    monkeypatch.setattr(_governed, "governed_mcp_call", deny)
    rt = _runtime(_skill("docs-search", "docs", "search_docs"))
    answer = await _ask(rt, query="x")
    assert answer.startswith("Search denied by policy")
    assert rt._mcp_manager.direct_calls == []


def test_search_skills_are_the_declared_mcp_search_tools_in_id_order() -> None:
    rt = _runtime(
        _skill("zeta-search", "z", "search_z"),
        _skill("alpha-search", "a", "Search_A"),
        _skill("not-search", "n", "read_file"),
    )
    assert [s.id for s in _serve.search_skills(rt)] == ["alpha-search", "zeta-search"]
