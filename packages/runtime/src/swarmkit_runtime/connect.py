"""Mode B poll connector — outbound-only control for NAT'd / edge instances.

Runs alongside an instance's ``swarmkit serve`` and reaches the control-plane panel over
**outbound HTTPS only** (no inbound port, no VPN). It long-polls the panel's per-instance command
queue, executes each command against **local serve over loopback** (trusted), and reports the
result. The transport is inverted but the panel stays the decider — the runner pattern.

The verb→tier map mirrors the panel's ``swarmkit_control_plane._verbs``; keep the two in sync. Tier
re-validation here is defense in depth: the panel already bounds enqueues to the instance's granted
tier. See design/details/control-plane/13-connector-registry.md §"Mode B — poll connector".
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx

# verb -> (http method, serve path template, required tier). Path placeholders are filled from the
# command's args; a "body" arg (dict) becomes the JSON request body for writes.
_VERB_ROUTES: dict[str, tuple[str, str, str]] = {
    "capabilities": ("GET", "/capabilities", "read"),
    "usage": ("GET", "/usage", "read"),
    "job-status": ("GET", "/jobs/{job_id}", "read"),
    "validate": ("GET", "/validate", "read"),
    "run": ("POST", "/run/{topology_name}", "run"),
    "reload": ("POST", "/api/reload", "admin"),
}

# `deploy` (governed artifact push) resolves its route from the command's `kind` arg.
_DEPLOY_PLURAL: dict[str, str] = {
    "topology": "topologies",
    "skill": "skills",
    "archetype": "archetypes",
}

_TIER_RANK: dict[str, int] = {"read": 0, "run": 1, "admin": 2}


class ConnectorError(Exception):
    """Raised when the connector cannot reach the panel."""


def _tier_rank(tier: str) -> int:
    return _TIER_RANK.get(tier.strip().lower(), -1)


async def execute_command(  # noqa: PLR0911 — branchy command dispatch; each error reports back
    serve_client: httpx.AsyncClient,
    *,
    serve_url: str,
    serve_token: str | None,
    granted_tier: str,
    verb: str,
    args: dict[str, Any],
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Run one command against local serve. Returns (status, output, error).

    status is 'done' on success or 'error' on any failure (unknown verb, tier breach, missing arg,
    serve error). The connector never raises on a bad command — it reports the error back.
    """
    if verb == "deploy":
        # Push an artifact version to local serve: PUT /api/{collection}/{id} with the content.
        plural = _DEPLOY_PLURAL.get(str(args.get("kind")))
        if plural is None:
            return ("error", None, f"deploy: undeployable kind '{args.get('kind')}'")
        method, path_template, required_tier = ("PUT", f"/api/{plural}/{{id}}", "admin")
    else:
        route = _VERB_ROUTES.get(verb)
        if route is None:
            return ("error", None, f"unknown verb: {verb}")
        method, path_template, required_tier = route
    if _tier_rank(granted_tier) < _tier_rank(required_tier):
        return ("error", None, f"verb '{verb}' exceeds granted tier '{granted_tier}'")
    try:
        path = path_template.format(**args)
    except KeyError as exc:
        return ("error", None, f"missing arg for verb '{verb}': {exc}")

    body = args.get("body")
    json_body = body if isinstance(body, dict) else None
    headers = {"Authorization": f"Bearer {serve_token}"} if serve_token else {}
    url = serve_url.rstrip("/") + path
    try:
        resp = await serve_client.request(method, url, headers=headers, json=json_body)
    except httpx.HTTPError as exc:
        return ("error", None, f"serve call failed: {exc}")
    if resp.status_code >= 400:
        return ("error", None, f"serve returned {resp.status_code}: {resp.text[:200]}")
    try:
        parsed: Any = resp.json()
        output = parsed if isinstance(parsed, dict) else {"result": parsed}
    except ValueError:
        output = {"text": resp.text}
    return ("done", output, None)


