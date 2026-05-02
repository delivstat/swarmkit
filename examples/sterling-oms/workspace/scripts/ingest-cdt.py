"""Ingest Sterling CDT XML dumps into structured JSON for the config MCP server.

Parses CDT XML files, decodes ConfigXML from YFS_SUB_FLOW, builds
cross-referenced indexes for services, pipelines, transactions,
events, user exits, and hold types.

Usage:
    python scripts/ingest-cdt.py /path/to/CDT-directory/
    python scripts/ingest-cdt.py /path/to/CDT/ --output ~/sterling-knowledge/cdt-index/
"""

from __future__ import annotations

import argparse
import html
import json
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


def _parse_cdt(path: Path) -> tuple[str, list[dict]]:
    """Parse a CDT XML file into table name + list of row dicts."""
    tree = ET.parse(path)
    root = tree.getroot()
    table_name = root.get("TableName", path.stem.replace(".cdt", ""))
    rows = []
    for child in root:
        row = dict(child.attrib)
        for k in AUDIT_COLS:
            row.pop(k, None)
        rows.append(row)
    return table_name, rows


def _decode_config_xml(encoded: str) -> ET.Element | None:
    """Decode HTML-encoded ConfigXML and parse it."""
    if not encoded or not encoded.strip():
        return None
    try:
        decoded = html.unescape(encoded)
        return ET.fromstring(decoded)
    except ET.ParseError:
        return None


def _parse_service_flow(config_xml: ET.Element) -> dict:
    """Extract structured data from a SubFlow ConfigXML."""
    nodes = []
    links = []
    for node in config_xml.findall(".//Node"):
        n = {
            "id": node.get("NodeId", ""),
            "type": node.get("NodeType", ""),
            "text": node.get("NodeText", ""),
            "group": node.get("NodeGroup", ""),
        }
        props = node.find("Properties")
        if props is not None:
            n["api_name"] = props.get("APIName", "")
            n["class_name"] = props.get("ClassName", "")
            n["method_name"] = props.get("MethodName", "")
            n["api_type"] = props.get("APIType", "")
            n["xsl_name"] = props.get("XSLName", "")
        nodes.append(n)

    for link in config_xml.findall(".//Link"):
        lk = {
            "from": link.get("FromNode", ""),
            "to": link.get("ToNode", ""),
            "type": link.get("Type", ""),
            "transport": link.get("TransportType", ""),
            "condition": link.get("ConditionValue", ""),
            "is_receiver": link.get("IsReceiver", ""),
        }
        props = link.find("Properties")
        if props is not None:
            lk["queue"] = props.get("QName", "")
            lk["threads"] = props.get("Threads", "")
            lk["runtime_id"] = props.get("RunTimeId", "")
            lk["schedule_trigger"] = props.get("ScheduleTrigger", "")
            lk["schedule_interval"] = props.get("ScheduleTriggerTimeInterval", "")
        links.append(lk)

    return {"nodes": nodes, "links": links}


def _build_services(cdt_dir: Path) -> dict:
    """Build service index from YFS_FLOW + YFS_SUB_FLOW."""
    services = {}

    flow_path = cdt_dir / "YFS_FLOW.cdt.xml"
    if flow_path.exists():
        _, rows = _parse_cdt(flow_path)
        for row in rows:
            key = row.get("FlowKey", "")
            services[key] = {
                "flow_key": key,
                "name": row.get("FlowName", ""),
                "flow_type": row.get("FlowType", ""),
                "group": row.get("FlowGroupName", ""),
                "owner": row.get("OwnerKey", ""),
                "process_type": row.get("ProcessTypeKey", ""),
                "is_realtime": row.get("IsRealTime", ""),
                "transport_type": row.get("TransportTypeKey", ""),
                "sub_flows": [],
            }

    sub_flow_path = cdt_dir / "YFS_SUB_FLOW.cdt.xml"
    if sub_flow_path.exists():
        _, rows = _parse_cdt(sub_flow_path)
        for row in rows:
            flow_key = row.get("FlowKey", "")
            config_xml_str = row.get("ConfigXML", "")
            config_el = _decode_config_xml(config_xml_str)
            parsed_flow = _parse_service_flow(config_el) if config_el else {}

            sub_flow = {
                "sub_flow_key": row.get("SubFlowKey", ""),
                "sub_flow_name": row.get("SubFlowName", ""),
                "seq_no": row.get("SeqNo", ""),
                "server_key": row.get("ServerKey", ""),
                **parsed_flow,
            }

            if flow_key in services:
                services[flow_key]["sub_flows"].append(sub_flow)

    return services


