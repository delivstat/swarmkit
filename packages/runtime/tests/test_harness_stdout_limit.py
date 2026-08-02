"""A large harness stdout line must not kill the run.

Reported 2026-08-02 against 1.129.0: `create_subprocess_exec` passed no `limit=`, so asyncio used
`_DEFAULT_LIMIT` (65,536 bytes) for the pipe's StreamReader. A harness emitting line-delimited JSON
died the first time a turn carried a large tool result:

    error: execution failed: Separator is found, but chunk is longer than limit

The message named neither the tool, the agent, nor the size, and the run aborted after earlier tool
calls had already succeeded. A 500 KB tool result is ordinary — of the 3,059 sample documents IBM
ships with Sterling 9.5, 122 exceed 60 KB.
"""

from __future__ import annotations

import asyncio.streams
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.executors._adapter_spec import parse_adapter_spec
from swarmkit_runtime.executors._declarative import (
    _STDOUT_LINE_LIMIT,
    DeclarativeExecutor,
)

ADAPTER: dict[str, Any] = {
    "apiVersion": "swarmkit/v1",
    "kind": "ExecutorAdapter",
    "metadata": {"id": "fake", "name": "Fake", "description": "test adapter"},
    "spec": {
        "launch": {"command": ["true"]},
        "event_map": [{"when": {"type": "result"}, "emit": [{"event": "result"}]}],
    },
    "provenance": {"authored_by": "human", "version": "1.0.0"},
}


def _executor() -> DeclarativeExecutor:
    return DeclarativeExecutor(parse_adapter_spec(ADAPTER))


def test_the_asyncio_default_is_the_thing_being_overridden() -> None:
    """Pins WHY the limit is passed: 64 KiB is asyncio's default, not a considered choice."""
    # Private, hence the ignores — but asserting the real attribute is the point: if asyncio ever
    # changes its default, this test should say so rather than quietly agreeing with a constant.
    default = asyncio.streams._DEFAULT_LIMIT  # type: ignore[attr-defined]
    assert default == 65536
    assert default < _STDOUT_LINE_LIMIT


@pytest.mark.asyncio
async def test_a_line_over_64kib_is_read_intact(tmp_path: Path) -> None:
    """The exact failure: one stdout line larger than asyncio's default."""
    size = 200_000
    argv = ["python3", "-c", f"print('x' * {size})"]
    lines = [line async for line in _executor()._open_stream(argv, {}, tmp_path, "run-1")]
    assert len(lines) == 1
    assert len(lines[0].rstrip("\n")) == size


@pytest.mark.asyncio
async def test_a_500kb_line_is_read_intact(tmp_path: Path) -> None:
    """The reported size — a single IBM sample document returned verbatim by an MCP tool."""
    size = 503_931
    argv = ["python3", "-c", f"print('y' * {size})"]
    lines = [line async for line in _executor()._open_stream(argv, {}, tmp_path, "run-2")]
    assert len(lines[0].rstrip("\n")) == size


@pytest.mark.asyncio
async def test_many_normal_lines_still_stream(tmp_path: Path) -> None:
    """The limit is a ceiling, not a buffer size — ordinary transcripts are unaffected."""
    argv = ["python3", "-c", "for i in range(500): print(f'{{\"n\": {i}}}')"]
    lines = [line async for line in _executor()._open_stream(argv, {}, tmp_path, "run-3")]
    assert len(lines) == 500
