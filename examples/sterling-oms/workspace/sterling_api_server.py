# /// script
# dependencies = ["httpx>=0.27", "mcp[cli]>=1.0"]
# ///
"""Sterling OMS API MCP server — live config access for agents.

Wraps Sterling Service APIs so agents can query the actual
configuration from Application Manager / Channel Configurator.
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

server = FastMCP("sterling-api")

_BASE_URL = os.environ.get("STERLING_API_URL", "http://localhost:9080/smcfs/restapi/")
_USER = os.environ.get("STERLING_API_USER", "admin")
_PASSWORD = os.environ.get("STERLING_API_PASSWORD", "password")


async def _call_api(api_name: str, input_xml: str = "<Input/>") -> str:
    """Call a Sterling Service API and return the response XML."""
    url = f"{_BASE_URL}{api_name}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            content=input_xml,
            headers={"Content-Type": "application/xml", "Accept": "application/xml"},
            auth=(_USER, _PASSWORD),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.text


@server.tool()
async def get_organization_list() -> str:
    """List all organizations configured in Sterling OMS."""
    return await _call_api("getOrganizationList", '<Organization OrganizationCode=""/>')


@server.tool()
async def get_flow_list(flow_name: str = "", document_type: str = "") -> str:
    """List pipelines/flows (Application Manager > Process Modeling > Pipelines)."""
    attrs = []
    if flow_name:
        attrs.append(f'FlowName="{flow_name}"')
    if document_type:
        attrs.append(f'DocumentType="{document_type}"')
    return await _call_api("getFlowList", f"<Flow {' '.join(attrs)}/>")


@server.tool()
async def get_document_type_list(document_type: str = "") -> str:
    """List document types (Order, Return, Transfer, etc.)."""
    attr = f'DocumentType="{document_type}"' if document_type else ""
    return await _call_api("getDocumentTypeList", f"<DocumentType {attr}/>")


@server.tool()
async def get_agent_list(agent_criteria_id: str = "") -> str:
    """List configured agents and their scheduling."""
    attr = f'AgentCriteriaId="{agent_criteria_id}"' if agent_criteria_id else ""
    return await _call_api("getAgentList", f"<Agent {attr}/>")


@server.tool()
async def get_sourcing_rule_list(organization_code: str = "DEFAULT") -> str:
    """List DOM sourcing rules (Channel Configurator > Sourcing & Scheduling)."""
    return await _call_api(
        "getSourcingRuleList", f'<SourcingRule OrganizationCode="{organization_code}"/>'
    )


@server.tool()
async def get_service_definition_list(service_name: str = "") -> str:
    """List service definitions (API configurations)."""
    attr = f'ServiceName="{service_name}"' if service_name else ""
    return await _call_api("getServiceDefinitionList", f"<Service {attr}/>")


@server.tool()
async def get_hold_type_list(organization_code: str = "DEFAULT") -> str:
    """List order hold types and their resolution rules."""
    return await _call_api("getHoldTypeList", f'<HoldType OrganizationCode="{organization_code}"/>')


@server.tool()
async def get_inventory_node_list(organization_code: str = "DEFAULT") -> str:
    """List inventory/fulfillment nodes (stores, DCs, drop-ship vendors)."""
    return await _call_api("getShipNodeList", f'<ShipNode OrganizationCode="{organization_code}"/>')


@server.tool()
async def get_item_details(item_id: str, organization_code: str = "DEFAULT") -> str:
    """Get item/product details including inventory configuration."""
    return await _call_api(
        "getItemDetails",
        f'<Item ItemID="{item_id}" OrganizationCode="{organization_code}"/>',
    )


@server.tool()
async def call_sterling_api(api_name: str, input_xml: str) -> str:
    """Call any Sterling Service API by name with custom input XML.

    Use for APIs not covered by the specific tools above. You must
    know the API name and input XML format from the Javadocs.
    """
    return await _call_api(api_name, input_xml)


if __name__ == "__main__":
    server.run()
