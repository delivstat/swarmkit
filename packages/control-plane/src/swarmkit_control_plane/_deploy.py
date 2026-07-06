"""Governed deploy — push a registry artifact version onto an instance's serve (design §15 sync).

Deploying an artifact is a **legislative** change (`topologies:modify` is reserved-for-human), so
the panel only deploys what a human has already published (an approved registry version) and the
action is operator-gated + audited. For a direct (Mode A) instance the panel pushes to serve `/api`;
for a poll (Mode B) instance the panel enqueues a ``deploy`` command the connector applies locally.

Only kinds with a serve ``/api`` write route are deployable today.
"""

from __future__ import annotations

from typing import Any

from swarmkit_control_plane._serve_client import ConnectorError, ServeClient

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
    try:
        async with ServeClient(endpoint, token_ref, timeout=30) as serve:
            resp = await serve.put(f"/api/{plural}/{artifact_id}", content)
    except ConnectorError as exc:  # transport failure — surface as a deploy failure
        raise DeployError(str(exc)) from exc
    if resp.status_code in (401, 403):
        raise DeployError(f"deploy unauthorized ({resp.status_code}) — needs a serve:admin token")
    if resp.status_code >= 400:
        raise DeployError(f"serve returned {resp.status_code}: {resp.text[:200]}")
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {"text": resp.text}
    return body
