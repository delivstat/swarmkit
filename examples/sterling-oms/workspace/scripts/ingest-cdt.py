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
    org_lookup = _build_org_lookup(cdt_dir)
    pt_lookup = _build_process_type_lookup(cdt_dir)
    services = {}

    flow_path = cdt_dir / "YFS_FLOW.cdt.xml"
    if flow_path.exists():
        _, rows = _parse_cdt(flow_path)
        for row in rows:
            key = row.get("FlowKey", "")
            owner_key = row.get("OwnerKey", "")
            pt_key = row.get("ProcessTypeKey", "")
            services[key] = {
                "flow_key": key,
                "name": row.get("FlowName", ""),
                "flow_type": row.get("FlowType", ""),
                "group": row.get("FlowGroupName", ""),
                "owner": owner_key,
                "owner_org": org_lookup.get(owner_key, owner_key),
                "process_type": pt_key,
                "process_type_name": pt_lookup.get(pt_key, pt_key),
                "graph_ui_key": row.get("GraphUIKey", ""),
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


def _build_org_lookup(cdt_dir: Path) -> dict[str, str]:
    """Build OrganizationKey → OrganizationCode lookup from YFS_ORGANIZATION."""
    lookup: dict[str, str] = {}
    org_path = cdt_dir / "YFS_ORGANIZATION.cdt.xml"
    if org_path.exists():
        _, rows = _parse_cdt(org_path)
        for row in rows:
            key = row.get("OrganizationKey", "")
            code = row.get("OrganizationCode", row.get("OrganizationName", key))
            if key:
                lookup[key] = code
    return lookup


def _build_process_type_lookup(cdt_dir: Path) -> dict[str, str]:
    """Build ProcessTypeKey → ProcessType name lookup from YFS_PROCESS_TYPE."""
    lookup: dict[str, str] = {}
    pt_path = cdt_dir / "YFS_PROCESS_TYPE.cdt.xml"
    if pt_path.exists():
        _, rows = _parse_cdt(pt_path)
        for row in rows:
            key = row.get("ProcessTypeKey", "")
            name = row.get("ProcessType", row.get("ProcessTypeName", key))
            if key:
                lookup[key] = name
    return lookup


def _build_pipelines(cdt_dir: Path) -> dict:  # noqa: PLR0912
    """Build pipeline index with definitions, conditions, pickup transactions.

    Hub rule linkage: conditions map to definitions via PipelineDefinitionKey.
    Each condition specifies the organization/enterprise rule, and the linked
    definition specifies which transaction to execute. This tells you which
    pipeline path each organization uses.

    Resolves OwnerKey → organization name, ProcessTypeKey → process type name.
    Conditions are sorted by priority (lower = evaluated first).
    """
    org_lookup = _build_org_lookup(cdt_dir)
    pt_lookup = _build_process_type_lookup(cdt_dir)

    pipelines = {}

    pipe_path = cdt_dir / "YFS_PIPELINE.cdt.xml"
    if pipe_path.exists():
        _, rows = _parse_cdt(pipe_path)
        for row in rows:
            key = row.get("PipelineKey", "")
            owner_key = row.get("OwnerKey", "")
            pt_key = row.get("ProcessTypeKey", "")
            pipelines[key] = {
                "pipeline_key": key,
                "pipeline_id": row.get("PipelineId", ""),
                "description": row.get("PipelineDescription", ""),
                "owner": owner_key,
                "owner_org": org_lookup.get(owner_key, owner_key),
                "process_type": pt_key,
                "process_type_name": pt_lookup.get(pt_key, pt_key),
                "active": row.get("ActiveFlag", ""),
                "definitions": [],
                "conditions": [],
                "hub_rules": [],
                "pickup_transactions": [],
            }

    defn_path = cdt_dir / "YFS_PIPELINE_DEFINITION.cdt.xml"
    defns_by_key: dict[str, dict] = {}
    if defn_path.exists():
        _, rows = _parse_cdt(defn_path)
        for row in rows:
            pk = row.get("PipelineKey", "")
            pdk = row.get("PipelineDefinitionKey", "")
            defn = {
                "pipeline_definition_key": pdk,
                "drop_status": row.get("DropStatus", ""),
                "transaction_key": row.get("TransactionKey", ""),
                "transaction_instance": row.get("TransactionInstanceKey", ""),
            }
            defns_by_key[pdk] = defn
            if pk in pipelines:
                pipelines[pk]["definitions"].append(defn)

    cond_path = cdt_dir / "YFS_PIPELINE_CONDITION.cdt.xml"
    if cond_path.exists():
        _, rows = _parse_cdt(cond_path)
        for row in rows:
            pk = row.get("PipelineKey", "")
            pdk = row.get("PipelineDefinitionKey", "")
            priority = row.get("Priority", row.get("SequenceNo", "999"))
            condition = {
                "condition_key": row.get("ConditionKey", ""),
                "condition_value": row.get("ConditionValue", ""),
                "pipeline_definition_key": pdk,
                "priority": priority,
            }
            if pk in pipelines:
                pipelines[pk]["conditions"].append(condition)
                linked_defn = defns_by_key.get(pdk)
                if linked_defn:
                    pipelines[pk]["hub_rules"].append(
                        {
                            "condition_key": condition["condition_key"],
                            "condition_value": condition["condition_value"],
                            "priority": priority,
                            "maps_to_definition": pdk,
                            "drop_status": linked_defn["drop_status"],
                            "transaction_key": linked_defn["transaction_key"],
                            "transaction_instance": linked_defn["transaction_instance"],
                        }
                    )

    for pipe in pipelines.values():
        pipe["hub_rules"].sort(key=lambda r: str(r.get("priority", "999")))

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


def _build_transactions(cdt_dir: Path) -> dict:  # noqa: PLR0912
    """Build transaction index with events, actions, and invoked flows.

    Joins: YFS_TRANSACTION → YFS_EVENT → YFS_EVENT_CONDITION →
           YFS_ACTION → YFS_INVOKED_FLOWS → YFS_FLOW
    """
    transactions = {}

    # 1. Transactions
    tran_path = cdt_dir / "YFS_TRANSACTION.cdt.xml"
    if tran_path.exists():
        _, rows = _parse_cdt(tran_path)
        for row in rows:
            key = row.get("TransactionKey", "")
            transactions[key] = {
                "transaction_key": key,
                "transaction_id": row.get("Tranid", row.get("TransactionKey", "")),
                "transaction_name": row.get("Tranname", ""),
                "base_transaction": row.get("BaseTransactionKey", ""),
                "process_type": row.get("ProcessTypeKey", ""),
                "owner": row.get("OwnerKey", ""),
                "hold_type_enabled": row.get("HoldTypeEnabled", ""),
                "externally_triggerable": row.get("ExternallyTriggerable", ""),
                "events": [],
            }

    # 2. Events (keyed by EventKey for condition lookup)
    events_by_key: dict[str, dict] = {}
    event_path = cdt_dir / "YFS_EVENT.cdt.xml"
    if event_path.exists():
        _, rows = _parse_cdt(event_path)
        for row in rows:
            ek = row.get("EventKey", "")
            tk = row.get("TransactionKey", "")
            event = {
                "event_key": ek,
                "event_id": row.get("Eventid", ""),
                "event_name": row.get("EventName", ""),
                "active": row.get("ActiveFlag", ""),
                "actions": [],
            }
            events_by_key[ek] = event
            if tk in transactions:
                transactions[tk]["events"].append(event)

    # 3. Actions (keyed by ActionKey for invoked flow lookup)
    actions_by_key: dict[str, dict] = {}
    action_path = cdt_dir / "YFS_ACTION.cdt.xml"
    if action_path.exists():
        _, rows = _parse_cdt(action_path)
        for row in rows:
            ak = row.get("ActionKey", "")
            actions_by_key[ak] = {
                "action_key": ak,
                "action_code": row.get("ActionCode", row.get("Actioncode", "")),
                "action_name": row.get("ActionName", row.get("Actionname", "")),
                "invoked_flows": [],
            }

    # 4. Event Conditions → link events to actions
    cond_path = cdt_dir / "YFS_EVENT_CONDITION.cdt.xml"
    if cond_path.exists():
        _, rows = _parse_cdt(cond_path)
        for row in rows:
            ek = row.get("EventKey", "")
            ak = row.get("ActionKey", "")
            if ek in events_by_key and ak in actions_by_key:
                action = actions_by_key[ak]
                if action not in events_by_key[ek]["actions"]:
                    events_by_key[ek]["actions"].append(action)

    # 5. Flows (keyed by FlowKey)
    flows_by_key: dict[str, dict] = {}
    flow_path = cdt_dir / "YFS_FLOW.cdt.xml"
    if flow_path.exists():
        _, rows = _parse_cdt(flow_path)
        for row in rows:
            fk = row.get("FlowKey", "")
            flows_by_key[fk] = {
                "flow_key": fk,
                "flow_name": row.get("FlowName", ""),
                "process_type": row.get("ProcessTypeKey", ""),
            }

    # 6. Invoked Flows → link actions to flows
    invoked_path = cdt_dir / "YFS_INVOKED_FLOWS.cdt.xml"
    if invoked_path.exists():
        _, rows = _parse_cdt(invoked_path)
        for row in rows:
            ak = row.get("ActionKey", "")
            fk = row.get("FlowKey", "")
            if ak in actions_by_key and fk in flows_by_key:
                actions_by_key[ak]["invoked_flows"].append(flows_by_key[fk])

    return transactions


def _build_statuses(cdt_dir: Path) -> dict:
    """Build status lookup by process type.

    Each status includes its owner organization so enterprise-specific
    status names are preserved. The lookup is keyed by ProcessTypeKey
    for backward compatibility — callers can filter by owner if needed.
    """
    org_lookup = _build_org_lookup(cdt_dir)
    statuses: dict[str, list[dict]] = {}
    status_path = cdt_dir / "YFS_STATUS.cdt.xml"
    if status_path.exists():
        _, rows = _parse_cdt(status_path)
        for row in rows:
            pt = row.get("ProcessTypeKey", "")
            owner_key = row.get("OwnerKey", "")
            statuses.setdefault(pt, []).append(
                {
                    "status": row.get("Status", ""),
                    "status_name": row.get("StatusName", ""),
                    "description": row.get("Description", ""),
                    "owner": owner_key,
                    "owner_org": org_lookup.get(owner_key, owner_key),
                }
            )
    return statuses


def _build_monitor_rules(cdt_dir: Path) -> list[dict]:
    """Build monitoring rules index from YFS_MONITOR_RULE + YFS_MONITORING_CONSOLIDATION.

    Monitor rules define alerts for orders/shipments stuck in a status.
    Each rule specifies the process type, status to monitor, timeout,
    and what action to take (raise alert, escalate, etc.).
    Consolidation records define how monitoring events are grouped.
    """
    org_lookup = _build_org_lookup(cdt_dir)
    pt_lookup = _build_process_type_lookup(cdt_dir)

    rules: list[dict] = []
    rule_path = cdt_dir / "YFS_MONITOR_RULE.cdt.xml"
    if rule_path.exists():
        _, rows = _parse_cdt(rule_path)
        for row in rows:
            owner_key = row.get("OwnerKey", "")
            pt_key = row.get("ProcessTypeKey", "")
            rules.append(
                {
                    "monitor_rule_key": row.get("MonitorRuleKey", ""),
                    "rule_name": row.get("MonitorRuleName", row.get("RuleName", "")),
                    "owner": owner_key,
                    "owner_org": org_lookup.get(owner_key, owner_key),
                    "process_type": pt_key,
                    "process_type_name": pt_lookup.get(pt_key, pt_key),
                    "document_type": row.get("DocumentType", ""),
                    "status": row.get("Status", ""),
                    "pipeline_key": row.get("PipelineKey", ""),
                    "monitoring_type": row.get("MonitoringType", ""),
                    "priority": row.get("Priority", ""),
                    "active": row.get("ActiveFlag", row.get("IsActive", "")),
                    "time_to_alert": row.get("TimeToAlert", ""),
                    "time_to_escalate": row.get("TimeToEscalate", ""),
                    "alert_queue": row.get("AlertQueue", ""),
                    "escalation_queue": row.get("EscalationQueue", ""),
                    "max_monitor_days": row.get("MaxMonitorDays", ""),
                    "condition_key": row.get("ConditionKey", ""),
                    "consolidation_key": row.get("ConsolidationKey", ""),
                }
            )

    consolidations: dict[str, dict] = {}
    consol_path = cdt_dir / "YFS_MONITORING_CONSOLIDATION.cdt.xml"
    if consol_path.exists():
        _, rows = _parse_cdt(consol_path)
        for row in rows:
            ck = row.get("ConsolidationKey", "")
            consolidations[ck] = {
                "consolidation_key": ck,
                "consolidation_type": row.get("ConsolidationType", ""),
                "consolidation_interval": row.get("ConsolidationInterval", ""),
                "max_records": row.get("MaxRecords", ""),
            }

    conditions = _build_condition_lookup(cdt_dir)
    queues = _build_queue_lookup(cdt_dir)

    for rule in rules:
        ck = rule.get("consolidation_key", "")
        if ck and ck in consolidations:
            rule["consolidation"] = consolidations[ck]
        cond_key = rule.get("condition_key", "")
        if cond_key and cond_key in conditions:
            rule["condition"] = conditions[cond_key]
        for q_field in ("alert_queue", "escalation_queue"):
            q_key = rule.get(q_field, "")
            if q_key and q_key in queues:
                rule[f"{q_field}_details"] = queues[q_key]

    return rules


def _build_queue_lookup(cdt_dir: Path) -> dict[str, dict]:
    """Build queue lookup from YFS_QUEUE table."""
    queues: dict[str, dict] = {}
    queue_path = cdt_dir / "YFS_QUEUE.cdt.xml"
    if queue_path.exists():
        _, rows = _parse_cdt(queue_path)
        for row in rows:
            qk = row.get("QueueKey", "")
            queues[qk] = {
                "queue_key": qk,
                "queue_id": row.get("QueueId", row.get("QueueID", "")),
                "queue_name": row.get("QueueName", row.get("QueueDescription", "")),
                "queue_type": row.get("QueueType", ""),
                "owner": row.get("OwnerKey", ""),
            }
    return queues


def _build_condition_lookup(cdt_dir: Path) -> dict[str, dict]:
    """Build condition lookup from YFS_CONDITION table."""
    conditions: dict[str, dict] = {}
    cond_path = cdt_dir / "YFS_CONDITION.cdt.xml"
    if cond_path.exists():
        _, rows = _parse_cdt(cond_path)
        for row in rows:
            ck = row.get("ConditionKey", "")
            conditions[ck] = {
                "condition_key": ck,
                "condition_id": row.get("ConditionId", row.get("ConditionID", "")),
                "condition_name": row.get("ConditionName", ""),
                "condition_type": row.get("ConditionType", ""),
                "condition_value": row.get("ConditionValue", ""),
                "class_name": row.get("ClassName", ""),
                "owner": row.get("OwnerKey", ""),
                "process_type": row.get("ProcessTypeKey", ""),
            }
    return conditions


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

    print("  Building transaction index (with events, actions, flows)...")
    transactions = _build_transactions(args.cdt_dir)
    total_events = sum(len(t.get("events", [])) for t in transactions.values())
    total_actions = sum(
        len(a.get("actions", [])) for t in transactions.values() for a in t.get("events", [])
    )
    print(f"    {len(transactions)} transactions, {total_events} events, {total_actions} actions")

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

    # Build monitoring rules and link to pipelines
    print("  Building monitoring rules index...")
    monitor_rules = _build_monitor_rules(args.cdt_dir)
    for rule in monitor_rules:
        pk = rule.get("pipeline_key", "")
        if pk and pk in pipelines:
            pipelines[pk].setdefault("monitor_rules", []).append(
                {
                    "rule_name": rule.get("rule_name", ""),
                    "status": rule.get("status", ""),
                    "monitoring_type": rule.get("monitoring_type", ""),
                    "time_to_alert": rule.get("time_to_alert", ""),
                    "time_to_escalate": rule.get("time_to_escalate", ""),
                    "active": rule.get("active", ""),
                    "condition": rule.get("condition", {}).get("condition_name", ""),
                }
            )
    print(f"    {len(monitor_rules)} monitoring rules")

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
    (output_dir / "monitor_rules.json").write_text(json.dumps(monitor_rules, indent=2))

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
    print(f"  monitor_rules.json: {len(monitor_rules)} monitoring rules")
    print(f"  tables/: {len(meta)} generic tables")


if __name__ == "__main__":
    main()
