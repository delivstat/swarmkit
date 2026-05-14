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
_monitor_rules: list = []
_meta: dict = {}


def _init() -> None:
    global _services, _pipelines, _transactions  # noqa: PLW0603
    global _statuses, _common_codes, _user_exits  # noqa: PLW0603
    global _hold_types, _monitor_rules, _meta  # noqa: PLW0603
    _services = _load("services.json")
    _pipelines = _load("pipelines.json")
    _transactions = _load("transactions.json")
    _statuses = _load("statuses.json")
    _common_codes = _load("common_codes.json")
    _user_exits = _load("user_exits.json")
    _hold_types = _load("hold_types.json")
    _monitor_rules = _load("monitor_rules.json")
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
        sf_name = sf.get("sub_flow_name", "")
        if sf_name:
            lines.append(f"    subgraph {sf_name}")

        node_labels: dict[str, str] = {}
        for node in sf.get("nodes", []):
            nid = node.get("id", "")
            ntype = node.get("type", "")
            if ntype == "Start":
                node_labels[nid] = f"    {nid}([Start])"
            elif ntype == "End":
                node_labels[nid] = f"    {nid}([End])"
            elif ntype == "API":
                api = node.get("api_name", "")
                cls = node.get("class_name", "")
                label = api or cls.split(".")[-1] if cls else ntype
                node_labels[nid] = f'    {nid}["{label}"]'
            elif ntype == "XSL":
                xsl = node.get("xsl_name", "").split("/")[-1]
                node_labels[nid] = f'    {nid}[/"XSL: {xsl}"/]'
            elif ntype in ("JMS", "LOCAL"):
                node_labels[nid] = f"    {nid}>{ntype}]"
            else:
                text = node.get("text", ntype)
                node_labels[nid] = f'    {nid}["{text}"]'

        for _nid, label in node_labels.items():
            lines.append(label)

        for link in sf.get("links", []):
            frm = link.get("from", "")
            to = link.get("to", "")
            if frm not in node_labels or to not in node_labels:
                continue
            cond = link.get("condition", "")
            transport = link.get("type", "")
            queue = link.get("queue", "")
            if transport == "Async":
                edge_label = queue or "async"
                lines.append(f"    {frm} -.->|{edge_label}| {to}")
            elif cond:
                lines.append(f"    {frm} -->|{cond}| {to}")
            else:
                lines.append(f"    {frm} --> {to}")

        if sf_name:
            lines.append("    end")
    lines.append("```")
    return "\n".join(lines)


def _render_mermaid_pipeline(pipe: dict) -> str:
    lines = ["```mermaid", "graph TD"]

    pt_key = pipe.get("process_type", "")
    owner_key = pipe.get("owner", "")
    status_names = _resolve_status_names(pt_key, owner_key)

    defns = sorted(
        pipe.get("definitions", []),
        key=lambda x: x.get("drop_status", ""),
    )
    for defn in defns:
        status = defn.get("drop_status", "")
        if not status:
            continue
        name = status_names.get(status, status)
        tran = defn.get("transaction_key", "")
        safe_id = status.replace(".", "_")
        label = f"{status} {name}"
        if tran:
            label += f"\\n({tran})"
        lines.append(f'    {safe_id}["{label}"]')

    for i in range(len(defns) - 1):
        s1 = defns[i].get("drop_status", "").replace(".", "_")
        s2 = defns[i + 1].get("drop_status", "").replace(".", "_")
        if s1 and s2:
            lines.append(f"    {s1} --> {s2}")

    hub_rules = pipe.get("hub_rules", [])
    if hub_rules:
        lines.append('    HUB{{"Hub Rule\\nDetermination"}}')
        first_status = defns[0].get("drop_status", "") if defns else ""
        if first_status:
            lines.append(f"    HUB --> {first_status.replace('.', '_')}")
        for i, rule in enumerate(hub_rules):
            ent = rule.get("enterprise_name", "")
            cn = rule.get("condition_name", "default")
            pri = rule.get("priority", "")
            safe = f"HR{i}"
            lines.append(f'    {safe}[/"{ent} P{pri}\\n{cn}"/] -.-> HUB')

    for pt in pipe.get("pickup_transactions", []):
        status = pt.get("status", "")
        tran = pt.get("transaction_key", "")
        if status:
            safe_id = status.replace(".", "_")
            name = status_names.get(status, status)
            lines.append(f'    {safe_id}_pickup["{status} {name}\\n(pickup: {tran})"]')
            lines.append(f"    {safe_id} -.-> {safe_id}_pickup")

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