def _build_pipelines(cdt_dir: Path) -> dict:
    """Build pipeline index with definitions, conditions, pickup transactions."""
    pipelines = {}

    pipe_path = cdt_dir / "YFS_PIPELINE.cdt.xml"
    if pipe_path.exists():
        _, rows = _parse_cdt(pipe_path)
        for row in rows:
            key = row.get("PipelineKey", "")
            pipelines[key] = {
                "pipeline_key": key,
                "pipeline_id": row.get("PipelineId", ""),
                "description": row.get("PipelineDescription", ""),
                "owner": row.get("OwnerKey", ""),
                "process_type": row.get("ProcessTypeKey", ""),
                "active": row.get("ActiveFlag", ""),
                "definitions": [],
                "conditions": [],
                "pickup_transactions": [],
            }

    defn_path = cdt_dir / "YFS_PIPELINE_DEFINITION.cdt.xml"
    if defn_path.exists():
        _, rows = _parse_cdt(defn_path)
        for row in rows:
            pk = row.get("PipelineKey", "")
            if pk in pipelines:
                pipelines[pk]["definitions"].append(
                    {
                        "drop_status": row.get("DropStatus", ""),
                        "transaction_key": row.get("TransactionKey", ""),
                        "transaction_instance": row.get("TransactionInstanceKey", ""),
                    }
                )

    cond_path = cdt_dir / "YFS_PIPELINE_CONDITION.cdt.xml"
    if cond_path.exists():
        _, rows = _parse_cdt(cond_path)
        for row in rows:
            pk = row.get("PipelineKey", "")
            if pk in pipelines:
                pipelines[pk]["conditions"].append(
                    {
                        "condition_key": row.get("ConditionKey", ""),
                        "condition_value": row.get("ConditionValue", ""),
                        "pipeline_definition_key": row.get("PipelineDefinitionKey", ""),
                    }
                )

    pickup_path = cdt_dir / "YFS_PIPELINE_PICKUP_TRAN.cdt.xml"
    if pickup_path.exists():
        _, rows = _parse_cdt(pickup_path)
        for row in rows:
            pk = row.get("PipelineKey", "")
            if pk in pipelines:
                pipelines[pk]["pickup_transactions"].append(
                    {
                        "status": row.get("Status", ""),
                        "transaction_key": row.get("TransactionKey", ""),
                        "transaction_instance": row.get("TransactionInstanceKey", ""),
                    }
                )

    return pipelines


def _build_transactions(cdt_dir: Path) -> dict:
    """Build transaction index with events."""
    transactions = {}

    tran_path = cdt_dir / "YFS_TRANSACTION.cdt.xml"
    if tran_path.exists():
        _, rows = _parse_cdt(tran_path)
        for row in rows:
            key = row.get("TransactionKey", "")
            transactions[key] = {
                "transaction_key": key,
                "transaction_id": row.get("Tranid", row.get("TransactionKey", "")),
                "base_transaction": row.get("BaseTransactionKey", ""),
                "process_type": row.get("ProcessTypeKey", ""),
                "owner": row.get("OwnerKey", ""),
                "hold_type_enabled": row.get("HoldTypeEnabled", ""),
                "externally_triggerable": row.get("ExternallyTriggerable", ""),
                "events": [],
            }

    event_path = cdt_dir / "YFS_EVENT.cdt.xml"
    if event_path.exists():
        _, rows = _parse_cdt(event_path)
        for row in rows:
            tk = row.get("TransactionKey", "")
            if tk in transactions:
                transactions[tk]["events"].append(
                    {
                        "event_id": row.get("Eventid", ""),
                        "event_name": row.get("EventName", ""),
                        "active": row.get("ActiveFlag", ""),
                    }
                )

    return transactions


def _build_statuses(cdt_dir: Path) -> dict:
    """Build status lookup by process type."""
    statuses: dict[str, list[dict]] = {}
    status_path = cdt_dir / "YFS_STATUS.cdt.xml"
    if status_path.exists():
        _, rows = _parse_cdt(status_path)
        for row in rows:
            pt = row.get("ProcessTypeKey", "")
            statuses.setdefault(pt, []).append(
                {
                    "status": row.get("Status", ""),
                    "status_name": row.get("StatusName", ""),
                    "description": row.get("Description", ""),
                }
            )
    return statuses


