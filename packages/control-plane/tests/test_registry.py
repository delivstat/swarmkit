"""Tests for the sqlite instance registry."""

from __future__ import annotations

from pathlib import Path

from swarmkit_control_plane import Instance, SqliteRegistry


def _registry(tmp_path: Path) -> SqliteRegistry:
    return SqliteRegistry(tmp_path / "registry.sqlite")


def test_add_get_roundtrip(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.add(
        Instance(
            id="i1",
            name="minder",
            endpoint="http://10.0.0.5:8321",
            connection="poll",
            token_ref="env:MINDER_TOKEN",
            capabilities={"topologies": ["minder-router"]},
            schema_version="1.6.0",
            health="healthy",
        )
    )
    got = reg.get("i1")
    assert got is not None
    assert got.name == "minder"
    assert got.connection == "poll"
    assert got.capabilities == {"topologies": ["minder-router"]}
    assert got.health == "healthy"
    assert got.created_at  # auto-stamped


def test_list_and_delete(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.add(Instance(id="a", name="a", endpoint="http://a"))
    reg.add(Instance(id="b", name="b", endpoint="http://b"))
    assert {i.id for i in reg.list_all()} == {"a", "b"}
    assert reg.delete("a") is True
    assert reg.delete("a") is False  # already gone
    assert {i.id for i in reg.list_all()} == {"b"}


def test_update_health(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.add(Instance(id="i1", name="x", endpoint="http://x"))
    reg.update_health(
        "i1", health="healthy", schema_version="1.6.0", capabilities={"topologies": []}
    )
    got = reg.get("i1")
    assert got is not None
    assert got.health == "healthy"
    assert got.schema_version == "1.6.0"
    assert got.last_seen is not None


def test_public_dict_hides_token(tmp_path: Path) -> None:
    inst = Instance(id="i1", name="x", endpoint="http://x", token_ref="env:SECRET")
    assert "token_ref" not in inst.public_dict()
