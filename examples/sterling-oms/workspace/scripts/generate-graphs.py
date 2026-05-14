"""Generate visual Mermaid diagrams from YFS_GRAPH_UI CDT data.

Parses the GraphXml from YFS_GRAPH_UI and enriches nodes with details
from the existing CDT index (pipelines, services, transactions, statuses).
Outputs .md files with Mermaid diagrams organized by type.

Usage:
    python scripts/generate-graphs.py /path/to/CDT/ --index ~/knowledge/cdt-index/
    python scripts/generate-graphs.py /path/to/CDT/ \
        --index ~/knowledge/cdt-index/ --output ~/graphs/

Output structure:
    graphs/
    ├── pipelines/         # Pipeline status flows with swim lanes
    ├── flows/             # Service/flow directed graphs
    ├── events/            # Event condition handler graphs
    ├── status-conditions/ # Status transition condition graphs
    └── hub-rules/         # Pipeline determination rules
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

AUDIT_COLS = {
    "Createprogid",
    "Createts",
    "Createuserid",
    "Modifyprogid",
    "Modifyts",
    "Modifyuserid",
    "Lockid",
}


def _safe_id(text: str) -> str:
    """Make a string safe for Mermaid node IDs."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", text)


def _safe_label(text: str) -> str:
    """Escape text for Mermaid labels."""
    return text.replace('"', "'").replace("\n", "\\n")


def _parse_graph_xml(raw: str) -> ET.Element | None:
    """Decode and parse GraphXml."""
    if not raw or not raw.strip():
        return None
    try:
        decoded = html.unescape(raw)
        return ET.fromstring(decoded)
    except ET.ParseError:
        return None


def _load_index(index_dir: Path) -> dict:
    """Load the CDT index for enrichment."""
    data = {}
    for name in [
        "pipelines.json",
        "services.json",
        "transactions.json",
        "statuses.json",
        "common_codes.json",
    ]:
        path = index_dir / name
        if path.exists():
            data[name.replace(".json", "")] = json.loads(path.read_text())
    return data


def _get_status_name(statuses: dict, process_type: str, status: str) -> str:
    """Look up a status name from the statuses index."""
    for s in statuses.get(process_type, []):
        if s.get("status") == status:
            return s.get("status_name", "")
    return ""


def _get_transaction_name(transactions: dict, tran_key: str) -> str:
    """Look up a transaction name."""
    t = transactions.get(tran_key, {})
    return t.get("transaction_id", t.get("transaction_name", tran_key))


# ---- Pipeline Graphs -------------------------------------------------------


def generate_pipeline_graph(  # noqa: PLR0912, PLR0915
    graph_el: ET.Element,
    ref_key: str,
    owner: str,
    process_type: str,
    index: dict,
) -> str:
    """Generate Mermaid from a Pipeline-type YFS_GRAPH_UI entry."""
    pipelines = index.get("pipelines", {})
    statuses = index.get("statuses", {})
    pipe = pipelines.get(ref_key, {})
    pipe_id = pipe.get("pipeline_id", ref_key)

    lines = [f"# Pipeline: {pipe_id}", ""]
    lines.append(f"**Owner:** {pipe.get('owner_org', owner)}")
    lines.append(f"**Process Type:** {pipe.get('process_type_name', process_type)}")
    lines.append("")

    graph_nodes = graph_el.findall(".//Node")
    graph_links = graph_el.findall(".//Link")

    lines.append("```mermaid")
    lines.append("graph TD")

    node_by_uindex: dict[str, str] = {}
    node_map: dict[str, str] = {}
    for node in graph_nodes:
        key = node.get("Key", "")
        text = node.get("Text", key)
        uindex = node.get("UIndex", "")
        safe = _safe_id(key) or f"N{len(node_map)}"

        if not text:
            continue

        status_name = _get_status_name(statuses, process_type, text)
        label = f"{text} {status_name}" if status_name else text

        defns = pipe.get("definitions", [])
        tran = ""
        for d in defns:
            if d.get("drop_status") == text:
                tran = d.get("transaction_key", "")
                break
        if tran:
            tran_name = _get_transaction_name(index.get("transactions", {}), tran)
            label += f"\\n({tran_name})"

        node_map[key] = safe
        if uindex:
            node_by_uindex[uindex] = safe
        lines.append(f'    {safe}["{_safe_label(label)}"]')

    for link in graph_links:
        frm = link.get("From", "")
        to = link.get("To", "")
        text = link.get("Text", "")
        frm_safe = node_by_uindex.get(frm, node_map.get(frm))
        to_safe = node_by_uindex.get(to, node_map.get(to))
        if frm_safe and to_safe and frm_safe != to_safe:
            if text:
                lines.append(f"    {frm_safe} -->|{_safe_label(text)}| {to_safe}")
            else:
                lines.append(f"    {frm_safe} --> {to_safe}")

    lines.append("```")

    if pipe.get("hub_rules"):
        lines.append("")
        lines.append("## Hub Rules")
        lines.append("")
        for r in pipe["hub_rules"]:
            ent = r.get("enterprise_name", "")
            cn = r.get("condition_name", "")
            cr = r.get("condition_rule", "")
            pri = r.get("priority", "")
            rule_str = f": `{cr}`" if cr else ""
            lines.append(f"- [P{pri}] {ent} — {cn}{rule_str}")

    if pipe.get("listeners"):
        lines.append("")
        lines.append("## Listeners")
        lines.append("")
        for ln in pipe["listeners"]:
            pt = ln.get("listening_to_process_type_name", "")
            st = ln.get("listening_to_status", "")
            tr = ln.get("transaction_key", "")
            lines.append(f"- Listens to **{pt}** status {st} → triggers {tr}")

    return "\n".join(lines)


