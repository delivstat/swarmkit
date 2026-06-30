"""Panel → instance connector (Mode A: direct pull over the serve REST API).

Verifies an instance at enrollment by calling its `/health` + `/capabilities` with the
panel-held token. See design/details/control-plane/13-connector-registry.md.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


class ConnectorError(Exception):
    """Raised when the panel cannot reach or verify an instance."""


def resolve_token(token_ref: str) -> str | None:
    """Resolve a token reference (env:/file:/literal) — never a stored literal secret."""
    if not token_ref:
        return None
    if token_ref.startswith("env:"):
        return os.environ.get(token_ref[4:])
    if token_ref.startswith("file:"):
        try:
            return Path(token_ref[5:]).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return token_ref


async def fetch_capabilities(endpoint: str, token_ref: str) -> dict[str, Any]:
    """Verify reachability + read an instance's capability advertisement.

    Returns the parsed /capabilities body. Raises ConnectorError on any failure.
    """
    base = endpoint.rstrip("/")
    token = resolve_token(token_ref)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            health = await client.get(f"{base}/health")
            if health.status_code != 200:
                raise ConnectorError(f"/health returned {health.status_code}")
            caps = await client.get(f"{base}/capabilities", headers=headers)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"cannot reach {base}: {exc}") from exc
    if caps.status_code in (401, 403):
        raise ConnectorError(f"/capabilities auth failed ({caps.status_code}) — check the token")
    if caps.status_code != 200:
        raise ConnectorError(f"/capabilities returned {caps.status_code}")
    body: dict[str, Any] = caps.json()
    return body


async def fetch_jobs(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
    """Federated live-query of an instance's current jobs (GET /jobs). Mode A only — not stored.

    Returns the parsed /jobs list. Raises ConnectorError on any failure.
    """
    base = endpoint.rstrip("/")
    token = resolve_token(token_ref)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base}/jobs", headers=headers)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"cannot reach {base}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise ConnectorError(f"/jobs auth failed ({resp.status_code}) — check the token")
    if resp.status_code != 200:
        raise ConnectorError(f"/jobs returned {resp.status_code}")
    jobs: list[dict[str, Any]] = resp.json()
    return jobs
