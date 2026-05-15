# /// script
# dependencies = ["mcp[cli]>=1.0"]
# ///
"""Sterling OMS log analyser MCP server.

Parses Sterling log4j FLAT format logs and provides structured query
tools for performance analysis, call tracing, and SQL debugging.

Supported log levels:
  VERBOSE — full logging with everything
  DEBUG   — same as VERBOSE
  TIMER   — Begin/End pairs for API and function call timing
  SQLDEBUG — TIMER + SQL DB calls for SQL performance

Usage:
    export STERLING_LOG_DIR=/path/to/logs
    uv run sterling_log_server.py
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

server = Server("sterling-log-analyser")

LOG_DIR = os.environ.get("STERLING_LOG_DIR", ".")

LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r":(\w+)\s*"
    r":([^:]+)"
    r":\s*(.*)"
)

TIMER_BEGIN = re.compile(r"^(.+?)::(.+?)\s*-\s*Begin")

TIMER_END = re.compile(r"^(.+?)::(.+?)\s*-\s*End\s*-\s*\[(\d+)\]")

METADATA_PATTERN = re.compile(r"\[([^\]]*)\]:\s*\[([^\]]*)\]:\s*\[([^\]]*)\]:\s*(\S+)\s*$")

SQL_PATTERN = re.compile(
    r"(SELECT|INSERT|UPDATE|DELETE|MERGE)\s+",
    re.IGNORECASE,
)


@dataclass
class LogEntry:
    timestamp: str
    level: str
    thread: str
    message: str
    user: str = ""
    correlation_id: str = ""
    enterprise: str = ""
    class_name: str = ""
    line_no: int = 0


@dataclass
class TimerCall:
    method: str
    class_name: str
    duration_ms: int = 0
    correlation_id: str = ""
    thread: str = ""
    timestamp_begin: str = ""
    timestamp_end: str = ""


@dataclass
class ParsedLog:
    entries: list[LogEntry] = field(default_factory=list)
    timer_calls: list[TimerCall] = field(default_factory=list)
    sql_calls: list[LogEntry] = field(default_factory=list)
    correlation_ids: set[str] = field(default_factory=set)
    file_path: str = ""


_cache: dict[str, ParsedLog] = {}


def _parse_log(file_path: str) -> ParsedLog:  # noqa: PLR0912, PLR0915
    if file_path in _cache:
        return _cache[file_path]

    path = Path(LOG_DIR) / file_path
    if not path.exists():
        path = Path(file_path)
    if not path.exists():
        return ParsedLog(file_path=file_path)

    entries: list[LogEntry] = []
    timer_stack: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    timer_calls: list[TimerCall] = []
    sql_calls: list[LogEntry] = []
    correlation_ids: set[str] = set()

    current_entry: LogEntry | None = None
    multiline_buffer: list[str] = []

    for line_no, raw_line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        m = LOG_PATTERN.match(raw_line)
        if m:
            if current_entry and multiline_buffer:
                current_entry.message += "\n" + "\n".join(multiline_buffer)
                multiline_buffer = []

            timestamp = m.group(1)
            level = m.group(2).strip()
            thread = m.group(3).strip()
            rest = m.group(4)

            user = ""
            corr_id = ""
            enterprise = ""
            cls = ""
            meta = METADATA_PATTERN.search(rest)
            if meta:
                user = meta.group(1)
                corr_id = meta.group(2)
                enterprise = meta.group(3)
                cls = meta.group(4)
                msg = rest[: meta.start()].strip()
            else:
                msg = rest.strip()

            entry = LogEntry(
                timestamp=timestamp,
                level=level,
                thread=thread,
                message=msg,
                user=user,
                correlation_id=corr_id,
                enterprise=enterprise,
                class_name=cls,
                line_no=line_no,
            )
            entries.append(entry)
            current_entry = entry

            if corr_id:
                correlation_ids.add(corr_id)

            if level == "TIMER":
                begin = TIMER_BEGIN.match(msg)
                end = TIMER_END.match(msg)
                if begin:
                    key = f"{thread}:{begin.group(1)}::{begin.group(2)}"
                    timer_stack[key].append((timestamp, corr_id, thread))
                elif end:
                    key = f"{thread}:{end.group(1)}::{end.group(2)}"
                    dur = int(end.group(3))
                    stack = timer_stack.get(key, [])
                    begin_ts = ""
                    begin_corr = corr_id
                    if stack:
                        begin_ts, begin_corr, _ = stack.pop()
                    timer_calls.append(
                        TimerCall(
                            method=end.group(1),
                            class_name=end.group(2),
                            duration_ms=dur,
                            correlation_id=begin_corr or corr_id,
                            thread=thread,
                            timestamp_begin=begin_ts,
                            timestamp_end=timestamp,
                        )
                    )

            if SQL_PATTERN.search(msg):
                sql_calls.append(entry)
        else:
            multiline_buffer.append(raw_line)

    parsed = ParsedLog(
        entries=entries,
        timer_calls=timer_calls,
        sql_calls=sql_calls,
        correlation_ids=correlation_ids,
        file_path=file_path,
    )
    _cache[file_path] = parsed
    return parsed


def _format_timer_table(calls: list[TimerCall], limit: int = 50) -> str:
    if not calls:
        return "No timer calls found."
    lines = [
        f"{'Duration':>10s}  {'Method':<40s}  {'Class':<30s}  {'Correlation ID':<38s}",
        "-" * 130,
    ]
    for tc in sorted(calls, key=lambda c: -c.duration_ms)[:limit]:
        lines.append(
            f"{tc.duration_ms:>8d}ms  {tc.method:<40s}  "
            f"{tc.class_name:<30s}  {tc.correlation_id:<38s}"
        )
    return "\n".join(lines)


TOOLS = [
    Tool(
        name="list_log_files",
        description="List available log files in the log directory.",
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (default: *.log)",
                },
            },
        },
    ),
    Tool(
        name="get_slow_calls",
        description=(
            "Find the slowest API/function calls from TIMER logs. "
            "Returns calls sorted by duration (slowest first). "
            "Use to identify performance bottlenecks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path (relative to log dir)",
                },
                "min_duration_ms": {
                    "type": "integer",
                    "description": "Minimum duration in ms (default: 100)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 30)",
                },
            },
            "required": ["file"],
        },
    ),
    Tool(
        name="get_call_trace",
        description=(
            "Get the complete call trace for a correlation ID. "
            "Shows the execution path with timing for a single "
            "request. Use to trace a specific slow transaction."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path",
                },
                "correlation_id": {
                    "type": "string",
                    "description": "Correlation ID (UUID from log)",
                },
                "level": {
                    "type": "string",
                    "description": "Filter level: TIMER, DEBUG, VERBOSE, ALL (default: TIMER)",
                },
            },
            "required": ["file", "correlation_id"],
        },
    ),
    Tool(
        name="get_sql_calls",
        description=(
            "Extract SQL statements from SQLDEBUG logs. "
            "Shows SQL queries with their execution context. "
            "Use to identify slow or repeated database queries."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path",
                },
                "correlation_id": {
                    "type": "string",
                    "description": "Filter by correlation ID (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 50)",
                },
            },
            "required": ["file"],
        },
    ),
    Tool(
        name="search_logs",
        description=(
            "Search log entries by keyword, level, class, or "
            "correlation ID. Returns matching entries with context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path",
                },
                "keyword": {
                    "type": "string",
                    "description": "Search term in message text",
                },
                "level": {
                    "type": "string",
                    "description": "Filter by level: TIMER, DEBUG, VERBOSE",
                },
                "class_name": {
                    "type": "string",
                    "description": "Filter by class name",
                },
                "correlation_id": {
                    "type": "string",
                    "description": "Filter by correlation ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 50)",
                },
            },
            "required": ["file"],
        },
    ),
    Tool(
        name="get_log_summary",
        description=(
            "Get a high-level summary of a log file: total entries, "
            "entries per level, unique correlation IDs, slowest "
            "calls, and time range."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path",
                },
            },
            "required": ["file"],
        },
    ),
    Tool(
        name="get_api_stats",
        description=(
            "Get timing statistics per API/method from TIMER logs. "
            "Shows count, min, max, avg, total duration for each "
            "method. Use to find which APIs consume most time."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path",
                },
                "correlation_id": {
                    "type": "string",
                    "description": "Filter by correlation ID (optional)",
                },
            },
            "required": ["file"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict,  # type: ignore[type-arg]
) -> list[TextContent]:
    try:
        result = _dispatch(name, arguments)
    except Exception as exc:
        result = f"Error: {exc}"
    return [TextContent(type="text", text=result)]


def _dispatch(  # noqa: PLR0911, PLR0912, PLR0915
    name: str,
    args: dict,  # type: ignore[type-arg]
) -> str:
    if name == "list_log_files":
        pattern = args.get("pattern", "*.log")
        log_dir = Path(LOG_DIR)
        if not log_dir.exists():
            return f"Log directory not found: {LOG_DIR}"
        files = sorted(log_dir.glob(pattern))
        if not files:
            return f"No files matching '{pattern}' in {LOG_DIR}"
        lines = [f"Log files in {LOG_DIR}:\n"]
        for f in files:
            size = f.stat().st_size
            if size > 1024 * 1024:
                sz = f"{size / 1024 / 1024:.1f}MB"
            elif size > 1024:
                sz = f"{size / 1024:.1f}KB"
            else:
                sz = f"{size}B"
            lines.append(f"  {f.name}  ({sz})")
        return "\n".join(lines)

    if name == "get_slow_calls":
        parsed = _parse_log(args["file"])
        min_dur = args.get("min_duration_ms", 100)
        limit = args.get("limit", 30)
        slow = [c for c in parsed.timer_calls if c.duration_ms >= min_dur]
        if not slow:
            return (
                f"No calls >= {min_dur}ms in {args['file']}. "
                f"Total timer calls: {len(parsed.timer_calls)}"
            )
        return _format_timer_table(slow, limit=limit)

    if name == "get_call_trace":
        parsed = _parse_log(args["file"])
        corr_id = args["correlation_id"]
        level = args.get("level", "TIMER").upper()
        entries = [
            e for e in parsed.entries if e.correlation_id == corr_id and (level in ("ALL", e.level))
        ]
        if not entries:
            return f"No entries for correlation ID {corr_id}"
        lines = [f"Call trace for {corr_id} ({len(entries)} entries, level={level}):\n"]
        for e in entries:
            msg = e.message[:120]
            lines.append(f"  {e.timestamp}  {e.level:7s}  {e.class_name:30s}  {msg}")
        timers = [c for c in parsed.timer_calls if c.correlation_id == corr_id]
        if timers:
            lines.append(f"\nTimer breakdown ({len(timers)} calls):")
            lines.append(_format_timer_table(timers, limit=50))
        return "\n".join(lines)

    if name == "get_sql_calls":
        parsed = _parse_log(args["file"])
        corr_id = args.get("correlation_id")
        limit = args.get("limit", 50)
        sqls = parsed.sql_calls
        if corr_id:
            sqls = [s for s in sqls if s.correlation_id == corr_id]
        if not sqls:
            return "No SQL statements found."
        lines = [f"SQL calls ({len(sqls)} total):\n"]
        for s in sqls[:limit]:
            msg = s.message[:200]
            lines.append(f"  {s.timestamp}  {s.class_name:30s}  {msg}")
        return "\n".join(lines)

    if name == "search_logs":
        parsed = _parse_log(args["file"])
        keyword = args.get("keyword", "")
        level = args.get("level", "")
        cls = args.get("class_name", "")
        corr_id = args.get("correlation_id", "")
        limit = args.get("limit", 50)
        matches = []
        for e in parsed.entries:
            if level and e.level != level.upper():
                continue
            if cls and cls.lower() not in e.class_name.lower():
                continue
            if corr_id and e.correlation_id != corr_id:
                continue
            if keyword and keyword.lower() not in e.message.lower():
                continue
            matches.append(e)
        if not matches:
            return "No matching entries."
        lines = [f"Found {len(matches)} entries:\n"]
        for e in matches[:limit]:
            msg = e.message[:150]
            lines.append(
                f"  L{e.line_no:>6d}  {e.timestamp}  {e.level:7s}  {e.class_name:25s}  {msg}"
            )
        return "\n".join(lines)

    if name == "get_log_summary":
        parsed = _parse_log(args["file"])
        if not parsed.entries:
            return f"No entries parsed from {args['file']}"
        levels: dict[str, int] = defaultdict(int)
        for e in parsed.entries:
            levels[e.level] += 1
        ts_first = parsed.entries[0].timestamp
        ts_last = parsed.entries[-1].timestamp
        lines = [
            f"Log summary: {args['file']}\n",
            f"  Time range: {ts_first} → {ts_last}",
            f"  Total entries: {len(parsed.entries)}",
            f"  Levels: {dict(levels)}",
            f"  Unique correlation IDs: {len(parsed.correlation_ids)}",
            f"  Timer calls: {len(parsed.timer_calls)}",
            f"  SQL calls: {len(parsed.sql_calls)}",
        ]
        if parsed.timer_calls:
            top = sorted(parsed.timer_calls, key=lambda c: -c.duration_ms)[:5]
            lines.append("\n  Top 5 slowest calls:")
            for tc in top:
                lines.append(f"    {tc.duration_ms:>6d}ms  {tc.method}::{tc.class_name}")
        return "\n".join(lines)

    if name == "get_api_stats":
        parsed = _parse_log(args["file"])
        corr_id = args.get("correlation_id")
        calls = parsed.timer_calls
        if corr_id:
            calls = [c for c in calls if c.correlation_id == corr_id]
        if not calls:
            return "No timer calls found."
        stats: dict[str, list[int]] = defaultdict(list)
        for c in calls:
            key = f"{c.method}::{c.class_name}"
            stats[key].append(c.duration_ms)
        lines = [
            f"{'Count':>6s}  {'Min':>8s}  {'Max':>8s}  {'Avg':>8s}  {'Total':>8s}  {'Method':<50s}",
            "-" * 100,
        ]
        for key in sorted(stats, key=lambda k: -sum(stats[k])):
            durations = stats[key]
            cnt = len(durations)
            mn = min(durations)
            mx = max(durations)
            avg = sum(durations) // cnt
            total = sum(durations)
            lines.append(
                f"{cnt:>6d}  {mn:>6d}ms  {mx:>6d}ms  {avg:>6d}ms  {total:>6d}ms  {key:<50s}"
            )
        return "\n".join(lines)

    return f"Unknown tool: {name}"


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
