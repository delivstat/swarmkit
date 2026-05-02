# /// script
# dependencies = ["mcp[cli]>=1.0"]
# ///
"""Sterling CDT Config MCP server — structured access to Sterling configuration.

Reads pre-ingested CDT index (from ingest-cdt.py) and serves structured
queries over services, pipelines, transactions, events, user exits,
hold types, common codes, and statuses.

Usage:
    export STERLING_CDT_INDEX=~/sterling-knowledge/cdt-index
    uv run sterling_cdt_server.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("sterling-config")

_INDEX = Path(os.environ.get("STERLING_CDT_INDEX", "cdt-index"))


def _load(name: str) -> dict | list:
    path = _INDEX / name
    if not path.exists():
        return {} if name.endswith(".json") else []
    return json.loads(path.read_text())


_services: dict = {}
_pipelines: dict = {}
_transactions: dict = {}
_statuses: dict = {}
_common_codes: dict = {}
_user_exits: list = []
_hold_types: list = []
_meta: dict = {}


def _init() -> None:
    global _services, _pipelines, _transactions  # noqa: PLW0603
    global _statuses, _common_codes, _user_exits  # noqa: PLW0603
    global _hold_types, _meta  # noqa: PLW0603
    _services = _load("services.json")
    _pipelines = _load("pipelines.json")
    _transactions = _load("transactions.json")
    _statuses = _load("statuses.json")
    _common_codes = _load("common_codes.json")
    _user_exits = _load("user_exits.json")
    _hold_types = _load("hold_types.json")
    _meta = _load("meta.json")


_init()


def _find_service(name: str) -> dict | None:
    name_lower = name.lower()
    for svc in _services.values():
        if svc.get("name", "").lower() == name_lower:
            return svc
    for svc in _services.values():
        if name_lower in svc.get("name", "").lower():
            return svc
    return None


def _format_service(svc: dict) -> str:
    lines = [
        f"# Service: {svc['name']}",
        f"**Flow Key:** {svc.get('flow_key', '')}",
        f"**Owner:** {svc.get('owner', '')}",
        f"**Process Type:** {svc.get('process_type', '')}",
        f"**Flow Type:** {svc.get('flow_type', '')}",
        f"**Real-time:** {svc.get('is_realtime', '')}",
        "",
    ]
    for sf in svc.get("sub_flows", []):
        lines.append(f"## SubFlow: {sf.get('sub_flow_name', '')}")
        lines.append(f"**Seq:** {sf.get('seq_no', '')} | **Server:** {sf.get('server_key', '')}")
        lines.append("")
        for node in sf.get("nodes", []):
            ntype = node.get("type", "")
            if ntype in ("Start", "End"):
                lines.append(f"- [{ntype}]")
            elif ntype == "API":
                api = node.get("api_name", "")
                cls = node.get("class_name", "")
                method = node.get("method_name", "")
                api_type = node.get("api_type", "")
                lines.append(f"- **API:** {api} ({api_type})")
                lines.append(f"  Class: `{cls}`")
                if method and method != api:
                    lines.append(f"  Method: `{method}`")
            elif ntype == "XSL":
                lines.append(f"- **XSL:** {node.get('xsl_name', '')}")
            elif ntype in ("JMS", "LOCAL"):
                lines.append(f"- **Transport:** {ntype}")
            else:
                lines.append(f"- [{ntype}] {node.get('text', '')}")

        for link in sf.get("links", []):
            if link.get("type") == "Async":
                q = link.get("queue", "")
                threads = link.get("threads", "")
                rt = link.get("runtime_id", "")
                lines.append(f"\n**Async Transport:** Queue={q}, Threads={threads}, RuntimeId={rt}")
                if link.get("schedule_trigger") == "Y":
                    lines.append(f"  Schedule: every {link.get('schedule_interval', '')}s")
        lines.append("")
    return "\n".join(lines)


def _render_mermaid_service(svc: dict) -> str:  # noqa: PLR0912
    lines = ["```mermaid", "graph LR"]
    for sf in svc.get("sub_flows", []):
        node_labels: dict[str, str] = {}
        for node in sf.get("nodes", []):
            nid = node.get("id", "")
            ntype = node.get("type", "")
            if ntype == "Start":
                node_labels[nid] = "Start([Start])"
            elif ntype == "End":
                node_labels[nid] = "End([End])"
            elif ntype == "API":
                node_labels[nid] = f'{nid}["{node.get("api_name", "")}"]'
            elif ntype == "XSL":
                xsl = node.get("xsl_name", "").split("/")[-1]
                node_labels[nid] = f'{nid}[/"XSL: {xsl}"/]'
            elif ntype in ("JMS", "LOCAL"):
                node_labels[nid] = f"{nid}>{ntype}]"
            else:
                node_labels[nid] = f'{nid}["{ntype}"]'

        for _nid, label in node_labels.items():
            lines.append(f"    {label}")

        for link in sf.get("links", []):
            frm = link.get("from", "")
            to = link.get("to", "")
            if frm in node_labels and to in node_labels:
                if link.get("type") == "Async":
                    lines.append(f"    {frm} -.->|async| {to}")
                else:
                    lines.append(f"    {frm} --> {to}")
    lines.append("```")
    return "\n".join(lines)


def _render_mermaid_pipeline(pipe: dict) -> str:
    lines = ["```mermaid", "graph TD"]
    status_set = set()
    for defn in pipe.get("definitions", []):
        status_set.add(defn.get("drop_status", ""))
    for pt in pipe.get("pickup_transactions", []):
        status_set.add(pt.get("status", ""))

    status_names = {}
    pt_key = pipe.get("process_type", "")
    for s in _statuses.get(pt_key, []):
        status_names[s["status"]] = s.get("status_name", s["status"])

    sorted_statuses = sorted(status_set - {""})
    for status in sorted_statuses:
        name = status_names.get(status, status)
        safe_id = status.replace(".", "_")
        lines.append(f'    {safe_id}["{status} {name}"]')

    for i in range(len(sorted_statuses) - 1):
        s1 = sorted_statuses[i].replace(".", "_")
        s2 = sorted_statuses[i + 1].replace(".", "_")
        lines.append(f"    {s1} --> {s2}")

    lines.append("```")
    return "\n".join(lines)


@server.tool()
def get_service_config(name: str) -> str:
    """Get a Sterling service/flow configuration — API calls, classes, XSL, transport."""
    svc = _find_service(name)
    if not svc:
        matches = [
            s["name"] for s in _services.values() if name.lower() in s.get("name", "").lower()
        ]
        if matches:
            return f"Service '{name}' not found. Similar: {', '.join(matches[:10])}"
        return f"Service '{name}' not found."
    return _format_service(svc)


@server.tool()
def render_service_graph(name: str) -> str:
    """Render a Sterling service flow as a Mermaid diagram."""
    svc = _find_service(name)
    if not svc:
        return f"Service '{name}' not found."
    return f"# {svc['name']} — Service Flow\n\n{_render_mermaid_service(svc)}"


@server.tool()
def list_services(filter: str = "") -> str:
    """List Sterling services. Optional filter by name or process type."""
    matches = []
    fl = filter.lower()
    for svc in _services.values():
        if not fl or fl in svc.get("name", "").lower() or fl in svc.get("process_type", "").lower():
            matches.append(svc)
    if not matches:
        return f"No services matching '{filter}'."
    lines = [f"Found {len(matches)} services:\n"]
    for s in sorted(matches, key=lambda x: x.get("name", ""))[:50]:
        lines.append(
            f"- **{s['name']}** (owner={s.get('owner', '')}, process={s.get('process_type', '')})"
        )
    if len(matches) > 50:
        lines.append(f"\n... and {len(matches) - 50} more")
    return "\n".join(lines)


@server.tool()
def get_pipeline(pipeline_id: str) -> str:
    """Get a Sterling pipeline — steps, statuses, conditions, transactions."""
    pipe = None
    pid_lower = pipeline_id.lower()
    for p in _pipelines.values():
        if p.get("pipeline_id", "").lower() == pid_lower:
            pipe = p
            break
    if not pipe:
        for p in _pipelines.values():
            if (
                pid_lower in p.get("pipeline_id", "").lower()
                or pid_lower in p.get("description", "").lower()
            ):
                pipe = p
                break
    if not pipe:
        return f"Pipeline '{pipeline_id}' not found."

    pt_key = pipe.get("process_type", "")
    status_names = {s["status"]: s.get("status_name", "") for s in _statuses.get(pt_key, [])}

    lines = [
        f"# Pipeline: {pipe['pipeline_id']}",
        f"**Description:** {pipe.get('description', '')}",
        f"**Owner:** {pipe.get('owner', '')}",
        f"**Process Type:** {pipe.get('process_type', '')}",
        "",
        "## Status Steps",
        "",
    ]
    for defn in sorted(pipe.get("definitions", []), key=lambda x: x.get("drop_status", "")):
        status = defn.get("drop_status", "")
        name = status_names.get(status, "")
        tran = defn.get("transaction_key", "")
        lines.append(f"- **{status}** {name} (transaction: {tran})")

    if pipe.get("conditions"):
        lines += ["", "## Conditions", ""]
        for cond in pipe["conditions"]:
            lines.append(
                f"- {cond.get('condition_value', '')} (key: {cond.get('condition_key', '')})"
            )

    if pipe.get("pickup_transactions"):
        lines += ["", "## Pickup Transactions", ""]
        for pt in sorted(pipe["pickup_transactions"], key=lambda x: x.get("status", "")):
            lines.append(f"- Status {pt['status']} → {pt['transaction_key']}")

    return "\n".join(lines)


@server.tool()
def render_pipeline_graph(pipeline_id: str) -> str:
    """Render a Sterling pipeline status flow as a Mermaid diagram."""
    pipe = None
    for p in _pipelines.values():
        if pipeline_id.lower() in p.get("pipeline_id", "").lower():
            pipe = p
            break
    if not pipe:
        return f"Pipeline '{pipeline_id}' not found."
    return f"# {pipe['pipeline_id']} — Pipeline Flow\n\n{_render_mermaid_pipeline(pipe)}"


@server.tool()
def list_pipelines(filter: str = "") -> str:
    """List Sterling pipelines. Optional filter by name or process type."""
    matches = []
    fl = filter.lower()
    for p in _pipelines.values():
        if (
            not fl
            or fl in p.get("pipeline_id", "").lower()
            or fl in p.get("description", "").lower()
        ):
            matches.append(p)
    if not matches:
        return f"No pipelines matching '{filter}'."
    lines = [f"Found {len(matches)} pipelines:\n"]
    for p in sorted(matches, key=lambda x: x.get("pipeline_id", "")):
        lines.append(
            f"- **{p['pipeline_id']}**: {p.get('description', '')}"
        )
    return "\n".join(lines)


@server.tool()
def get_transactions(filter: str = "") -> str:
    """Get Sterling transactions with their events."""
    fl = filter.lower()
    matches = []
    for t in _transactions.values():
        tid = t.get("transaction_id", "")
        if not fl or fl in tid.lower() or fl in t.get("base_transaction", "").lower():
            matches.append(t)
    if not matches:
        return f"No transactions matching '{filter}'."
    lines = [f"Found {len(matches)} transactions:\n"]
    for t in sorted(matches, key=lambda x: x.get("transaction_id", ""))[:30]:
        events = [e["event_id"] for e in t.get("events", [])]
        event_str = f" Events: {', '.join(events)}" if events else ""
        lines.append(
            f"- **{t['transaction_id']}** (base={t.get('base_transaction', '')}){event_str}"
        )
    if len(matches) > 30:
        lines.append(f"\n... and {len(matches) - 30} more")
    return "\n".join(lines)


@server.tool()
def get_events(transaction_id: str) -> str:
    """Get events configured for a Sterling transaction."""
    fl = transaction_id.lower()
    for t in _transactions.values():
        if t.get("transaction_id", "").lower() == fl or t.get("transaction_key", "").lower() == fl:
            events = t.get("events", [])
            if not events:
                return f"Transaction '{transaction_id}' has no events configured."
            lines = [f"# Events for {t['transaction_id']}\n"]
            for e in events:
                active = "ACTIVE" if e.get("active") == "Y" else "inactive"
                lines.append(f"- **{e['event_id']}**: {e.get('event_name', '')} ({active})")
            return "\n".join(lines)
    return f"Transaction '{transaction_id}' not found."


@server.tool()
def get_user_exits() -> str:
    """Get all configured user exit implementations."""
    if not _user_exits:
        return "No user exit implementations configured in CDT."
    lines = ["# User Exit Implementations\n"]
    for ue in _user_exits:
        cls = ue.get("java_class", "")
        key = ue.get("user_exit_key", "")
        if cls:
            lines.append(
                f"- **{key}**: `{cls}` (org={ue.get('org', '')}, doc={ue.get('doc_type', '')})"
            )
        elif ue.get("use_flow") == "Y":
            lines.append(
                f"- **{key}**: Uses flow {ue.get('flow_key', '')} (org={ue.get('org', '')})"
            )
    return "\n".join(lines)


@server.tool()
def get_hold_types(filter: str = "") -> str:
    """Get Sterling hold type configurations."""
    fl = filter.lower()
    matches = [
        h
        for h in _hold_types
        if not fl or fl in h.get("hold_type", "").lower() or fl in h.get("description", "").lower()
    ]
    if not matches:
        return f"No hold types matching '{filter}'."
    lines = [f"Found {len(matches)} hold types:\n"]
    for h in matches:
        lines.append(f"- **{h['hold_type']}**: {h.get('description', '')}")
        lines.append(
            f"  Level={h.get('hold_level', '')}, Doc={h.get('doc_type', '')}"
        )
        if h.get("process_transaction"):
            lines.append(
                f"  Process: {h['process_transaction']}, Reject: {h.get('reject_transaction', '')}"
            )
    return "\n".join(lines)


@server.tool()
def get_common_codes(code_type: str) -> str:
    """Get Sterling common codes by type (e.g. hold types, reason codes)."""
    codes = _common_codes.get(code_type, [])
    if not codes:
        types = sorted(_common_codes.keys())
        close = [t for t in types if code_type.lower() in t.lower()]
        if close:
            return f"Code type '{code_type}' not found. Similar: {', '.join(close[:10])}"
        return f"Code type '{code_type}' not found. Use list_config_tables to see available types."
    lines = [f"# Common Codes: {code_type} ({len(codes)} codes)\n"]
    for c in codes[:50]:
        lines.append(
            f"- **{c['code_value']}**: {c.get('code_short_description', '')}"
        )
    if len(codes) > 50:
        lines.append(f"\n... and {len(codes) - 50} more")
    return "\n".join(lines)


@server.tool()
def search_configs(pattern: str) -> str:
    """Search across all Sterling configurations for a text pattern."""
    pattern_lower = pattern.lower()
    results = []

    for svc in _services.values():
        if pattern_lower in json.dumps(svc).lower():
            results.append(f"[Service] {svc.get('name', '')}")

    for pipe in _pipelines.values():
        if (
            pattern_lower in pipe.get("pipeline_id", "").lower()
            or pattern_lower in pipe.get("description", "").lower()
        ):
            results.append(
                f"[Pipeline] {pipe.get('pipeline_id', '')}: {pipe.get('description', '')}"
            )

    for tran in _transactions.values():
        if pattern_lower in tran.get("transaction_id", "").lower():
            results.append(f"[Transaction] {tran.get('transaction_id', '')}")

    for ht in _hold_types:
        if (
            pattern_lower in ht.get("hold_type", "").lower()
            or pattern_lower in ht.get("description", "").lower()
        ):
            results.append(f"[HoldType] {ht['hold_type']}: {ht.get('description', '')}")

    if not results:
        return f"No config matches for '{pattern}'."

    return f"Found {len(results)} matches:\n\n" + "\n".join(results[:30])


@server.tool()
def get_config_table(table_name: str) -> str:
    """Get raw rows from any CDT config table."""
    table_path = _INDEX / "tables" / f"{table_name}.json"
    if not table_path.exists():
        close = [
            f.stem
            for f in (_INDEX / "tables").glob("*.json")
            if table_name.lower() in f.stem.lower()
        ]
        if close:
            return f"Table '{table_name}' not found. Similar: {', '.join(close[:10])}"
        return f"Table '{table_name}' not found."
    rows = json.loads(table_path.read_text())
    if not rows:
        return f"Table '{table_name}' is empty."
    lines = [f"# {table_name} ({len(rows)} rows)\n"]
    cols = list(rows[0].keys())[:10]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for row in rows[:20]:
        vals = [str(row.get(c, ""))[:30] for c in cols]
        lines.append("| " + " | ".join(vals) + " |")
    if len(rows) > 20:
        lines.append(f"\n... and {len(rows) - 20} more rows")
    return "\n".join(lines)


@server.tool()
def list_config_tables() -> str:
    """List all available CDT config tables with record counts."""
    if not _meta:
        return "No CDT tables ingested."
    lines = [f"# CDT Config Tables ({len(_meta)} tables)\n"]
    for table in sorted(_meta.keys()):
        count = _meta[table].get("records", 0)
        if count > 0:
            lines.append(f"- **{table}**: {count} records")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print(f"Services: {len(_services)}")
        print(f"Pipelines: {len(_pipelines)}")
        print(f"Transactions: {len(_transactions)}")
        print(f"Statuses: {sum(len(v) for v in _statuses.values())}")
        print(f"Common codes: {sum(len(v) for v in _common_codes.values())}")
        print(f"User exits: {len(_user_exits)}")
        print(f"Hold types: {len(_hold_types)}")
        print(f"Tables: {len(_meta)}")
    else:
        server.run()