async def poll_once(
    panel_client: httpx.AsyncClient,
    serve_client: httpx.AsyncClient,
    *,
    panel_url: str,
    instance_id: str,
    granted_tier: str,
    serve_url: str,
    serve_token: str | None,
    status_body: dict[str, Any],
) -> int:
    """One poll cycle: drain the queue, execute each command, report each result.

    Returns the number of commands handled. Raises ConnectorError if the panel is unreachable.
    """
    base = panel_url.rstrip("/")
    try:
        resp = await panel_client.post(f"{base}/instances/{instance_id}/poll", json=status_body)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"cannot reach panel {base}: {exc}") from exc
    if resp.status_code >= 400:
        raise ConnectorError(f"panel /poll returned {resp.status_code}: {resp.text[:200]}")

    commands: list[dict[str, Any]] = resp.json().get("commands", [])
    for cmd in commands:
        cmd_id = cmd.get("cmd_id")
        verb = cmd.get("verb")
        status: str
        output: dict[str, Any] | None
        error: str | None
        # Never let one bad enqueue kill the poll loop (the "never raises on a bad command"
        # contract): a malformed or failing command reports an error result and moves on.
        if not cmd_id or not verb:
            status, output, error = "error", {}, "malformed command (missing verb/cmd_id)"
        else:
            try:
                status, output, error = await execute_command(
                    serve_client,
                    serve_url=serve_url,
                    serve_token=serve_token,
                    granted_tier=granted_tier,
                    verb=verb,
                    args=cmd.get("args", {}),
                )
            except Exception as exc:
                status, output, error = "error", {}, str(exc)
        if not cmd_id:
            continue
        # At-least-once: if the result POST fails, the panel re-dispatches; next poll retries.
        with contextlib.suppress(httpx.HTTPError):
            await panel_client.post(
                f"{base}/instances/{instance_id}/commands/{cmd_id}/result",
                json={"status": status, "output": output, "error": error},
            )
    return len(commands)


async def _fetch_status_body(
    serve_client: httpx.AsyncClient, *, serve_url: str, serve_token: str | None
) -> dict[str, Any]:
    """Best-effort capability snapshot to fold into the poll (heartbeat + capability refresh)."""
    body: dict[str, Any] = {"status": "ok"}
    headers = {"Authorization": f"Bearer {serve_token}"} if serve_token else {}
    try:
        resp = await serve_client.get(f"{serve_url.rstrip('/')}/capabilities", headers=headers)
        if resp.status_code == 200:
            caps = resp.json()
            body["capabilities"] = caps
            body["schema_version"] = str(caps.get("schema_version", ""))
    except (httpx.HTTPError, ValueError):
        pass
    return body


async def run_connector(
    *,
    panel_url: str,
    instance_id: str,
    panel_token: str | None,
    serve_url: str = "http://127.0.0.1:8000",
    serve_token: str | None = None,
    granted_tier: str = "read",
    interval: float = 5.0,
    once: bool = False,
    log: Any = print,
) -> None:
    """Poll the panel forever (or once), executing queued commands against local serve."""
    panel_headers = {"Authorization": f"Bearer {panel_token}"} if panel_token else {}
    async with (
        httpx.AsyncClient(timeout=40, headers=panel_headers) as panel_client,
        httpx.AsyncClient(timeout=60) as serve_client,
    ):
        while True:
            status_body = await _fetch_status_body(
                serve_client, serve_url=serve_url, serve_token=serve_token
            )
            try:
                handled = await poll_once(
                    panel_client,
                    serve_client,
                    panel_url=panel_url,
                    instance_id=instance_id,
                    granted_tier=granted_tier,
                    serve_url=serve_url,
                    serve_token=serve_token,
                    status_body=status_body,
                )
                if handled:
                    log(f"connector: handled {handled} command(s)")
            except ConnectorError as exc:
                log(f"connector: poll failed — {exc}")
            if once:
                break
            await asyncio.sleep(interval)
