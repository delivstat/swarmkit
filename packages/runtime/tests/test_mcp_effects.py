"""Where an MCP tool's read/write nature comes from — issue #825.

It used to come from substring-scanning the tool name for
``create|delete|update|write|put|post|set|add|remove|modify|edit|insert|drop|push|send``.
That failed in both directions at once, which is why a longer list was never the fix:

* ``get_dataset`` and ``read_asset`` matched **set**, ``list_addresses`` matched **add**,
  ``get_post`` matched **post** — ordinary reads, denied.
* ``truncate_table``, ``purge_cache``, ``revoke_token`` and ``wipe_db`` matched nothing —
  destructive, allowed.

The vocabulary of destructive verbs is unbounded and per-server, so it is now declared or admitted
to be unknown.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.mcp._client import MCPClientManager, MCPServerConfig
from swarmkit_runtime.mcp._sdk_compat import tool_read_only_hint


class _Annotations:
    def __init__(self, read_only: bool) -> None:
        self.readOnlyHint = read_only


class _Tool:
    def __init__(self, name: str, read_only: bool | None = None) -> None:
        self.name = name
        self.annotations = None if read_only is None else _Annotations(read_only)


def _manager(**kw: Any) -> MCPClientManager:
    cfg = MCPServerConfig(server_id="s", **kw)
    return MCPClientManager({"s": cfg})


class TestResolutionOrder:
    def test_declared_effects_win(self) -> None:
        mgr = _manager(effects={"anything": "read"})
        assert mgr.get_effects("s", "anything") == "read"

    def test_the_server_annotation_is_used_when_nothing_is_declared(self) -> None:
        mgr = _manager()
        mgr._tool_read_only["s"] = {"peek": True, "poke": False}
        assert mgr.get_effects("s", "peek") == "read"
        assert mgr.get_effects("s", "poke") == "write"

    def test_the_workspace_declaration_overrides_the_server_annotation(self) -> None:
        """The annotation is the server describing itself; the declaration is the half the
        operator controls, and the half that cannot change under them on an upgrade."""
        mgr = _manager(effects={"peek": "write"})
        mgr._tool_read_only["s"] = {"peek": True}
        assert mgr.get_effects("s", "peek") == "write"

    def test_unknown_is_returned_rather_than_guessed(self) -> None:
        mgr = _manager()
        for name in ("get_dataset", "truncate_table", "read_asset", "purge_cache"):
            assert mgr.get_effects("s", name) == "unknown", name

    def test_an_unconfigured_server_is_unknown(self) -> None:
        assert _manager().get_effects("no-such-server", "x") == "unknown"


class TestNamesThatBrokeTheOldHeuristic:
    """Pinned so a future 'improvement' cannot reintroduce name-sniffing quietly."""

    @pytest.mark.parametrize(
        "name", ["get_dataset", "read_asset", "list_addresses", "get_post", "query_offset"]
    )
    def test_ordinary_reads_are_read_when_declared(self, name: str) -> None:
        assert _manager(effects={name: "read"}).get_effects("s", name) == "read"

    @pytest.mark.parametrize(
        "name", ["truncate_table", "purge_cache", "revoke_token", "wipe_db", "clear_all"]
    )
    def test_destructive_tools_are_write_when_declared(self, name: str) -> None:
        assert _manager(effects={name: "write"}).get_effects("s", name) == "write"

    @pytest.mark.parametrize("name", ["truncate_table", "purge_cache", "revoke_token"])
    def test_destructive_tools_are_not_silently_read(self, name: str) -> None:
        """The failure that mattered: allowed under `readonly` because no substring matched."""
        assert _manager().get_effects("s", name) != "read"


class TestAnnotationCompat:
    def test_camel_case_hint_is_read(self) -> None:
        assert tool_read_only_hint(_Tool("x", read_only=True)) is True
        assert tool_read_only_hint(_Tool("x", read_only=False)) is False

    def test_absent_annotations_are_none_not_false(self) -> None:
        """None means 'the server said nothing'; False would mean 'the server said it writes'."""
        assert tool_read_only_hint(_Tool("x")) is None

    def test_snake_case_hint_is_also_read(self) -> None:
        class SnakeAnnotations:
            read_only_hint = True

        class SnakeTool:
            name = "x"
            annotations = SnakeAnnotations()

        assert tool_read_only_hint(SnakeTool()) is True
