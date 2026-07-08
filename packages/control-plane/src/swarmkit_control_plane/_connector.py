"""Panel → instance connector (Mode A: direct pull over the serve REST API).

Verifies an instance at enrollment, live-queries its jobs, and drives authoring/eval runs on it —
all over the serve REST API with the panel-held token. The HTTP plumbing (token resolution, bearer
header, base URL, transport-error mapping) lives in :class:`ServeClient`; these functions express
only the call sequence + per-endpoint status handling.
See design/details/control-plane/13-connector-registry.md.

``ConnectorError`` and ``resolve_secret_ref`` are re-exported here for the modules that import them
from ``_connector`` (their original home).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from swarmkit_control_plane._serve_client import (
    ConnectorError,
    ServeClient,
    resolve_secret_ref,
)

__all__ = [
    "ConnectorError",
    "fetch_capabilities",
    "fetch_jobs",
    "fetch_state",
    "refresh",
    "register",
    "resolve_secret_ref",
    "run_authoring",
    "run_eval",
]


async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
    """Pull an instance's full observed state (GET /fleet/state) — every artifact's content, not
    just names (fleet enrollment Phase 1, design 19). The panel caches this so the instance stays
    inspectable offline. Returns the parsed InstanceState. Raises ConnectorError on any failure.
    """
    async with ServeClient(endpoint, token_ref) as serve:
        state: dict[str, Any] = serve.ok(await serve.get("/fleet/state"), "/fleet/state")
    return state


async def register(
    endpoint: str, enroll_token: str, fleet_id: str, requested_scope: str | None = None
) -> dict[str, Any]:
    """Register this fleet with an instance (POST /fleet/register) using a one-time enrollment token
    (design 19, Phase 2). The instance issues back a scoped membership credential + its full state
    in one round trip. The enrollment token is the bearer (its own auth). Returns
    ``{membership_id, credential, instance_state}``; raises ConnectorError on any failure.
    """
    body: dict[str, Any] = {"fleet_id": fleet_id}
    if requested_scope:
        body["requested_scope"] = requested_scope
    async with ServeClient(endpoint, enroll_token) as serve:
        result: dict[str, Any] = serve.ok(
            await serve.post("/fleet/register", body), "/fleet/register"
        )
    return result


async def refresh(endpoint: str, membership_key: str) -> dict[str, Any]:
    """Rotate this fleet's membership key on an instance (POST /fleet/refresh) — authenticated with
    the *current* key (design 19, Phase 2). Returns ``{membership_id, credential}`` with the new key
    (the old one stops working); raises ConnectorError on any failure.
    """
    async with ServeClient(endpoint, membership_key) as serve:
        result: dict[str, Any] = serve.ok(await serve.post("/fleet/refresh", {}), "/fleet/refresh")
    return result


async def fetch_capabilities(endpoint: str, token_ref: str) -> dict[str, Any]:
    """Verify reachability + read an instance's capability advertisement.

    Returns the parsed /capabilities body. Raises ConnectorError on any failure.
    """
    async with ServeClient(endpoint, token_ref) as serve:
        health = await serve.get("/health", auth=False)
        if health.status_code != 200:
            raise ConnectorError(f"/health returned {health.status_code}")
        body: dict[str, Any] = serve.ok(await serve.get("/capabilities"), "/capabilities")
    return body


async def fetch_jobs(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
    """Federated live-query of an instance's current jobs (GET /jobs). Mode A only — not stored.

    Returns the parsed /jobs list. Raises ConnectorError on any failure.
    """
    async with ServeClient(endpoint, token_ref) as serve:
        jobs: list[dict[str, Any]] = serve.ok(await serve.get("/jobs"), "/jobs")
    return jobs


async def run_authoring(
    endpoint: str, token_ref: str, topology: str, message: str
) -> dict[str, Any]:
    """Run one authoring turn on an instance's serve: POST /run/{topology}, then poll
    /jobs/{id} until it finishes. Mode A only (the panel drives the authoring swarm on
    a directly-reachable instance). Returns {"reply", "status"}. Raises ConnectorError
    on any transport/auth failure or if the run doesn't complete in time."""
    async with ServeClient(endpoint, token_ref, timeout=15) as serve:
        started = await serve.post(f"/run/{topology}", {"input": message})
        if started.status_code in (401, 403):
            raise ConnectorError(f"/run auth failed ({started.status_code}) — check the token")
        if started.status_code == 404:
            raise ConnectorError(f"topology '{topology}' not found on the instance")
        if started.status_code not in (200, 201):
            raise ConnectorError(f"/run returned {started.status_code}")
        job_id = started.json().get("job_id")
        if not job_id:
            raise ConnectorError("/run did not return a job_id")
        # Poll the job to completion. Bounded so a stuck run can't hang the panel.
        for _ in range(90):  # ~180s at 2s/poll
            await asyncio.sleep(2)
            jr = await serve.get(f"/jobs/{job_id}")
            if jr.status_code != 200:
                raise ConnectorError(f"/jobs/{job_id} returned {jr.status_code}")
            job = jr.json()
            status = job.get("status")
            if status == "completed":
                return {"reply": job.get("output") or "", "status": "completed"}
            if status == "failed":
                raise ConnectorError(f"authoring run failed: {job.get('error') or 'unknown'}")
    raise ConnectorError("authoring run did not complete in time")


async def run_eval(  # noqa: PLR0911 — each branch reports a distinct eval status
    endpoint: str, token_ref: str, eval_topology: str, payload: str
) -> dict[str, Any]:
    """Run an eval topology on an instance's serve to test a drafted artifact (the
    growth loop's 'test' stage, design 17). Returns a summary dict parsed from the eval
    topology's output — ``{passed, total, pass_rate}`` when the output is a JSON eval
    result, else ``{"status": ...}``. Never raises: a failed/absent eval must not block
    the human from seeing the proposal, so failures return a status rather than throw."""
    try:
        async with ServeClient(endpoint, token_ref, timeout=15) as serve:
            started = await serve.post(f"/run/{eval_topology}", {"input": payload})
            if started.status_code == 404:
                return {"status": "no-eval-topology", "eval_topology": eval_topology}
            if started.status_code not in (200, 201):
                return {"status": f"run-error-{started.status_code}"}
            job_id = started.json().get("job_id")
            if not job_id:
                return {"status": "no-job-id"}
            for _ in range(90):
                await asyncio.sleep(2)
                jr = await serve.get(f"/jobs/{job_id}")
                if jr.status_code != 200:
                    return {"status": f"poll-error-{jr.status_code}"}
                job = jr.json()
                if job.get("status") == "completed":
                    return _parse_eval(job.get("output") or "")
                if job.get("status") == "failed":
                    return {"status": "failed", "error": job.get("error") or "unknown"}
    except ConnectorError as exc:
        return {"status": "unreachable", "error": str(exc)}
    return {"status": "timeout"}


def _parse_eval(output: str) -> dict[str, Any]:
    """Best-effort parse of an eval topology's output into {passed, total, pass_rate}."""
    text = (output or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            obj = None
        if isinstance(obj, dict) and "passed" in obj and "total" in obj:
            passed, total = int(obj["passed"]), int(obj["total"])
            return {
                "passed": passed,
                "total": total,
                "pass_rate": round(passed / total, 4) if total else None,
                "status": "completed",
            }
    return {"status": "unparsed", "raw": text[:200]}
