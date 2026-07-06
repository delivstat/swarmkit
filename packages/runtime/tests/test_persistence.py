"""Tests for SQLite persistence layer.

Covers:
- Job CRUD (create, update, get, list)
- Conversation CRUD (create, update, get, list, delete)
- Usage tracking (record, summary, by-model)
- Persistence across store instances (restart simulation)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.persistence._store import (
    SqliteStore,
    UsageRow,
)


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def test_create_and_get_job(store: SqliteStore) -> None:
    job = store.create_job("j1", "hello", "test input")
    assert job.id == "j1"
    assert job.status == "pending"

    fetched = store.get_job("j1")
    assert fetched is not None
    assert fetched.topology == "hello"
    assert fetched.input == "test input"


def test_update_job(store: SqliteStore) -> None:
    store.create_job("j1", "hello", "input")
    store.update_job(
        "j1",
        status="completed",
        output="result text",
        completed_at="2026-05-28T00:00:00",
        events=["started", "completed"],
    )
    job = store.get_job("j1")
    assert job is not None
    assert job.status == "completed"
    assert job.output == "result text"
    assert job.events == ["started", "completed"]


def test_update_job_usage(store: SqliteStore) -> None:
    store.create_job("j1", "hello", "input")
    store.update_job(
        "j1",
        usage_input_tokens=1500,
        usage_output_tokens=500,
        usage_cost_usd=0.0025,
    )
    job = store.get_job("j1")
    assert job is not None
    assert job.usage_input_tokens == 1500
    assert job.usage_output_tokens == 500
    assert job.usage_cost_usd == pytest.approx(0.0025)


def test_list_jobs(store: SqliteStore) -> None:
    store.create_job("j1", "hello", "a")
    store.create_job("j2", "hello", "b")
    store.create_job("j3", "world", "c")
    jobs = store.list_jobs()
    assert len(jobs) == 3


def test_get_nonexistent_job(store: SqliteStore) -> None:
    assert store.get_job("nonexistent") is None


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def test_create_and_get_conversation(store: SqliteStore) -> None:
    conv = store.create_conversation("c1", "hello")
    assert conv.id == "c1"
    assert conv.topology == "hello"
    assert conv.turns == []

    fetched = store.get_conversation("c1")
    assert fetched is not None
    assert fetched.topology == "hello"


def test_update_conversation_turns(store: SqliteStore) -> None:
    store.create_conversation("c1", "hello")
    turns = [
        {"role": "human", "content": "Hi"},
        {"role": "swarm", "content": "Hello!"},
    ]
    store.update_conversation("c1", turns)

    conv = store.get_conversation("c1")
    assert conv is not None
    assert len(conv.turns) == 2
    assert conv.turns[0]["role"] == "human"


def test_list_conversations(store: SqliteStore) -> None:
    store.create_conversation("c1", "hello")
    store.create_conversation("c2", "world")
    convs = store.list_conversations()
    assert len(convs) == 2


def test_delete_conversation(store: SqliteStore) -> None:
    store.create_conversation("c1", "hello")
    assert store.delete_conversation("c1") is True
    assert store.get_conversation("c1") is None
    assert store.delete_conversation("nonexistent") is False


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------


def test_record_and_summarize_usage(store: SqliteStore) -> None:
    store.create_job("j1", "hello", "input")
    store.record_usage(
        UsageRow(
            job_id="j1",
            agent_id="root",
            model="kimi-k2.6",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.002,
        )
    )
    store.record_usage(
        UsageRow(
            job_id="j1",
            agent_id="worker",
            model="kimi-k2.6",
            input_tokens=800,
            output_tokens=300,
            cost_usd=0.001,
        )
    )

    summary = store.get_usage_summary(job_id="j1")
    assert summary["total_calls"] == 2
    assert summary["total_input_tokens"] == 1800
    assert summary["total_output_tokens"] == 800
    assert summary["total_cost_usd"] == pytest.approx(0.003)


def test_usage_by_model(store: SqliteStore) -> None:
    store.record_usage(UsageRow(agent_id="a", model="kimi-k2.6", input_tokens=1000, cost_usd=0.002))
    store.record_usage(
        UsageRow(agent_id="b", model="claude-haiku", input_tokens=500, cost_usd=0.0005)
    )
    store.record_usage(UsageRow(agent_id="c", model="kimi-k2.6", input_tokens=2000, cost_usd=0.004))

    by_model = store.get_usage_by_model()
    assert len(by_model) == 2
    kimi = next(m for m in by_model if m["model"] == "kimi-k2.6")
    assert kimi["calls"] == 2
    assert kimi["input_tokens"] == 3000


def test_global_usage_summary(store: SqliteStore) -> None:
    store.record_usage(UsageRow(agent_id="a", model="m1", input_tokens=100, cost_usd=0.01))
    store.record_usage(UsageRow(agent_id="b", model="m2", input_tokens=200, cost_usd=0.02))
    summary = store.get_usage_summary()
    assert summary["total_calls"] == 2
    assert summary["total_cost_usd"] == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# Persistence across restarts
# ---------------------------------------------------------------------------


def test_jobs_persist_across_restart(tmp_path: Path) -> None:
    store1 = SqliteStore(tmp_path)
    store1.create_job("j1", "hello", "input")
    store1.update_job("j1", status="completed", output="done")

    store2 = SqliteStore(tmp_path)
    job = store2.get_job("j1")
    assert job is not None
    assert job.status == "completed"
    assert job.output == "done"


def test_conversations_persist_across_restart(tmp_path: Path) -> None:
    store1 = SqliteStore(tmp_path)
    store1.create_conversation("c1", "hello")
    store1.update_conversation("c1", [{"role": "human", "content": "test"}])

    store2 = SqliteStore(tmp_path)
    conv = store2.get_conversation("c1")
    assert conv is not None
    assert len(conv.turns) == 1


def test_usage_persists_across_restart(tmp_path: Path) -> None:
    store1 = SqliteStore(tmp_path)
    store1.record_usage(UsageRow(agent_id="a", model="m1", input_tokens=500, cost_usd=0.01))

    store2 = SqliteStore(tmp_path)
    summary = store2.get_usage_summary()
    assert summary["total_calls"] == 1
    assert summary["total_cost_usd"] == pytest.approx(0.01)