def _resolve_status_names(pt_key: str, owner_key: str = "") -> dict[str, str]:
    """Build status code → name mapping, preferring owner-specific names.

    Sterling allows enterprises to customize status descriptions. This
    checks for owner-specific names first, then falls back to the
    default (blank owner) entries.
    """
    defaults: dict[str, str] = {}
    owner_specific: dict[str, str] = {}
    for s in _statuses.get(pt_key, []):
        code = s.get("status", "")
        name = s.get("status_name", "")
        desc = s.get("description", "")
        label = name or desc or code
        s_owner = s.get("owner", "")
        if s_owner == owner_key and owner_key:
            owner_specific[code] = label
        elif not s_owner or s_owner == "DEFAULT":
            defaults[code] = label
    merged = {**defaults, **owner_specific}
    return merged


@server.tool()
def get_pipeline(pipeline_id: str) -> str:  # noqa: PLR0912, PLR0915
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
    owner_key = pipe.get("owner", "")
    status_names = _resolve_status_names(pt_key, owner_key)

    lines = [
        f"# Pipeline: {pipe['pipeline_id']}",
        f"**Description:** {pipe.get('description', '')}",
        f"**Owner:** {pipe.get('owner_org', pipe.get('owner', ''))}",
        f"**Process Type:** {pipe.get('process_type_name', pipe.get('process_type', ''))}",
        "",
        "## Status Steps",
        "",
    ]
    for defn in sorted(pipe.get("definitions", []), key=lambda x: x.get("drop_status", "")):
        status = defn.get("drop_status", "")
        name = status_names.get(status, "")
        tran = defn.get("transaction_key", "")
        lines.append(f"- **{status}** {name} (transaction: {tran})")

    if pipe.get("hub_rules"):
        lines += ["", "## Hub Rules (Pipeline Selection Conditions)", ""]
        lines.append("Evaluated in priority order (lower = first):\n")
        for rule in pipe["hub_rules"]:
            ent = rule.get("enterprise_name", rule.get("enterprise", ""))
            cond_name = rule.get("condition_name", "")
            cond_rule = rule.get("condition_rule", "")
            pri = rule.get("priority", "")
            rule_desc = f": `{cond_rule}`" if cond_rule else ""
            lines.append(f"- [P{pri}] Enterprise=**{ent}** — {cond_name}{rule_desc}")

    if pipe.get("conditions"):
        lines += ["", "## Conditions (Detail)", ""]
        for cond in pipe["conditions"]:
            ent = cond.get("enterprise_name", cond.get("enterprise", ""))
            cn = cond.get("condition_name", "")
            cv = cond.get("condition_value", "")
            pri = cond.get("priority", "")
            cid = cond.get("condition_id", "")
            rule_str = f" — `{cv}`" if cv else ""
            lines.append(f"- [P{pri}] {ent}: **{cn}** ({cid}){rule_str}")

    if pipe.get("pickup_transactions"):
        lines += ["", "## Pickup Transactions", ""]
        for pt in sorted(pipe["pickup_transactions"], key=lambda x: x.get("status", "")):
            lines.append(f"- Status {pt['status']} → {pt['transaction_key']}")

    if pipe.get("monitor_rules"):
        lines += ["", "## Monitoring Rules", ""]
        for mr in pipe["monitor_rules"]:
            name = mr.get("rule_name", "")
            status = mr.get("status", "")
            sname = status_names.get(status, "")
            alert = mr.get("time_to_alert", "")
            esc = mr.get("time_to_escalate", "")
            active = mr.get("active", "")
            cond = mr.get("condition", "")
            parts = [f"- **{name}** at status {status} {sname}"]
            if alert:
                parts.append(f"alert={alert}m")
            if esc:
                parts.append(f"escalate={esc}m")
            if cond:
                parts.append(f"condition={cond}")
            if active:
                parts.append(f"active={active}")
            lines.append(" | ".join(parts))

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
    """List Sterling pipelines. Filter by name, process type, or organization.

    Args:
        filter: Search term — matches pipeline name, description, process type,
                owner org, or hub rule condition values (organization names).
    """
    matches = []
    fl = filter.lower()
    for p in _pipelines.values():
        if not fl:
            matches.append(p)
            continue
        searchable = " ".join(
            [
                p.get("pipeline_id", ""),
                p.get("description", ""),
                p.get("process_type_name", ""),
                p.get("owner_org", ""),
            ]
        ).lower()
        hub_text = " ".join(
            f"{r.get('enterprise_name', '')} {r.get('condition_name', '')}"
            for r in p.get("hub_rules", [])
        ).lower()
        if fl in searchable or fl in hub_text:
            matches.append(p)
    if not matches:
        return f"No pipelines matching '{filter}'."
    lines = [f"Found {len(matches)} pipelines:\n"]
    for p in sorted(matches, key=lambda x: x.get("pipeline_id", "")):
        ents = sorted(
            {
                r.get("enterprise_name", "")
                for r in p.get("hub_rules", [])
                if r.get("enterprise_name")
            }
        )
        org_str = f" (enterprises: {', '.join(ents)})" if ents else ""
        pt = p.get("process_type_name", p.get("process_type", ""))
        lines.append(f"- **{p['pipeline_id']}**: {p.get('description', '')} [{pt}]{org_str}")
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
    """Get events, actions, and invoked flows for a Sterling transaction.

    Shows the full chain: Transaction → Events → Actions → Invoked Flows.
    """
    fl = transaction_id.lower()
    for t in _transactions.values():
        if t.get("transaction_id", "").lower() == fl or t.get("transaction_key", "").lower() == fl:
            events = t.get("events", [])
            if not events:
                return f"Transaction '{transaction_id}' has no events configured."
            tname = t.get("transaction_name") or t["transaction_id"]
            lines = [f"# Events for {tname} ({t['transaction_id']})\n"]
            for e in events:
                active = "ACTIVE" if e.get("active") == "Y" else "inactive"
                eid = e.get("event_id", "")
                ename = e.get("event_name", "")
                lines.append(f"## Event: {eid} - {ename} ({active})")
                actions = e.get("actions", [])
                if actions:
                    for a in actions:
                        acode = a.get("action_code", "")
                        aname = a.get("action_name", "")
                        lines.append(f"  - Action: **{acode}** ({aname})")
                        for fl_item in a.get("invoked_flows", []):
                            fname = fl_item.get("flow_name", "")
                            fpt = fl_item.get("process_type", "")
                            lines.append(f"    - Invokes: **{fname}** (process type: {fpt})")
                else:
                    lines.append("  - (no actions configured)")
                lines.append("")
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
        lines.append(f"  Level={h.get('hold_level', '')}, Doc={h.get('doc_type', '')}")
        if h.get("process_transaction"):
            lines.append(
                f"  Process: {h['process_transaction']}, Reject: {h.get('reject_transaction', '')}"
            )
    return "\n".join(lines)


