"""Parsed ``server:`` block from workspace.yaml + the workspace→dict projections the app
factory feeds to the scheduler and canary router."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_DEFAULT_MAX_CONCURRENT = 5
_DEFAULT_TIMEOUT_SECONDS = 300
_DEFAULT_MCP_ENABLED = True


@dataclass(frozen=True)
class ServerCfg:
    """Parsed ``server:`` block from workspace.yaml."""

    max_concurrent: int = _DEFAULT_MAX_CONCURRENT
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS
    mcp_enabled: bool = _DEFAULT_MCP_ENABLED


def _parse_server_config(workspace: Any) -> ServerCfg:
    """Extract server config from the resolved workspace's raw model."""
    server_raw = getattr(workspace.raw, "server", None)
    if server_raw is None:
        return ServerCfg()
    jobs = getattr(server_raw, "jobs", None)
    mcp = getattr(server_raw, "mcp", None)
    return ServerCfg(
        max_concurrent=(getattr(jobs, "max_concurrent", None) or _DEFAULT_MAX_CONCURRENT),
        timeout_seconds=(getattr(jobs, "timeout_seconds", None) or _DEFAULT_TIMEOUT_SECONDS),
        mcp_enabled=(
            bool(getattr(mcp, "enabled", _DEFAULT_MCP_ENABLED))
            if mcp is not None
            else _DEFAULT_MCP_ENABLED
        ),
    )


def _parse_canary_routes(workspace: Any) -> list[dict[str, Any]]:
    """Extract canary route configs from workspace.yaml server.canary block."""
    server_raw = getattr(workspace.raw, "server", None)
    if server_raw is None:
        return []
    canary = getattr(server_raw, "canary", None)
    if canary is None:
        return []
    routes = getattr(canary, "routes", None) or []
    result: list[dict[str, Any]] = []
    for route in routes:
        versions = []
        for v in route.versions:
            entry: dict[str, Any] = {"version": v.version, "weight": v.weight}
            if v.promote_when is not None:
                entry["promote_when"] = v.promote_when.model_dump(exclude_none=True)
            versions.append(entry)
        result.append({"topology": route.topology, "versions": versions})
    return result


def _parse_trigger_configs(workspace: Any) -> list[dict[str, Any]]:
    """Convert resolved triggers to plain dicts for the scheduler."""
    configs: list[dict[str, Any]] = []
    triggers = getattr(workspace, "triggers", ()) or ()
    for rt in triggers:
        raw = rt.raw
        config_obj = getattr(raw, "config", None)
        config_dict: dict[str, Any] = {}
        if config_obj is not None:
            config_dict = dict(config_obj.model_dump(exclude_none=True))
        configs.append(
            {
                "id": rt.id,
                "type": raw.type.value,
                "enabled": raw.enabled if raw.enabled is not None else True,
                "targets": list(rt.targets),
                "config": config_dict,
            }
        )
    return configs