# ---- Flow Graphs -----------------------------------------------------------


def generate_flow_graph(  # noqa: PLR0912
    graph_el: ET.Element,
    ref_key: str,
    owner: str,
    process_type: str,
    index: dict,
) -> str:
    """Generate Mermaid from a Flow-type YFS_GRAPH_UI entry."""
    services = index.get("services", {})
    svc = services.get(ref_key, {})
    svc_name = svc.get("name", ref_key)

    lines = [f"# Flow: {svc_name}", ""]
    lines.append(f"**Owner:** {svc.get('owner_org', owner)}")
    lines.append(f"**Process Type:** {svc.get('process_type_name', process_type)}")
    lines.append(f"**Flow Type:** {svc.get('flow_type', '')}")
    lines.append("")

    graph_nodes = graph_el.findall(".//Node")
    graph_links = graph_el.findall(".//Link")

    lines.append("```mermaid")
    lines.append("graph LR")

    node_map: dict[str, str] = {}
    for node in graph_nodes:
        key = node.get("Key", "")
        text = node.get("Text", key)
        ntype = node.get("Type", "")
        safe = _safe_id(key) or f"N{len(node_map)}"

        if ntype == "1":
            node_map[key] = safe
            lines.append(f"    {safe}([Start])")
        elif ntype == "2":
            node_map[key] = safe
            lines.append(f"    {safe}([End])")
        else:
            label = _safe_label(text) if text else safe
            node_map[key] = safe
            lines.append(f'    {safe}["{label}"]')

    for link in graph_links:
        frm = link.get("From", link.get("FromNode", ""))
        to = link.get("To", link.get("ToNode", ""))
        label = link.get("Label", link.get("Text", ""))
        if frm in node_map and to in node_map:
            if label:
                lines.append(f"    {node_map[frm]} -->|{_safe_label(label)}| {node_map[to]}")
            else:
                lines.append(f"    {node_map[frm]} --> {node_map[to]}")

    lines.append("```")

    if svc.get("sub_flows"):
        lines.append("")
        lines.append("## Sub-Flow Details")
        for sf in svc["sub_flows"]:
            lines.append(f"\n### {sf.get('sub_flow_name', '')}")
            for n in sf.get("nodes", []):
                ntype = n.get("type", "")
                if ntype == "API":
                    api = n.get("api_name", "")
                    cls = n.get("class_name", "")
                    lines.append(f"- **API:** {api} — `{cls}`")
                elif ntype == "XSL":
                    lines.append(f"- **XSL:** {n.get('xsl_name', '')}")
                elif ntype not in ("Start", "End"):
                    lines.append(f"- [{ntype}] {n.get('text', '')}")

    return "\n".join(lines)


# ---- Event Condition Graphs ------------------------------------------------


def generate_event_graph(
    graph_el: ET.Element,
    ref_key: str,
    owner: str,
    process_type: str,
    index: dict,
) -> str:
    """Generate Mermaid from an EventCondition-type YFS_GRAPH_UI entry."""
    lines = [f"# Event Condition: {ref_key}", ""]
    lines.append(f"**Owner:** {owner}")
    lines.append(f"**Process Type:** {process_type}")
    lines.append("")

    graph_nodes = graph_el.findall(".//Node")
    graph_links = graph_el.findall(".//Link")

    lines.append("```mermaid")
    lines.append("graph TD")

    node_map: dict[str, str] = {}
    for node in graph_nodes:
        key = node.get("Key", "")
        text = node.get("Text", key)
        safe = _safe_id(key) or f"N{len(node_map)}"
        node_map[key] = safe
        lines.append(f'    {safe}["{_safe_label(text)}"]')

    for link in graph_links:
        frm = link.get("From", link.get("FromNode", ""))
        to = link.get("To", link.get("ToNode", ""))
        label = link.get("Label", link.get("Text", ""))
        if frm in node_map and to in node_map:
            if label:
                lines.append(f"    {node_map[frm]} -->|{_safe_label(label)}| {node_map[to]}")
            else:
                lines.append(f"    {node_map[frm]} --> {node_map[to]}")

    lines.append("```")
    return "\n".join(lines)


# ---- Hub Rule (PDRule) Graphs ----------------------------------------------


