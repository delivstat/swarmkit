"""Tests for workspace memory store and hooks.

Covers:
- MemoryStore CRUD (add, search, list, get, delete)
- TF-IDF search ranking
- User-scoped queries
- Memory persistence (save/load)
- Memory hooks (extract_and_save, retrieve_context)
- Memory gate integration
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from swarmkit_runtime.memory._store import MemoryEntry, MemoryStore


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path)


# ---------------------------------------------------------------------------
# MemoryStore CRUD
# ---------------------------------------------------------------------------


def test_add_and_count(store: MemoryStore) -> None:
    assert store.count() == 0
    store.add(MemoryEntry(id="m1", topic="grief", tags=["loss"]))
    assert store.count() == 1


def test_get(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="grief"))
    entry = store.get("m1")
    assert entry is not None
    assert entry.topic == "grief"
    assert store.get("nonexistent") is None


def test_list_all(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="grief"))
    store.add(MemoryEntry(id="m2", topic="duty"))
    entries = store.list_all()
    assert len(entries) == 2
    assert entries[0].id == "m2"


def test_delete(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="grief"))
    assert store.delete("m1") is True
    assert store.count() == 0
    assert store.delete("nonexistent") is False


def test_delete_user(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", user="alice", topic="grief"))
    store.add(MemoryEntry(id="m2", user="alice", topic="duty"))
    store.add(MemoryEntry(id="m3", user="bob", topic="karma"))
    assert store.delete_user("alice") == 2
    assert store.count() == 1
    assert store.list_all()[0].user == "bob"


def test_auto_generated_id(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="", topic="test"))
    entries = store.list_all()
    assert entries[0].id.startswith("mem-")


def test_auto_generated_timestamp(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="test"))
    entry = store.get("m1")
    assert entry is not None
    assert entry.created_at != ""


# ---------------------------------------------------------------------------
# User scoping
# ---------------------------------------------------------------------------


def test_count_by_user(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", user="alice", topic="grief"))
    store.add(MemoryEntry(id="m2", user="bob", topic="duty"))
    assert store.count(user="alice") == 1
    assert store.count(user="bob") == 1


def test_list_by_user(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", user="alice", topic="grief"))
    store.add(MemoryEntry(id="m2", user="bob", topic="duty"))
    store.add(MemoryEntry(id="m3", user=None, topic="shared"))
    entries = store.list_all(user="alice")
    assert len(entries) == 2
    topics = {e.topic for e in entries}
    assert "grief" in topics
    assert "shared" in topics


def test_search_by_user(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", user="alice", topic="grief and loss"))
    store.add(MemoryEntry(id="m2", user="bob", topic="grief and sorrow"))
    results = store.search("grief", user="alice")
    assert len(results) == 1
    assert results[0][0].user == "alice"


# ---------------------------------------------------------------------------
# TF-IDF search
# ---------------------------------------------------------------------------


def test_search_basic(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="grief and loss", key_points=["katha upanishad"]))
    store.add(MemoryEntry(id="m2", topic="career duty", key_points=["bhagavad gita"]))
    store.add(MemoryEntry(id="m3", topic="letting go attachment", key_points=["isha upanishad"]))

    results = store.search("grief")
    assert len(results) >= 1
    assert results[0][0].id == "m1"


def test_search_ranking(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="career guidance", context="user asking about job change"))
    store.add(
        MemoryEntry(
            id="m2",
            topic="career duty dharma",
            context="deep discussion about career duty and dharma in work life career",
        )
    )
    results = store.search("career duty")
    assert len(results) >= 2
    assert results[0][0].id == "m2"


def test_search_empty_query(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="test"))
    assert store.search("") == []


def test_search_no_results(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="grief"))
    results = store.search("quantum physics", min_score=0.5)
    assert len(results) == 0


def test_search_tags(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", topic="session", tags=["attachment", "letting-go"]))
    store.add(MemoryEntry(id="m2", topic="session", tags=["career", "duty"]))
    results = store.search("attachment")
    assert len(results) >= 1
    assert results[0][0].id == "m1"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persistence(tmp_path: Path) -> None:
    store1 = MemoryStore(tmp_path)
    store1.add(MemoryEntry(id="m1", topic="grief", user="alice"))
    store1.add(MemoryEntry(id="m2", topic="duty", user="bob"))

    store2 = MemoryStore(tmp_path)
    assert store2.count() == 2
    entry = store2.get("m1")
    assert entry is not None
    assert entry.topic == "grief"


def test_persistence_after_delete(tmp_path: Path) -> None:
    store1 = MemoryStore(tmp_path)
    store1.add(MemoryEntry(id="m1", topic="grief"))
    store1.delete("m1")

    store2 = MemoryStore(tmp_path)
    assert store2.count() == 0


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_get_status(store: MemoryStore) -> None:
    store.add(MemoryEntry(id="m1", user="alice", topic="grief", tags=["loss", "katha"]))
    store.add(MemoryEntry(id="m2", user="bob", topic="duty", tags=["gita", "karma"]))
    store.add(MemoryEntry(id="m3", user="alice", topic="letting go", tags=["loss", "isha"]))

    status = store.get_status()
    assert status["total_entries"] == 3
    assert set(status["users"]) == {"alice", "bob"}
    assert ("loss", 2) in status["top_tags"]


# ---------------------------------------------------------------------------
# Memory hooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_context(store: MemoryStore) -> None:
    from swarmkit_runtime.memory._hooks import retrieve_context  # noqa: PLC0415

    store.add(
        MemoryEntry(
            id="m1",
            user="alice",
            session_id="conv-12",
            topic="grief and loss",
            context="user lost a close friend",
            key_points=["Katha 2.19 — the self neither kills nor is killed"],
        )
    )

    result = retrieve_context(
        user_input="dealing with grief and loss of a friend",
        user="alice",
        store=store,
    )
    assert result is not None
    assert "grief" in result.lower() or "loss" in result.lower()
    assert "WORKSPACE MEMORY" in result


@pytest.mark.asyncio
async def test_retrieve_context_no_results(store: MemoryStore) -> None:
    from swarmkit_runtime.memory._hooks import retrieve_context  # noqa: PLC0415

    result = retrieve_context(
        user_input="quantum physics",
        user="alice",
        store=store,
    )
    assert result is None


@pytest.mark.asyncio
async def test_extract_and_save(store: MemoryStore) -> None:
    from swarmkit_runtime.memory._hooks import extract_and_save  # noqa: PLC0415

    mock_provider = AsyncMock()
    mock_response = AsyncMock()
    mock_response.text = (
        '{"topic": "grief", "context": "lost a friend", '
        '"key_points": ["impermanence"], "tags": ["grief", "loss"], '
        '"worth_saving": true}'
    )
    mock_provider.complete.return_value = mock_response

    entry = await extract_and_save(
        user_input="I lost my friend",
        agent_output="The Katha Upanishad teaches that the self is eternal...",
        agent_id="advisor",
        session_id="conv-12",
        user="alice",
        store=store,
        model_provider=mock_provider,
        model_name="test-model",
    )

    assert entry is not None
    assert entry.topic == "grief"
    assert entry.user == "alice"
    assert "loss" in entry.tags
    assert store.count() == 1


@pytest.mark.asyncio
async def test_extract_not_worth_saving(store: MemoryStore) -> None:
    from swarmkit_runtime.memory._hooks import extract_and_save  # noqa: PLC0415

    mock_provider = AsyncMock()
    mock_response = AsyncMock()
    mock_response.text = '{"topic": "greeting", "worth_saving": false}'
    mock_provider.complete.return_value = mock_response

    entry = await extract_and_save(
        user_input="hello",
        agent_output="Hello! How can I help you today with questions about Vedanta?",
        agent_id="advisor",
        session_id="conv-1",
        user="alice",
        store=store,
        model_provider=mock_provider,
        model_name="test-model",
    )

    assert entry is None
    assert store.count() == 0


@pytest.mark.asyncio
async def test_extract_short_output_skipped(store: MemoryStore) -> None:
    from swarmkit_runtime.memory._hooks import extract_and_save  # noqa: PLC0415

    mock_provider = AsyncMock()

    entry = await extract_and_save(
        user_input="hi",
        agent_output="Hello!",
        agent_id="advisor",
        session_id="conv-1",
        user="alice",
        store=store,
        model_provider=mock_provider,
        model_name="test-model",
    )

    assert entry is None
    mock_provider.complete.assert_not_called()


# ---------------------------------------------------------------------------
# Memory gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_pre_input_injects_context(store: MemoryStore) -> None:
    from swarmkit_runtime.governance import DecisionSkillBinding  # noqa: PLC0415
    from swarmkit_runtime.memory._gate import memory_pre_input  # noqa: PLC0415

    store.add(
        MemoryEntry(
            id="m1",
            user="alice",
            topic="grief discussion",
            context="user dealing with loss",
            key_points=["found comfort in Katha Upanishad"],
        )
    )

    bindings = [
        DecisionSkillBinding(
            id="memory-reader", trigger="pre_input", config={"search_scope": "user"}
        ),
    ]

    context = await memory_pre_input(
        agent_id="advisor",
        user_input="dealing with grief again",
        bindings=bindings,
        store=store,
        user="alice",
    )

    assert context is not None
    assert "grief" in context.lower()


@pytest.mark.asyncio
async def test_memory_pre_input_no_binding(store: MemoryStore) -> None:
    from swarmkit_runtime.governance import DecisionSkillBinding  # noqa: PLC0415
    from swarmkit_runtime.memory._gate import memory_pre_input  # noqa: PLC0415

    bindings = [
        DecisionSkillBinding(id="other-skill", trigger="pre_input"),
    ]

    context = await memory_pre_input(
        agent_id="advisor",
        user_input="test",
        bindings=bindings,
        store=store,
    )

    assert context is None


@pytest.mark.asyncio
async def test_memory_post_output_saves(store: MemoryStore) -> None:
    from swarmkit_runtime.governance import DecisionSkillBinding  # noqa: PLC0415
    from swarmkit_runtime.memory._gate import memory_post_output  # noqa: PLC0415

    mock_provider = AsyncMock()
    mock_response = AsyncMock()
    mock_response.text = (
        '{"topic": "attachment", "context": "user asking about detachment", '
        '"key_points": ["Isha Upanishad"], "tags": ["attachment"], '
        '"worth_saving": true}'
    )
    mock_provider.complete.return_value = mock_response

    bindings = [
        DecisionSkillBinding(id="memory-writer", trigger="post_output"),
    ]

    result = await memory_post_output(
        agent_id="advisor",
        user_input="how to let go of attachment",
        agent_output="The Isha Upanishad teaches..." + "x" * 80,
        bindings=bindings,
        store=store,
        model_provider=mock_provider,
        model_name="test-model",
        session_id="conv-28",
        user="alice",
    )

    assert result.verdict == "pass"
    assert result.raw.get("memory_saved") is True
    assert store.count() == 1
