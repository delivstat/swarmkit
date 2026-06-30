"""Governed deploy — push a registry artifact version onto an instance's serve (design §15 sync).

Deploying an artifact is a **legislative** change (`topologies:modify` is reserved-for-human), so
the panel only deploys what a human has already published (an approved registry version) and the
action is operator-gated + audited. For a direct (Mode A) instance the panel pushes to serve `/api`;
for a poll (Mode B) instance the panel enqueues a ``deploy`` command the connector applies locally.

Only kinds with a serve ``/api`` write route are deployable today.
"""

from __future__ import annotations

from typing import Any

import httpx

from swarmkit_control_plane._connector import resolve_token

# artifact kind → serve /api collection
DEPLOYABLE: dict[str, str] = {
    "topology": "topologies",
    "skill": "skills",
    "archetype": "archetypes",
}


class DeployError(Exception):
    """Raised when a Mode A push to an instance fails."""


async def push_artifact(
    endpoint: str, token_ref: str, kind: str, artifact_id: str, content: Any
) -> dict[str, Any]:
    """Mode A: PUT the artifact content to the instance's serve /api collection."""
    plural = DEPLOYABLE[kind]
    base = endpoint.rstrip("/")
    token = resolve_token(token_ref)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                f"{base}/api/{plural}/{artifact_id}", json=content, headers=headers
            )
    except httpx.HTTPError as exc:
        raise DeployError(f"cannot reach {base}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise DeployError(f"deploy unauthorized ({resp.status_code}) — needs a serve:admin token")
    if resp.status_code >= 400:
        raise DeployError(f"serve returned {resp.status_code}: {resp.text[:200]}")
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {"text": resp.text}
    return body