def main() -> None:  # noqa: PLR0915
    parser = argparse.ArgumentParser(description="Ingest Sterling CDT XMLs")
    parser.add_argument("cdt_dir", type=Path, help="Directory containing *.cdt.xml files")
    parser.add_argument("--output", type=Path, default=None, help="Output directory")
    args = parser.parse_args()

    if not args.cdt_dir.is_dir():
        print(f"Not found: {args.cdt_dir}")
        sys.exit(1)

    output_dir = args.output or args.cdt_dir / "cdt-index"
    output_dir.mkdir(parents=True, exist_ok=True)
    tables_dir = output_dir / "tables"
    tables_dir.mkdir(exist_ok=True)

    print(f"Ingesting CDT from {args.cdt_dir}...")

    # Build structured indexes
    print("  Building service index...")
    services = _build_services(args.cdt_dir)
    print(f"    {len(services)} services")

    print("  Building pipeline index...")
    pipelines = _build_pipelines(args.cdt_dir)
    print(f"    {len(pipelines)} pipelines")

    print("  Building transaction index...")
    transactions = _build_transactions(args.cdt_dir)
    print(f"    {len(transactions)} transactions")

    print("  Building status index...")
    statuses = _build_statuses(args.cdt_dir)
    total_s = sum(len(v) for v in statuses.values())
    print(f"    {total_s} statuses across {len(statuses)} process types")

    # Parse common codes
    common_codes: dict[str, list[dict]] = {}
    cc_path = args.cdt_dir / "YFS_COMMON_CODE.cdt.xml"
    if cc_path.exists():
        _, rows = _parse_cdt(cc_path)
        for row in rows:
            ct = row.get("CodeType", "")
            common_codes.setdefault(ct, []).append(
                {
                    "code_value": row.get("CodeValue", ""),
                    "code_short_description": row.get("CodeShortDescription", ""),
                    "code_long_description": row.get("CodeLongDescription", ""),
                    "organization": row.get("OrganizationCode", ""),
                }
            )
        total_cc = sum(len(v) for v in common_codes.values())
        print(f"    {total_cc} common codes across {len(common_codes)} types")

    # Parse user exits
    user_exits = []
    ue_path = args.cdt_dir / "YFS_USER_EXIT_IMPL.cdt.xml"
    if ue_path.exists():
        _, rows = _parse_cdt(ue_path)
        user_exits = [
            {
                "user_exit_key": r.get("UserExitKey", ""),
                "java_class": r.get("JavaClassName", ""),
                "org": r.get("OrgKey", ""),
                "doc_type": r.get("DocumentTypeKey", ""),
                "use_flow": r.get("UseFlow", ""),
                "flow_key": r.get("FlowKey", ""),
            }
            for r in rows
        ]
        print(f"    {len(user_exits)} user exit implementations")

    # Parse hold types
    hold_types = []
    ht_path = args.cdt_dir / "YFS_HOLD_TYPE.cdt.xml"
    if ht_path.exists():
        _, rows = _parse_cdt(ht_path)
        hold_types = [
            {
                "hold_type": r.get("HoldType", ""),
                "description": r.get("HoldTypeDescription", ""),
                "hold_level": r.get("HoldLevel", ""),
                "doc_type": r.get("DocumentType", ""),
                "org": r.get("OrganizationCode", ""),
                "process_transaction": r.get("ProcessTransactionId", ""),
                "reject_transaction": r.get("RejectTransactionId", ""),
            }
            for r in rows
        ]
        print(f"    {len(hold_types)} hold types")

    # Write structured indexes
    (output_dir / "services.json").write_text(json.dumps(services, indent=2))
    (output_dir / "pipelines.json").write_text(json.dumps(pipelines, indent=2))
    (output_dir / "transactions.json").write_text(json.dumps(transactions, indent=2))
    (output_dir / "statuses.json").write_text(json.dumps(statuses, indent=2))
    (output_dir / "common_codes.json").write_text(json.dumps(common_codes, indent=2))
    (output_dir / "user_exits.json").write_text(json.dumps(user_exits, indent=2))
    (output_dir / "hold_types.json").write_text(json.dumps(hold_types, indent=2))

    # Parse all remaining tables generically
    print("  Parsing all CDT tables...")
    meta = {}
    for cdt_file in sorted(args.cdt_dir.glob("*.cdt.xml")):
        table_name, rows = _parse_cdt(cdt_file)
        meta[table_name] = {"file": cdt_file.name, "records": len(rows)}
        table_json = tables_dir / f"{table_name}.json"
        table_json.write_text(json.dumps(rows, indent=2))

    (output_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"    {len(meta)} tables")

    print(f"\nIngestion complete → {output_dir}")
    print(f"  services.json: {len(services)} services")
    print(f"  pipelines.json: {len(pipelines)} pipelines")
    print(f"  transactions.json: {len(transactions)} transactions")
    print(f"  statuses.json: {sum(len(v) for v in statuses.values())} statuses")
    print(f"  common_codes.json: {sum(len(v) for v in common_codes.values())} codes")
    print(f"  user_exits.json: {len(user_exits)} implementations")
    print(f"  hold_types.json: {len(hold_types)} hold types")
    print(f"  tables/: {len(meta)} generic tables")


if __name__ == "__main__":
    main()