@server.tool()
def get_monitor_rules(filter: str = "") -> str:
    """Get Sterling monitoring rules for order/shipment status monitoring.

    Args:
        filter: Search by rule name, organization, status, or process type.
    """
    fl = filter.lower()
    matches = [
        r
        for r in _monitor_rules
        if not fl
        or fl in r.get("rule_name", "").lower()
        or fl in r.get("owner_org", "").lower()
        or fl in r.get("status", "").lower()
        or fl in r.get("process_type_name", "").lower()
        or fl in r.get("monitoring_type", "").lower()
    ]
    if not matches:
        return f"No monitoring rules matching '{filter}'."
    lines = [f"Found {len(matches)} monitoring rules:\n"]
    for r in matches:
        name = r.get("rule_name", r.get("monitor_rule_key", ""))
        org = r.get("owner_org", r.get("owner", ""))
        status = r.get("status", "")
        pt = r.get("process_type_name", r.get("process_type", ""))
        active = r.get("active", "")
        lines.append(f"### {name}")
        lines.append(
            f"**Org:** {org} | **Process:** {pt} | **Status:** {status} | **Active:** {active}"
        )
        if r.get("time_to_alert"):
            lines.append(f"**Alert after:** {r['time_to_alert']} mins")
        if r.get("time_to_escalate"):
            lines.append(f"**Escalate after:** {r['time_to_escalate']} mins")
        if r.get("alert_queue_details"):
            aq = r["alert_queue_details"]
            lines.append(f"**Alert Queue:** {aq.get('queue_name', aq.get('queue_id', ''))}")
        if r.get("escalation_queue_details"):
            eq = r["escalation_queue_details"]
            lines.append(f"**Escalation Queue:** {eq.get('queue_name', eq.get('queue_id', ''))}")
        if r.get("condition"):
            cond = r["condition"]
            cn = cond.get("condition_name", "")
            ct = cond.get("condition_type", "")
            lines.append(f"**Condition:** {cn} ({ct})")
        if r.get("consolidation"):
            consol = r["consolidation"]
            ctype = consol.get("consolidation_type", "")
            cint = consol.get("consolidation_interval", "")
            lines.append(f"**Consolidation:** {ctype} every {cint} mins")
        lines.append("")
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
        lines.append(f"- **{c['code_value']}**: {c.get('code_short_description', '')}")
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
