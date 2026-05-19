"""Tests for the local prompt ring buffer (M6 PR 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from swarmkit_runtime.telemetry import PromptRingBuffer


class TestPromptRingBuffer:
    def test_store_and_query_by_span_id(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(db_path=tmp_path / "prompts.sqlite")
        buf.store(
            span_id="span-001",
            run_id="run-abc",
            agent_id="reviewer",
            step=1,
            prompt="Review this code",
            response="The code looks good",
            model="claude-sonnet-4-6",
        )

        result = buf.query_by_span_id("span-001")
        assert result is not None
        assert result["prompt"] == "Review this code"
        assert result["response"] == "The code looks good"
        assert result["model"] == "claude-sonnet-4-6"
        assert result["agent_id"] == "reviewer"
        assert result["step"] == 1
        buf.close()

    def test_query_missing_span_id(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(db_path=tmp_path / "prompts.sqlite")
        assert buf.query_by_span_id("nonexistent") is None
        buf.close()

    def test_query_by_run_id(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(db_path=tmp_path / "prompts.sqlite")
        buf.store(
            span_id="s1",
            run_id="run-1",
            agent_id="a",
            step=1,
            prompt="p1",
            response="r1",
            model="m",
        )
        buf.store(
            span_id="s2",
            run_id="run-1",
            agent_id="a",
            step=2,
            prompt="p2",
            response="r2",
            model="m",
        )
        buf.store(
            span_id="s3",
            run_id="run-2",
            agent_id="a",
            step=1,
            prompt="p3",
            response="r3",
            model="m",
        )

        results = buf.query_by_run_id("run-1")
        assert len(results) == 2
        assert results[0]["step"] == 1
        assert results[1]["step"] == 2

        results = buf.query_by_run_id("run-2")
        assert len(results) == 1
        buf.close()

    def test_query_by_agent(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(db_path=tmp_path / "prompts.sqlite")
        for i in range(10):
            buf.store(
                span_id=f"s{i}",
                run_id="r",
                agent_id="worker",
                step=i,
                prompt=f"p{i}",
                response=f"r{i}",
                model="m",
            )

        results = buf.query_by_agent("worker", last_n=3)
        assert len(results) == 3
        buf.close()

    def test_duplicate_span_id_ignored(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(db_path=tmp_path / "prompts.sqlite")
        buf.store(
            span_id="s1",
            run_id="r",
            agent_id="a",
            step=1,
            prompt="original",
            response="resp",
            model="m",
        )
        buf.store(
            span_id="s1",
            run_id="r",
            agent_id="a",
            step=1,
            prompt="duplicate",
            response="resp2",
            model="m",
        )

        assert buf.count() == 1
        result = buf.query_by_span_id("s1")
        assert result is not None
        assert result["prompt"] == "original"
        buf.close()

    def test_metadata_roundtrip(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(db_path=tmp_path / "prompts.sqlite")
        buf.store(
            span_id="s1",
            run_id="r",
            agent_id="a",
            step=1,
            prompt="p",
            response="r",
            model="m",
            metadata={"tokens_in": 100, "tokens_out": 50, "tool_calls": ["read_file"]},
        )

        result = buf.query_by_span_id("s1")
        assert result is not None
        assert result["metadata"]["tokens_in"] == 100
        assert result["metadata"]["tool_calls"] == ["read_file"]
        buf.close()

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        db = tmp_path / "prompts.sqlite"
        buf1 = PromptRingBuffer(db_path=db)
        buf1.store(
            span_id="s1",
            run_id="r",
            agent_id="a",
            step=1,
            prompt="p",
            response="r",
            model="m",
        )
        buf1.close()

        buf2 = PromptRingBuffer(db_path=db)
        assert buf2.count() == 1
        assert buf2.query_by_span_id("s1") is not None
        buf2.close()

    def test_prune_by_ttl(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(db_path=tmp_path / "prompts.sqlite", retention_days=7)

        old_ts = (datetime.now(tz=UTC) - timedelta(days=10)).isoformat()
        buf._conn.execute(
            """INSERT INTO prompts
               (span_id, run_id, agent_id, step, prompt, response, model, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("old", "r", "a", 1, "p", "r", "m", old_ts),
        )
        buf._conn.commit()

        buf.store(
            span_id="new",
            run_id="r",
            agent_id="a",
            step=2,
            prompt="p",
            response="r",
            model="m",
        )

        pruned = buf.prune_expired()
        assert pruned == 1
        assert buf.count() == 1
        assert buf.query_by_span_id("old") is None
        assert buf.query_by_span_id("new") is not None
        buf.close()

    def test_prune_by_max_entries(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(
            db_path=tmp_path / "prompts.sqlite",
            retention_days=365,
            max_entries=5,
        )

        for i in range(10):
            buf.store(
                span_id=f"s{i}",
                run_id="r",
                agent_id="a",
                step=i,
                prompt=f"p{i}",
                response=f"r{i}",
                model="m",
            )

        pruned = buf.prune_expired()
        assert pruned == 5
        assert buf.count() == 5
        buf.close()

    def test_count(self, tmp_path: Path) -> None:
        buf = PromptRingBuffer(db_path=tmp_path / "prompts.sqlite")
        assert buf.count() == 0
        buf.store(
            span_id="s1",
            run_id="r",
            agent_id="a",
            step=1,
            prompt="p",
            response="r",
            model="m",
        )
        assert buf.count() == 1
        buf.close()
