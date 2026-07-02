"""Panel → instance connector (Mode A: direct pull over the serve REST API).

Verifies an instance at enrollment by calling its `/health` + `/capabilities` with the
panel-held token. See design/details/control-plane/13-connector-registry.md.
"""

from __future__ import annotations

import asyncio
import json
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


async def run_authoring(
    endpoint: str, token_ref: str, topology: str, message: str
) -> dict[str, Any]:
    """Run one authoring turn on an instance's serve: POST /run/{topology}, then poll
    /jobs/{id} until it finishes. Mode A only (the panel drives the authoring swarm on
    a directly-reachable instance). Returns {"reply", "status"}. Raises ConnectorError
    on any transport/auth failure or if the run doesn't complete in time."""
    base = endpoint.rstrip("/")
    token = resolve_token(token_ref)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            started = await client.post(
                f"{base}/run/{topology}", json={"input": message}, headers=headers
            )
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
                jr = await client.get(f"{base}/jobs/{job_id}", headers=headers)
                if jr.status_code != 200:
                    raise ConnectorError(f"/jobs/{job_id} returned {jr.status_code}")
                job = jr.json()
                status = job.get("status")
                if status == "completed":
                    return {"reply": job.get("output") or "", "status": "completed"}
                if status == "failed":
                    raise ConnectorError(f"authoring run failed: {job.get('error') or 'unknown'}")
    except httpx.HTTPError as exc:
        raise ConnectorError(f"cannot reach {base}: {exc}") from exc
    raise ConnectorError("authoring run did not complete in time")


async def run_eval(  # noqa: PLR0911
    endpoint: str, token_ref: str, eval_topology: str, payload: str
) -> dict[str, Any]:
    """Run an eval topology on an instance's serve to test a drafted artifact (the
    growth loop's 'test' stage, design 17). Returns a summary dict parsed from the eval
    topology's output — ``{passed, total, pass_rate}`` when the output is a JSON eval
    result, else ``{"status": ...}``. Never raises: a failed/absent eval must not block
    the human from seeing the proposal, so failures return a status rather than throw."""
    base = endpoint.rstrip("/")
    token = resolve_token(token_ref)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            started = await client.post(
                f"{base}/run/{eval_topology}", json={"input": payload}, headers=headers
            )
            if started.status_code == 404:
                return {"status": "no-eval-topology", "eval_topology": eval_topology}
            if started.status_code not in (200, 201):
                return {"status": f"run-error-{started.status_code}"}
            job_id = started.json().get("job_id")
            if not job_id:
                return {"status": "no-job-id"}
            for _ in range(90):
                await asyncio.sleep(2)
                jr = await client.get(f"{base}/jobs/{job_id}", headers=headers)
                if jr.status_code != 200:
                    return {"status": f"poll-error-{jr.status_code}"}
                job = jr.json()
                if job.get("status") == "completed":
                    return _parse_eval(job.get("output") or "")
                if job.get("status") == "failed":
                    return {"status": "failed", "error": job.get("error") or "unknown"}
    except httpx.HTTPError as exc:
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
