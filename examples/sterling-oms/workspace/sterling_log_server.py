# /// script
# dependencies = ["mcp[cli]>=1.0"]
# ///
"""Sterling OMS log analyser MCP server.

Parses Sterling log4j FLAT format logs into a SQLite index for fast
querying. Handles 500MB+ logs by streaming the file and storing only
metadata in the index — full message text is read on demand via seek.

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
import sqlite3
from collections import defaultdict
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

server = Server("sterling-log-analyser")

LOG_DIR = os.environ.get("STERLING_LOG_DIR", ".")
INDEX_DIR = os.environ.get(
    "STERLING_LOG_INDEX_DIR",
    os.path.join(LOG_DIR, ".swarmkit", "log-index"),
)

LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r":(\w+)\s*"
    r":([^:]+)"
    r":\s*(.*)"
)
TIMER_BEGIN = re.compile(r"^(.+?)::(.+?)\s*-\s*Begin")
TIMER_END = re.compile(r"^(.+?)::(.+?)\s*-\s*End\s*-\s*\[(\d+)\]")
METADATA_PATTERN = re.compile(r"\[([^\]]*)\]:\s*\[([^\]]*)\]:\s*\[([^\]]*)\]:\s*(\S+)\s*$")
SQL_PATTERN = re.compile(r"(SELECT|INSERT|UPDATE|DELETE|MERGE)\s+", re.IGNORECASE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_start INTEGER,
    line_end INTEGER,
    byte_offset INTEGER,
    byte_end INTEGER,
    timestamp TEXT,
    level TEXT,
    thread TEXT,
    correlation_id TEXT,
    enterprise TEXT,
    class_name TEXT,
    api_name TEXT,
    message_preview TEXT,
    is_timer_begin INTEGER DEFAULT 0,
    is_timer_end INTEGER DEFAULT 0,
    duration_ms INTEGER,
    has_sql INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_corr ON log_entries(correlation_id);
CREATE INDEX IF NOT EXISTS idx_level ON log_entries(level);
CREATE INDEX IF NOT EXISTS idx_ts ON log_entries(timestamp);
CREATE INDEX IF NOT EXISTS idx_api ON log_entries(api_name);
CREATE INDEX IF NOT EXISTS idx_dur ON log_entries(duration_ms);
CREATE INDEX IF NOT EXISTS idx_thread ON log_entries(thread);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_MAX_RESPONSE_CHARS = 50_000


def _resolve_path(file_path: str) -> Path | None:
    p = Path(LOG_DIR) / file_path
    if p.exists():
        return p
    p = Path(file_path)
    if p.exists():
        return p
    return None


def _db_path(file_path: str) -> Path:
    idx = Path(INDEX_DIR)
    idx.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(file_path).name)
    return idx / f"{safe}.db"


def _needs_reindex(db: Path, log: Path) -> bool:
    if not db.exists():
        return True
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT value FROM meta WHERE key='mtime'").fetchone()
        conn.close()
        if row and float(row[0]) >= log.stat().st_mtime:
            return False
    except Exception:
        pass
    return True


def _index_log(file_path: str) -> sqlite3.Connection:  # noqa: PLR0912, PLR0915
    """Stream-parse a log file into SQLite index."""
    log_path = _resolve_path(file_path)
    if not log_path:
        conn = sqlite3.connect(":memory:")
        conn.executescript(_SCHEMA)
        return conn

    db = _db_path(file_path)
    if not _needs_reindex(db, log_path):
        return sqlite3.connect(str(db))

    if db.exists():
        db.unlink()
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")

    batch: list[tuple] = []  # type: ignore[type-arg]
    line_no = 0
    byte_pos = 0
    current_entry: dict | None = None  # type: ignore[type-arg]
    timer_stack: dict[str, list[str]] = defaultdict(list)

    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line_no += 1
            line_bytes = len(raw_line.encode("utf-8", errors="replace"))

            m = LOG_PATTERN.match(raw_line)
            if m:
                if current_entry:
                    current_entry["line_end"] = line_no - 1
                    current_entry["byte_end"] = byte_pos
                    batch.append(_entry_tuple(current_entry))
                    if len(batch) >= 10_000:
                        _insert_batch(conn, batch)
                        batch = []

                timestamp = m.group(1)
                level = m.group(2).strip()
                thread = m.group(3).strip()
                rest = m.group(4)

                corr_id = ""
                enterprise = ""
                cls = ""
                api = ""
                meta = METADATA_PATTERN.search(rest)
                if meta:
                    corr_id = meta.group(2)
                    enterprise = meta.group(3)
                    cls = meta.group(4)
                    msg = rest[: meta.start()].strip()
                else:
                    msg = rest.strip()

                is_begin = 0
                is_end = 0
                duration = None
                has_sql = 1 if SQL_PATTERN.search(msg) else 0

                if level == "TIMER":
                    bm = TIMER_BEGIN.match(msg)
                    em = TIMER_END.match(msg)
                    if bm:
                        is_begin = 1
                        api = bm.group(1)
                        cls = bm.group(2) or cls
                        key = f"{thread}:{api}::{cls}"
                        timer_stack[key].append(timestamp)
                    elif em:
                        is_end = 1
                        api = em.group(1)
                        cls = em.group(2) or cls
                        duration = int(em.group(3))
                        key = f"{thread}:{api}::{cls}"
                        if timer_stack.get(key):
                            timer_stack[key].pop()
                else:
                    bm2 = TIMER_BEGIN.match(msg)
                    if bm2:
                        api = bm2.group(1)
                    elif "::" in msg[:80]:
                        parts = msg.split("::", 1)
                        api = parts[0].strip()[:60]

                preview = msg[:150]

                current_entry = {
                    "line_start": line_no,
                    "line_end": line_no,
                    "byte_offset": byte_pos,
                    "byte_end": byte_pos + line_bytes,
                    "timestamp": timestamp,
                    "level": level,
                    "thread": thread,
                    "correlation_id": corr_id,
                    "enterprise": enterprise,
                    "class_name": cls,
                    "api_name": api,
                    "message_preview": preview,
                    "is_timer_begin": is_begin,
                    "is_timer_end": is_end,
                    "duration_ms": duration,
                    "has_sql": has_sql,
                }
            byte_pos += line_bytes

    if current_entry:
        current_entry["line_end"] = line_no
        current_entry["byte_end"] = byte_pos
        batch.append(_entry_tuple(current_entry))
    if batch:
        _insert_batch(conn, batch)

    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('mtime', ?)",
        (str(log_path.stat().st_mtime),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('entries', ?)",
        (str(line_no),),
    )
    conn.commit()
    return conn


def _entry_tuple(e: dict) -> tuple:  # type: ignore[type-arg]
    return (
        e["line_start"],
        e["line_end"],
        e["byte_offset"],
        e["byte_end"],
        e["timestamp"],
        e["level"],
        e["thread"],
        e["correlation_id"],
        e["enterprise"],
        e["class_name"],
        e["api_name"],
        e["message_preview"],
        e["is_timer_begin"],
        e["is_timer_end"],
        e["duration_ms"],
        e["has_sql"],
    )


def _insert_batch(
    conn: sqlite3.Connection,
    batch: list[tuple],  # type: ignore[type-arg]
) -> None:
    conn.executemany(
        "INSERT INTO log_entries "
        "(line_start, line_end, byte_offset, byte_end, "
        "timestamp, level, thread, correlation_id, enterprise, "
        "class_name, api_name, message_preview, "
        "is_timer_begin, is_timer_end, duration_ms, has_sql) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        batch,
    )
    conn.commit()


def _read_lines(file_path: str, byte_offset: int, byte_end: int) -> str:
    """Read a section of the log file by byte offset."""
    log_path = _resolve_path(file_path)
    if not log_path:
        return "(file not found)"
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(byte_offset)
        return fh.read(byte_end - byte_offset)


def _get_conn(file_path: str) -> sqlite3.Connection:
    return _index_log(file_path)


def _truncate(text: str) -> str:
    if len(text) > _MAX_RESPONSE_CHARS:
        return text[:_MAX_RESPONSE_CHARS] + f"\n\n... truncated ({len(text)} total chars)"
    return text


# ---- tool definitions -------------------------------------------------------

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
        name="get_log_summary",
        description=(
            "Get a high-level summary of a log file: total entries, "
            "entries per level, unique correlation IDs, slowest "
            "calls, and time range. Triggers indexing on first call."
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
        name="get_slow_calls",
        description=(
            "Find the slowest API/function calls from TIMER logs. "
            "Returns calls sorted by duration (slowest first)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path",
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
            "Shows all entries for a single request."
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
                    "description": "Filter: TIMER, DEBUG, VERBOSE, ALL (default: TIMER)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries (default: 100)",
                },
            },
            "required": ["file", "correlation_id"],
        },
    ),
    Tool(
        name="get_sql_calls",
        description=("Extract SQL statements from logs. Shows queries with execution context."),
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
        description=("Search log entries by keyword, level, class, or correlation ID."),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path",
                },
                "keyword": {
                    "type": "string",
                    "description": "Search in message preview",
                },
                "level": {
                    "type": "string",
                    "description": "Filter by level",
                },
                "class_name": {
                    "type": "string",
                    "description": "Filter by class",
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
        name="get_api_stats",
        description=(
            "Timing statistics per API/method. Shows count, min, max, avg, total duration."
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
    Tool(
        name="get_timer_detail",
        description=(
            "Drill into a slow TIMER call to see WHY it was slow. "
            "Shows ALL entries between Begin and End for the same "
            "correlation ID and thread. Pass the full log file."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file (full detail, not timer-only)",
                },
                "method": {
                    "type": "string",
                    "description": "Method name (uses slowest match)",
                },
                "correlation_id": {
                    "type": "string",
                    "description": "Correlation ID (optional)",
                },
                "class_name": {
                    "type": "string",
                    "description": "Class filter (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries (default: 200)",
                },
            },
            "required": ["file", "method"],
        },
    ),
    Tool(
        name="read_log_entry",
        description=(
            "Read the full text of a specific log entry including "
            "multiline XML payloads. Pass the line number from "
            "other tool results."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Log file path",
                },
                "line_start": {
                    "type": "integer",
                    "description": "Start line number",
                },
            },
            "required": ["file", "line_start"],
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
    return [TextContent(type="text", text=_truncate(result))]


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

    if name == "get_log_summary":
        conn = _get_conn(args["file"])
        total = conn.execute("SELECT COUNT(*) FROM log_entries").fetchone()[0]
        if total == 0:
            return f"No entries in {args['file']}"
        levels = conn.execute(
            "SELECT level, COUNT(*) FROM log_entries GROUP BY level ORDER BY COUNT(*) DESC"
        ).fetchall()
        corr_count = conn.execute(
            "SELECT COUNT(DISTINCT correlation_id) FROM log_entries WHERE correlation_id != ''"
        ).fetchone()[0]
        ts_range = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM log_entries").fetchone()
        timer_count = conn.execute(
            "SELECT COUNT(*) FROM log_entries WHERE is_timer_end=1"
        ).fetchone()[0]
        sql_count = conn.execute("SELECT COUNT(*) FROM log_entries WHERE has_sql=1").fetchone()[0]
        top_slow = conn.execute(
            "SELECT duration_ms, api_name, class_name, correlation_id "
            "FROM log_entries WHERE is_timer_end=1 "
            "ORDER BY duration_ms DESC LIMIT 10"
        ).fetchall()

        lines = [
            f"Log summary: {args['file']}\n",
            f"  Time range: {ts_range[0]} → {ts_range[1]}",
            f"  Total entries: {total:,}",
            f"  Levels: {dict(levels)}",
            f"  Unique correlation IDs: {corr_count:,}",
            f"  Timer calls: {timer_count:,}",
            f"  SQL statements: {sql_count:,}",
        ]
        if top_slow:
            lines.append("\n  Top 10 slowest calls:")
            for dur, api, cls, cid in top_slow:
                lines.append(f"    {dur:>8d}ms  {api}::{cls}  [{cid[:16]}]")
        conn.close()
        return "\n".join(lines)

    if name == "get_slow_calls":
        conn = _get_conn(args["file"])
        min_dur = args.get("min_duration_ms", 100)
        limit = args.get("limit", 30)
        rows = conn.execute(
            "SELECT duration_ms, api_name, class_name, "
            "correlation_id, thread, timestamp "
            "FROM log_entries WHERE is_timer_end=1 "
            "AND duration_ms >= ? "
            "ORDER BY duration_ms DESC LIMIT ?",
            (min_dur, limit),
        ).fetchall()
        conn.close()
        if not rows:
            return f"No calls >= {min_dur}ms"
        lines = [
            f"{'Duration':>10s}  {'Method':<40s}  {'Class':<25s}  {'Correlation ID':<38s}",
            "-" * 120,
        ]
        for dur, api, cls, cid, _thr, _ts in rows:
            lines.append(f"{dur:>8d}ms  {api:<40s}  {cls:<25s}  {cid:<38s}")
        return "\n".join(lines)

    if name == "get_call_trace":
        conn = _get_conn(args["file"])
        cid = args["correlation_id"]
        level = args.get("level", "TIMER").upper()
        limit = args.get("limit", 100)
        where = "correlation_id = ?"
        params: list = [cid]  # type: ignore[type-arg]
        if level != "ALL":
            where += " AND level = ?"
            params.append(level)
        rows = conn.execute(
            f"SELECT line_start, timestamp, level, class_name, "
            f"api_name, message_preview, duration_ms, "
            f"is_timer_begin, is_timer_end "
            f"FROM log_entries WHERE {where} "
            f"ORDER BY line_start LIMIT ?",
            (*params, limit),
        ).fetchall()
        conn.close()
        if not rows:
            return f"No entries for {cid} (level={level})"
        lines = [f"Call trace: {cid} ({len(rows)} entries):\n"]
        for (
            ln,
            ts,
            lev,
            cls,
            _api,
            preview,
            dur,
            is_b,
            is_e,
        ) in rows:
            marker = ""
            if is_b:
                marker = " → BEGIN"
            elif is_e:
                marker = f" ← END [{dur}ms]"
            lines.append(f"  L{ln:>7d}  {ts}  {lev:7s}  {cls:25s}  {preview[:80]}{marker}")
        return "\n".join(lines)

    if name == "get_sql_calls":
        conn = _get_conn(args["file"])
        cid = args.get("correlation_id")
        limit = args.get("limit", 50)
        if cid:
            rows = conn.execute(
                "SELECT line_start, timestamp, class_name, "
                "message_preview FROM log_entries "
                "WHERE has_sql=1 AND correlation_id=? "
                "ORDER BY line_start LIMIT ?",
                (cid, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT line_start, timestamp, class_name, "
                "message_preview FROM log_entries "
                "WHERE has_sql=1 ORDER BY line_start LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        if not rows:
            return "No SQL statements found."
        lines = [f"SQL calls ({len(rows)}):\n"]
        for ln, ts, cls, preview in rows:
            lines.append(f"  L{ln:>7d}  {ts}  {cls:25s}  {preview[:120]}")
        return "\n".join(lines)

    if name == "search_logs":
        conn = _get_conn(args["file"])
        where_parts = ["1=1"]
        params = []  # type: ignore[type-arg]
        if args.get("keyword"):
            where_parts.append("message_preview LIKE ?")
            params.append(f"%{args['keyword']}%")
        if args.get("level"):
            where_parts.append("level = ?")
            params.append(args["level"].upper())
        if args.get("class_name"):
            where_parts.append("class_name LIKE ?")
            params.append(f"%{args['class_name']}%")
        if args.get("correlation_id"):
            where_parts.append("correlation_id = ?")
            params.append(args["correlation_id"])
        limit = args.get("limit", 50)
        where = " AND ".join(where_parts)
        rows = conn.execute(
            f"SELECT line_start, timestamp, level, class_name, "
            f"message_preview FROM log_entries "
            f"WHERE {where} ORDER BY line_start LIMIT ?",
            (*params, limit),
        ).fetchall()
        conn.close()
        if not rows:
            return "No matching entries."
        lines = [f"Found {len(rows)} entries:\n"]
        for ln, ts, lev, cls, preview in rows:
            lines.append(f"  L{ln:>7d}  {ts}  {lev:7s}  {cls:25s}  {preview[:100]}")
        return "\n".join(lines)

    if name == "get_api_stats":
        conn = _get_conn(args["file"])
        cid = args.get("correlation_id")
        if cid:
            rows = conn.execute(
                "SELECT api_name, class_name, "
                "COUNT(*) as cnt, "
                "MIN(duration_ms), MAX(duration_ms), "
                "AVG(duration_ms), SUM(duration_ms) "
                "FROM log_entries WHERE is_timer_end=1 "
                "AND correlation_id=? "
                "GROUP BY api_name, class_name "
                "ORDER BY SUM(duration_ms) DESC",
                (cid,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT api_name, class_name, "
                "COUNT(*) as cnt, "
                "MIN(duration_ms), MAX(duration_ms), "
                "AVG(duration_ms), SUM(duration_ms) "
                "FROM log_entries WHERE is_timer_end=1 "
                "GROUP BY api_name, class_name "
                "ORDER BY SUM(duration_ms) DESC",
            ).fetchall()
        conn.close()
        if not rows:
            return "No timer calls found."
        lines = [
            f"{'Count':>6s}  {'Min':>8s}  {'Max':>8s}  {'Avg':>8s}  {'Total':>8s}  Method",
            "-" * 90,
        ]
        for api, cls, cnt, mn, mx, avg, total in rows:
            lines.append(
                f"{cnt:>6d}  {mn:>6d}ms  {mx:>6d}ms  {int(avg):>6d}ms  {total:>6d}ms  {api}::{cls}"
            )
        return "\n".join(lines)

    if name == "get_timer_detail":
        conn = _get_conn(args["file"])
        method = args.get("method", "")
        cid = args.get("correlation_id", "")
        cls_filter = args.get("class_name", "")
        limit = args.get("limit", 200)

        where_parts = ["is_timer_end=1"]
        params = []  # type: ignore[type-arg]
        if method:
            where_parts.append("api_name LIKE ?")
            params.append(f"%{method}%")
        if cid:
            where_parts.append("correlation_id = ?")
            params.append(cid)
        if cls_filter:
            where_parts.append("class_name LIKE ?")
            params.append(f"%{cls_filter}%")
        where = " AND ".join(where_parts)

        target = conn.execute(
            f"SELECT id, duration_ms, api_name, class_name, "
            f"correlation_id, thread, timestamp "
            f"FROM log_entries WHERE {where} "
            f"ORDER BY duration_ms DESC LIMIT 1",
            params,
        ).fetchone()
        if not target:
            conn.close()
            return f"No timer call found for method='{method}'"

        tid, dur, t_api, t_cls, t_cid, t_thread, t_ts = target

        begin_row = conn.execute(
            "SELECT timestamp FROM log_entries "
            "WHERE is_timer_begin=1 AND api_name=? "
            "AND class_name=? AND correlation_id=? "
            "AND thread=? AND timestamp <= ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (t_api, t_cls, t_cid, t_thread, t_ts),
        ).fetchone()
        begin_ts = begin_row[0] if begin_row else ""

        between = conn.execute(
            "SELECT line_start, timestamp, level, class_name, "
            "api_name, message_preview, duration_ms, "
            "is_timer_begin, is_timer_end "
            "FROM log_entries "
            "WHERE correlation_id=? AND thread=? "
            "AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY line_start LIMIT ?",
            (t_cid, t_thread, begin_ts, t_ts, limit),
        ).fetchall()

        sub_timers = conn.execute(
            "SELECT duration_ms, api_name, class_name "
            "FROM log_entries "
            "WHERE is_timer_end=1 AND correlation_id=? "
            "AND thread=? AND timestamp >= ? "
            "AND timestamp <= ? AND id != ? "
            "ORDER BY duration_ms DESC LIMIT 20",
            (t_cid, t_thread, begin_ts, t_ts, tid),
        ).fetchall()
        conn.close()

        lines = [
            f"Timer detail: {t_api}::{t_cls}",
            f"  Duration: {dur}ms",
            f"  Correlation: {t_cid}",
            f"  Thread: {t_thread}",
            f"  Begin: {begin_ts}",
            f"  End:   {t_ts}",
            f"  Entries between: {len(between)}",
            "",
        ]
        for (
            ln,
            ts,
            lev,
            cls,
            _api,
            preview,
            d,
            is_b,
            is_e,
        ) in between:
            marker = ""
            if is_b:
                marker = " → BEGIN"
            elif is_e and d:
                marker = f" ← [{d}ms]"
            lines.append(f"  L{ln:>7d}  {ts}  {lev:7s}  {cls:25s}  {preview[:80]}{marker}")
        if sub_timers:
            lines.append(f"\nSub-calls ({len(sub_timers)}):")
            for d, api, cls in sub_timers:
                lines.append(f"  {d:>8d}ms  {api}::{cls}")
        return "\n".join(lines)

    if name == "read_log_entry":
        conn = _get_conn(args["file"])
        ln = args["line_start"]
        row = conn.execute(
            "SELECT byte_offset, byte_end FROM log_entries WHERE line_start = ?",
            (ln,),
        ).fetchone()
        conn.close()
        if not row:
            return f"No entry at line {ln}"
        return _read_lines(args["file"], row[0], row[1])

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
