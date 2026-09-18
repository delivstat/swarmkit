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
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from swarmkit_control_plane._serve_client import (
    ConnectorError,
    ManifestUnsupported,
    ServeClient,
    resolve_secret_ref,
)

__all__ = [
    "ConnectorError",
    "ManifestUnsupported",
    "fetch_artifacts",
    "fetch_canary",
    "fetch_capabilities",
    "fetch_jobs",
    "fetch_manifest",
    "fetch_run_trace",
    "fetch_runs",
    "fetch_state",
    "fetch_usage",
    "leave",
    "promote_canary",
    "refresh",
    "register",
    "resolve_secret_ref",
    "rollback_canary",
    "run_authoring",
    "run_eval",
    "start_canary",
]


async def fetch_state(endpoint: str, token_ref: str) -> dict[str, Any]:
    """Pull an instance's full observed state (GET /fleet/state) — every artifact's content, not
    just names (fleet enrollment Phase 1, design 19). The panel caches this so the instance stays
    inspectable offline. Returns the parsed InstanceState. Raises ConnectorError on any failure.
    """
    async with ServeClient(endpoint, token_ref) as serve:
        state: dict[str, Any] = serve.ok(await serve.get("/fleet/state"), "/fleet/state")
    return state


async def fetch_manifest(endpoint: str, token_ref: str) -> dict[str, Any]:
    """Pull the names-only state manifest (GET /fleet/state/manifest) — id/version/content_hash per
    artifact, no content (design 19 §delta sync). The panel diffs these hashes against its cache to
    learn what changed. Raises ``ManifestUnsupported`` when the instance predates delta sync (404),
    so the caller falls back to a full pull; other failures raise ``ConnectorError``.
    """
    async with ServeClient(endpoint, token_ref) as serve:
        resp = await serve.get("/fleet/state/manifest")
        if resp.status_code == 404:
            raise ManifestUnsupported(
                "instance has no /fleet/state/manifest (pre-delta-sync serve)"
            )
        manifest: dict[str, Any] = serve.ok(resp, "/fleet/state/manifest")
    return manifest


async def fetch_artifacts(
    endpoint: str, token_ref: str, refs: list[tuple[str, str]]
) -> dict[str, Any]:
    """Fetch the *content* of specific artifacts (POST /fleet/state/artifacts) — the body-fetch half
    of delta sync. ``refs`` is ``(collection, id)`` pairs. Returns an InstanceState carrying only
    those artifacts. Raises ConnectorError on any failure.
    """
    body = {
        "refs": [{"collection": collection, "id": artifact_id} for collection, artifact_id in refs]
    }
    async with ServeClient(endpoint, token_ref) as serve:
        result: dict[str, Any] = serve.ok(
            await serve.post("/fleet/state/artifacts", body), "/fleet/state/artifacts"
        )
    return result