def generate_pdrule_graph(
    graph_el: ET.Element,
    ref_key: str,
    owner: str,
    process_type: str,
    index: dict,
) -> str:
    """Generate Mermaid from a PDRule-type YFS_GRAPH_UI entry."""
    conditions = graph_el.findall(".//PipelineCondition")

    lines = [f"# Hub Rule: {process_type} ({owner})", ""]
    lines.append("```mermaid")
    lines.append("graph TD")
    lines.append(f'    HUB{{"Pipeline Determination\\n{process_type}"}}')

    pipelines = index.get("pipelines", {})
    for _i, cond in enumerate(conditions):
        pk = cond.get("PipelineKey", "")
        ck = cond.get("ConditionKey", "")
        ent = cond.get("EnterpriseKey", "")
        pri = cond.get("Priority", "")

        pipe = pipelines.get(pk, {})
        pipe_name = pipe.get("pipeline_id", pk)

        safe_pipe = _safe_id(pipe_name)
        lines.append(f'    {safe_pipe}["{_safe_label(pipe_name)}"]')
        if ck:
            lines.append(f"    HUB -->|P{pri} {ent}| {safe_pipe}")
        else:
            lines.append(f"    HUB -->|P{pri} default| {safe_pipe}")

    lines.append("```")
    return "\n".join(lines)


# ---- Main ------------------------------------------------------------------


def main() -> None:  # noqa: PLR0912, PLR0915
    parser = argparse.ArgumentParser(description="Generate Mermaid diagrams from YFS_GRAPH_UI")
    parser.add_argument("cdt_dir", type=Path, help="CDT directory with YFS_GRAPH_UI.cdt.xml")
    parser.add_argument(
        "--index", type=Path, required=True, help="CDT index directory (from ingest-cdt.py)"
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Output directory (default: <index>/graphs)"
    )
    args = parser.parse_args()

    graph_ui_path = args.cdt_dir / "YFS_GRAPH_UI.cdt.xml"
    if not graph_ui_path.exists():
        print(f"Not found: {graph_ui_path}")
        sys.exit(1)

    index = _load_index(args.index)
    output_dir = args.output or args.index / "graphs"

    type_dirs = {
        "Pipeline": "pipelines",
        "Flow": "flows",
        "EventCondition": "events",
        "StatusCondition": "status-conditions",
        "PDRule": "hub-rules",
    }
    for d in type_dirs.values():
        (output_dir / d).mkdir(parents=True, exist_ok=True)

    tree = ET.parse(graph_ui_path)
    root = tree.getroot()

    counts: dict[str, int] = {}
    errors = 0

    for child in root:
        attrs = {k: v for k, v in child.attrib.items() if k not in AUDIT_COLS}
        graph_type = attrs.get("GraphType", "")
        ref_key = attrs.get("GraphRefKey", "")
        owner = attrs.get("OwnerKey", "")
        process_type = attrs.get("ProcessTypeKey", "")
        graph_xml = attrs.get("GraphXml", "")

        if graph_type not in type_dirs:
            continue

        graph_el = _parse_graph_xml(graph_xml)
        if graph_el is None:
            errors += 1
            continue

        try:
            if graph_type == "Pipeline":
                content = generate_pipeline_graph(graph_el, ref_key, owner, process_type, index)
            elif graph_type == "Flow":
                content = generate_flow_graph(graph_el, ref_key, owner, process_type, index)
            elif graph_type in ("EventCondition", "StatusCondition"):
                content = generate_event_graph(graph_el, ref_key, owner, process_type, index)
            elif graph_type == "PDRule":
                content = generate_pdrule_graph(graph_el, ref_key, owner, process_type, index)
            else:
                continue
        except Exception as exc:
            errors += 1
            print(f"  Error generating {graph_type}/{ref_key}: {exc}", file=sys.stderr)
            continue

        # Use human-readable name when available
        display_name = ref_key
        if graph_type == "Pipeline":
            pipe = index.get("pipelines", {}).get(ref_key, {})
            display_name = pipe.get("pipeline_id", ref_key)
        elif graph_type == "Flow":
            svc = index.get("services", {}).get(ref_key, {})
            display_name = svc.get("name", ref_key)
        elif graph_type == "PDRule":
            display_name = f"{process_type}_{owner}"

        safe_name = re.sub(r"[^a-zA-Z0-9_\-.]", "_", display_name)[:80]
        subdir = type_dirs[graph_type]
        out_path = output_dir / subdir / f"{safe_name}.md"
        out_path.write_text(content)
        counts[graph_type] = counts.get(graph_type, 0) + 1

    print(f"\nGenerated diagrams → {output_dir}")
    for gt, count in sorted(counts.items()):
        print(f"  {type_dirs.get(gt, gt)}/: {count} diagrams")
    if errors:
        print(f"  ({errors} errors skipped)")
    print(f"  Total: {sum(counts.values())} diagrams")


if __name__ == "__main__":
    main()