async def register(
    endpoint: str,
    enroll_token: str,
    fleet_id: str,
    requested_scope: str | None = None,
    *,
    fleet_public_key: str | None = None,
    proof: str | None = None,
    target_workspace_id: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Register this fleet with an instance (POST /fleet/register) using a one-time enrollment token
    (design 19, Phase 2). The instance issues back a scoped membership credential + its full state
    in one round trip. The enrollment token is the bearer (its own auth). When *fleet_public_key* +
    *proof* are supplied, the fleet also proves its identity (design 21) so the instance pins its
    key. Returns ``{membership_id, credential, instance_state}``; raises ConnectorError on failure.
    """
    body: dict[str, Any] = {"fleet_id": fleet_id}
    if requested_scope:
        body["requested_scope"] = requested_scope
    if fleet_public_key:
        body["fleet_public_key"] = fleet_public_key
        body["proof"] = proof or ""
        body["target_workspace_id"] = target_workspace_id or ""
        if display_name:
            body["display_name"] = display_name
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


async def leave(endpoint: str, membership_key: str, membership_id: str) -> dict[str, Any]:
    """Leave a fleet: revoke this fleet's own membership on an instance (DELETE
    /fleet/membership/{id}), authenticated with the *membership key itself* (self-leave, design 19).
    The instance stops accepting the key. Returns the serve response; raises ConnectorError on any
    failure (including a 404 when the membership is already gone)."""
    async with ServeClient(endpoint, membership_key) as serve:
        result: dict[str, Any] = serve.ok(
            await serve.delete(f"/fleet/membership/{membership_id}"), "/fleet/membership"
        )
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


async def fetch_usage(endpoint: str, token_ref: str) -> dict[str, Any]:
    """Pull an instance's usage rollup (GET /usage) — cumulative token/cost totals grouped by
    model (design 23). Folded into /sync so the fleet Runs page reflects Mode-A instances without
    requiring the observability push pipeline. Returns {"summary", "by_model"}. Raises
    ConnectorError on any failure.
    """
    async with ServeClient(endpoint, token_ref) as serve:
        usage: dict[str, Any] = serve.ok(await serve.get("/usage"), "/usage")
    return usage


async def fetch_artifact_yaml(
    endpoint: str, token_ref: str, plural: str, artifact_id: str
) -> str | None:
    """The file text of one artifact (GET /api/{plural}/{id}/yaml), or None when the instance has
    no such route or artifact. Adopt uses it when the cached state entry carries no text — a cache
    written before the text travelled — so a later deploy still writes the file verbatim."""
    path = f"/api/{plural}/{artifact_id}/yaml"
    async with ServeClient(endpoint, token_ref) as serve:
        resp = await serve.get(path)
        if resp.status_code != 200:
            return None
        body = resp.json()
    text = body.get("yaml") if isinstance(body, dict) else None
    return text if isinstance(text, str) and text else None


async def fetch_gaps(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
    """The instance's skill gap log (GET /gaps) — what `swarmkit gaps` prints there. Pulled on
    sync and folded into the fleet gap rollup (design 27). Raises ConnectorError."""
    async with ServeClient(endpoint, token_ref) as serve:
        gaps: list[dict[str, Any]] = serve.ok(await serve.get("/gaps"), "/gaps")
    return gaps


#: How many audit events one sync pulls. A long-idle instance catches up over several syncs.
AUDIT_PULL_LIMIT = 500


async def fetch_audit(endpoint: str, token_ref: str, since: str | None) -> list[dict[str, Any]]:
    """The instance's audit events after *since* (GET /audit?since=…), newest first. Pulled on
    sync with a per-instance cursor so each event travels once (design 27). Raises
    ConnectorError."""
    # The cursor is an ISO timestamp with an offset; its `+` must be encoded or it arrives as a
    # space and the instance answers 422 (found on the second sync of the very first live run).
    query = {"limit": str(AUDIT_PULL_LIMIT), **({"since": since} if since else {})}
    path = f"/audit?{urlencode(query)}"
    async with ServeClient(endpoint, token_ref) as serve:
        events: list[dict[str, Any]] = serve.ok(await serve.get(path), "/audit")
    return events


async def fetch_runs(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
    """Federated live-query of an instance's *completed* run history (GET /jobs/history) — per-run
    cost/token/status detail, pulled on demand and **not stored** (design 24). This is the "details"
    half of the two-lane model: aggregates are pushed, granular per-run history stays on the owner's
    instance and is fetched only when viewed. Returns the parsed /jobs/history list. Raises
    ConnectorError on any failure.
    """
    async with ServeClient(endpoint, token_ref) as serve:
        runs: list[dict[str, Any]] = serve.ok(await serve.get("/jobs/history"), "/jobs/history")
    return runs


async def fetch_run_trace(endpoint: str, token_ref: str, run_id: str) -> dict[str, Any] | None:
    """Federated live-query of a finished run's span tree (GET /observability/runs/<id>/trace) — the
    "details" lane (design 24), pulled on demand and **not stored**. Returns the span-tree dict, or
    ``None`` when the instance has no trace for that run (a real 404, not a failure). Raises
    ConnectorError on any connection/auth failure so the caller can report the instance offline."""
    path = f"/observability/runs/{run_id}/trace"
    async with ServeClient(endpoint, token_ref) as serve:
        resp = await serve.get(path)
        if resp.status_code == 404:
            return None
        trace: dict[str, Any] = serve.ok(resp, path)
    return trace


async def fetch_canary(endpoint: str, token_ref: str) -> dict[str, Any]:
    """Federated read of an instance's canary status (GET /canary) — per-version weights + metrics
    (design 26). Live-queried, not stored. Returns ``{"enabled", "routes"}``. Raises ConnectorError
    on any failure."""
    async with ServeClient(endpoint, token_ref) as serve:
        canary: dict[str, Any] = serve.ok(await serve.get("/canary"), "/canary")
    return canary


async def promote_canary(
    endpoint: str, token_ref: str, topology: str, version: str
) -> dict[str, Any]:
    """Promote a canary version to 100% on an instance (POST /canary/{topology}/promote). A
    manage-scope fleet action (design 26). Raises ConnectorError on any failure."""
    async with ServeClient(endpoint, token_ref) as serve:
        result: dict[str, Any] = serve.ok(
            await serve.post(f"/canary/{topology}/promote", {"version": version}),
            f"/canary/{topology}/promote",
        )
    return result


async def rollback_canary(endpoint: str, token_ref: str, topology: str) -> dict[str, Any]:
    """Roll a canary back to its base version on an instance (POST /canary/{topology}/rollback).
    A manage-scope fleet action (design 26). Raises ConnectorError on any failure."""
    async with ServeClient(endpoint, token_ref) as serve:
        result: dict[str, Any] = serve.ok(
            await serve.post(f"/canary/{topology}/rollback", {}),
            f"/canary/{topology}/rollback",
        )
    return result


async def start_canary(
    endpoint: str,
    token_ref: str,
    topology: str,
    base_version: str,
    canary_version: str,
    weight: int,
    promote_when: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a canary on an instance (POST /canary/{topology}, design 26 Layer B): split *weight*%
    of traffic to *canary_version*. The canary version's artifact must already be deployed to the
    instance. A manage-scope fleet action. Raises ConnectorError on any failure."""
    body: dict[str, Any] = {
        "base_version": base_version,
        "canary_version": canary_version,
        "weight": weight,
    }
    if promote_when:
        body["promote_when"] = promote_when
    async with ServeClient(endpoint, token_ref) as serve:
        result: dict[str, Any] = serve.ok(
            await serve.post(f"/canary/{topology}", body), f"/canary/{topology}"
        )
    return result


# Job statuses after which polling is pointless: the run will not complete on its own. `stopped`
# is an operator's stop, `interrupted` a restart sweep over a run a dead process left running.
_TERMINAL_FAILURES = frozenset({"failed", "stopped", "interrupted"})


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
            if status == "deferred":
                # Parked on a human gate on the instance: not a failure, and not ours to wait
                # out — the gate is resolved on the instance and the run resumes there.
                return {"reply": job.get("error") or "", "status": "deferred", "job_id": job_id}
            if status in _TERMINAL_FAILURES:
                raise ConnectorError(f"authoring run {status}: {job.get('error') or 'unknown'}")
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
                status = job.get("status")
                if status == "completed":
                    return _parse_eval(job.get("output") or "")
                if status == "deferred" or status in _TERMINAL_FAILURES:
                    return {"status": status, "error": job.get("error") or "unknown"}
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


async def fetch_gates(endpoint: str, token_ref: str) -> list[dict[str, Any]]:
    """Federated read of an instance's pending harness gates (GET /review) — §6.2 permission and
    §6.3 input requests awaiting a human. Live-queried, not stored. Raises ConnectorError."""
    async with ServeClient(endpoint, token_ref) as serve:
        gates: list[dict[str, Any]] = serve.ok(await serve.get("/review"), "/review")
    return gates


@dataclass(frozen=True)
class ActorAssertion:
    """Who is resolving, vouched for by the fleet identity (design 28): the panel's fleet id, the
    operator's OIDC subject, the unix-seconds issue time and the signature over all three."""

    fleet_id: str
    subject: str
    issued_at: int
    signature: str

    def headers(self) -> dict[str, str]:
        return {
            "X-Fleet-Id": self.fleet_id,
            "X-Fleet-Actor": self.subject,
            "X-Fleet-Actor-Issued": str(self.issued_at),
            "X-Fleet-Actor-Signature": self.signature,
        }


class GateRefused(ConnectorError):
    """The instance answered the resolution with a 4xx of its own — the caller is not a member of
    the role, the item is not pending, the verb does not fit the kind. The instance was reached;
    its reason is the message, and it belongs to the human, not to the health monitor."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


async def resolve_gate(
    endpoint: str,
    token_ref: str,
    item_id: str,
    action: str,
    answer: str = "",
    *,
    outcome: str = "",
    comment: str = "",
    actor: ActorAssertion | None = None,
) -> dict[str, Any]:
    """Proxy a human decision to the instance's review queue (POST /review/{id}/{action}), where
    action is approve | reject | answer for a harness gate, or resolve for a multi-party
    role-task (``outcome`` approve | changes-requested | reject). A resolution counts against the
    panel's enrolment identity on the instance — unless *actor* carries a signed assertion of the
    signed-in operator, which an ``approve-as`` instance counts instead (design 28). Returns the
    updated item. Raises GateRefused when the instance declined the decision, ConnectorError when
    it could not be reached."""
    body: dict[str, Any]
    if action == "answer":
        body = {"answer": answer}
    elif action == "resolve":
        body = {"outcome": outcome or "approve", "comment": comment}
    else:
        body = {"comment": comment} if comment else {}
    path = f"/review/{item_id}/{action}"
    async with ServeClient(endpoint, token_ref) as serve:
        resp = await serve.post(path, body, headers=actor.headers() if actor else None)
        # 401 is the panel's token; every other 4xx here is the instance's verdict on the decision —
        # including 403, which is how it says the panel's identity is not a member of the role.
        if 400 <= resp.status_code < 500 and resp.status_code != 401:
            try:
                detail = str(resp.json().get("detail", resp.text[:200]))
            except ValueError:
                detail = resp.text[:200]
            raise GateRefused(resp.status_code, detail)
        result: dict[str, Any] = serve.ok(resp, path)
    return result
