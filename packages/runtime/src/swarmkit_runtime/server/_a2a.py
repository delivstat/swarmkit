"""A2A (Agent2Agent) server — an Agent Card and the task API as a *transport onto jobs*.

``design/details/a2a-interop.md``. Every topology `swarmkit serve` hosts is advertised as an A2A
skill on the instance's Agent Card, and the A2A task API is answered by mapping each method onto
the job model that ``POST /run`` already uses — same job store, same gates, same audit trail. There
is no second execution path here: ``message/send`` *is* a run submission, ``tasks/get`` *is*
``GET /jobs/{id}``, ``tasks/cancel`` *is* ``POST /jobs/{id}/stop``.

The one place the mapping is not mechanical is a run parked on a human gate. A2A calls that
``input-required``, which to a client means "you can supply what is needed". Here the human is not
the caller: approval scopes are structurally un-grantable to agents (§8.7), and an A2A client is an
agent. So the task reports ``input-required`` with the gate named in its status message, and a
follow-up message on that task is refused — the human resolves it through the review queue, the
task then goes ``working`` again, and a subscribed client sees it. The gate cannot be talked past
over a new transport, which is the whole reason A2A rides on the job model rather than beside it.

Wire shapes follow the JSON-RPC binding the reference SDKs speak (``kind: "task"`` / ``"message"``,
lower-case hyphenated states, ``parts`` with a ``kind``). Push notifications, gRPC and card
signing are deliberately not implemented; they answer ``UnsupportedOperationError``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

# ---- A2A wire constants ----------------------------------------------------------------------

#: JSON-RPC 2.0 standard codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
#: A2A-defined codes (specification §"Error handling").
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
UNSUPPORTED_OPERATION = -32004
CONTENT_TYPE_NOT_SUPPORTED = -32005

#: The well-known discovery path (RFC 8615), as the A2A spec recommends.
WELL_KNOWN_PATH = "/.well-known/agent-card.json"

#: `jobs.source` for a run that arrived over A2A — the same column that says `serve` / `cli` /
#: `chat`, so the portal's Source field and `tasks/list` read one thing. The task id IS the job id.
SOURCE = "a2a"
#: The A2A `contextId` rides on `jobs.correlation_id` (the generic "same ticket" grouping); the
#: label keeps the exact string a client sent, since a correlation id may be set by other callers.
LABEL_CONTEXT = "a2a.context_id"

#: Job status -> A2A task state. Exhaustive over `Job.status`'s Literal; a test holds it so.
JOB_TO_TASK_STATE: dict[str, str] = {
    "pending": "submitted",
    "running": "working",
    "deferred": "input-required",
    "completed": "completed",
    "failed": "failed",
    "stopped": "canceled",
    # A run a dead process left `running`, swept on restart: it did not finish and nobody chose to
    # stop it — that is a failure from the caller's side, not a cancellation.
    "interrupted": "failed",
}

TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})


class A2AError(Exception):
    """A JSON-RPC error the handler turns into an error response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# ---- the Agent Card ---------------------------------------------------------------------------


def build_agent_card(
    rt: Any,
    *,
    base_url: str,
    auth_provider: str,
    identity: dict[str, Any] | None = None,
    only_topology: str | None = None,
) -> dict[str, Any]:
    """The instance's Agent Card: one A2A skill per topology (or one, for the per-topology card).

    `securitySchemes` is derived from the auth provider serve is *actually* running — the card says
    what the server already enforces; it never widens it. `none` advertises no scheme, and the
    key/JWT providers both present as a bearer token to a client.
    """
    from swarmkit_runtime._versions import runtime_version  # noqa: PLC0415

    ws = rt.workspace
    topologies = sorted(ws.topologies.keys())
    if only_topology is not None:
        topologies = [t for t in topologies if t == only_topology]
    skills = []
    for name in topologies:
        topo = ws.topologies[name]
        meta = getattr(topo, "metadata", None)
        description = (
            str(getattr(meta, "description", "") or "").strip() or f"Run the {name} topology."
        )
        skills.append(
            {
                "id": name,
                "name": str(getattr(meta, "name", "") or name),
                "description": description,
                "tags": ["swarmkit", "topology"],
                "inputModes": ["text/plain", "application/json"],
                "outputModes": ["text/plain", "application/json"],
            }
        )
    workspace_name = str(getattr(ws.raw.metadata, "name", "") or ws.raw.metadata.id)
    ident = dict(identity or {})
    # `server.a2a.identity.url` is the address *other agents* should call — set behind a proxy,
    # where the request's own host is the wrong answer. It replaces the derived base URL.
    root = str(ident.get("url") or base_url).rstrip("/")
    agent_name = str(ident.get("name") or workspace_name)
    card: dict[str, Any] = {
        "name": agent_name if only_topology is None else f"{only_topology} — {agent_name}",
        "description": (
            str(ident.get("description") or "")
            or "SwarmKit serve instance; each skill is a governed topology run."
            if only_topology is None
            else f"SwarmKit topology {only_topology}, run as a governed job."
        ),
        "url": f"{root}/a2a" if only_topology is None else f"{root}/a2a/{only_topology}",
        "version": runtime_version(),
        "protocolVersion": "0.3.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": skills,
    }
    if ident.get("organization"):
        card["provider"] = {"organization": str(ident["organization"]), "url": root}
    if auth_provider in ("api_key", "jwt"):
        card["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
        card["security"] = [{"bearer": []}]
    return card


# ---- task <-> job -----------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text_parts(text: str | None) -> list[dict[str, Any]]:
    return [{"kind": "text", "text": text}] if text else []


def _labels(job: Any) -> dict[str, str]:
    labels = getattr(job, "labels", None)
    return dict(labels) if isinstance(labels, dict) else {}


def task_from_job(job: Any, *, gate_url: str | None = None) -> dict[str, Any]:
    """The A2A Task for a job, from whichever view of it the caller has (live, durable, merged).

    `deferred` becomes `input-required` with a status message that names what the run is waiting
    for; the message is informative, not an invitation — see `send_message` for the refusal.
    """
    status = str(getattr(job, "status", "") or "")
    state = JOB_TO_TASK_STATE.get(status, "unknown")
    labels = _labels(job)
    task: dict[str, Any] = {
        "id": job.id,
        "contextId": labels.get(LABEL_CONTEXT) or getattr(job, "correlation_id", None) or job.id,
        "kind": "task",
        "status": {"state": state, "timestamp": getattr(job, "completed_at", None) or _now()},
        "metadata": {"swarmkit": {"topology": job.topology, "job_status": status}},
    }
    if state == "input-required":
        text = (
            "This run is waiting on a human decision that an A2A caller cannot supply "
            "(SwarmKit approval scopes are not grantable to agents). It resumes when a person "
            "resolves the gate; subscribe to be told."
        )
        if gate_url:
            text += f" Gate: {gate_url}"
            task["metadata"]["swarmkit"]["gate_url"] = gate_url
        task["status"]["message"] = {
            "kind": "message",
            "role": "agent",
            "messageId": uuid.uuid4().hex,
            "taskId": job.id,
            "parts": _text_parts(text),
        }
    elif state == "failed":
        err = getattr(job, "error", None)
        if err:
            task["status"]["message"] = {
                "kind": "message",
                "role": "agent",
                "messageId": uuid.uuid4().hex,
                "taskId": job.id,
                "parts": _text_parts(str(err)),
            }
    output = getattr(job, "output", None)
    if state == "completed" and output is not None:
        task["artifacts"] = [
            {
                "artifactId": f"{job.id}-output",
                "name": "output",
                "parts": _artifact_parts(output),
            }
        ]
    return task


def _artifact_parts(output: Any) -> list[dict[str, Any]]:
    """The run's output as A2A parts — a `data` part when it is JSON, a `text` part otherwise."""
    if isinstance(output, dict | list):
        return [{"kind": "data", "data": output}]
    text = str(output)
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return [{"kind": "data", "data": json.loads(stripped)}]
        except ValueError:
            pass
    return [{"kind": "text", "text": text}]


def message_text(message: dict[str, Any]) -> str:
    """The user's text, from the message's text parts; `data` parts are carried as JSON text."""
    chunks: list[str] = []
    for part in message.get("parts") or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind")
        if kind == "text" and part.get("text"):
            chunks.append(str(part["text"]))
        elif kind == "data" and part.get("data") is not None:
            chunks.append(json.dumps(part["data"]))
    return "\n".join(chunks)


def message_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    """File parts as run attachments — the same shape `POST /run` takes, so the same rules apply
    (content-sniffed type, size ceiling, images only today). A `uri` file is refused: the runtime
    does not fetch caller-supplied addresses (attachments.md)."""
    out: list[dict[str, Any]] = []
    for part in message.get("parts") or []:
        if not isinstance(part, dict) or part.get("kind") != "file":
            continue
        file = part.get("file") or {}
        if file.get("bytes"):
            entry: dict[str, Any] = {"data": file["bytes"]}
            if file.get("name"):
                entry["name"] = file["name"]
            out.append(entry)
        elif file.get("uri"):
            raise A2AError(
                CONTENT_TYPE_NOT_SUPPORTED,
                "file parts by URI are not accepted; send the bytes (the runtime does not fetch "
                "caller-supplied addresses)",
            )
    return out


# ---- JSON-RPC handling ------------------------------------------------------------------------


def _rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def parse_rpc(body: Any) -> tuple[Any, str, dict[str, Any]]:
    """Validate a JSON-RPC 2.0 request envelope; returns (id, method, params)."""
    if not isinstance(body, dict):
        raise A2AError(INVALID_REQUEST, "request must be a JSON object")
    if body.get("jsonrpc") != "2.0":
        raise A2AError(INVALID_REQUEST, "jsonrpc must be '2.0'")
    method = body.get("method")
    if not isinstance(method, str) or not method:
        raise A2AError(INVALID_REQUEST, "method is required")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise A2AError(INVALID_PARAMS, "params must be an object")
    return body.get("id"), method, params


UNSUPPORTED_METHODS = frozenset(
    {
        "tasks/pushNotificationConfig/set",
        "tasks/pushNotificationConfig/get",
        "tasks/pushNotificationConfig/list",
        "tasks/pushNotificationConfig/delete",
        "tasks/pushNotificationConfig/create",
    }
)


class A2AHandler:
    """The A2A methods, each a thin mapping onto the job service — no logic of its own.

    Constructed per request with the pieces the jobs routes already have (runtime, job service,
    store, request deps); `handle` dispatches one JSON-RPC call and returns the response envelope.
    Streaming methods return an async generator of SSE frames instead.
    """

    def __init__(
        self,
        *,
        rt: Any,
        jobs: Any,
        job_store: Any,
        store: Any,
        cfg: Any,
        canary: Any,
        semaphore: Any,
        gate_url_for: Any,
        topology: str | None = None,
        actor: str = "a2a",
    ) -> None:
        self.rt = rt
        self.jobs = jobs
        self.job_store = job_store
        self.store = store
        self.cfg = cfg
        self.canary = canary
        self.semaphore = semaphore
        self.gate_url_for = gate_url_for
        self.topology = topology
        self.actor = actor

    # -- lookups --

    async def _job_view(self, task_id: str) -> Any:
        from ._routes_jobs import _resolve_job  # noqa: PLC0415

        live = await self.job_store.get(task_id)
        row = self.store.get_job(task_id) if self.store is not None else None
        found = _resolve_job(live, row)
        if found is None:
            raise A2AError(TASK_NOT_FOUND, f"task {task_id!r} not found")
        return found

    def _task(self, job: Any) -> dict[str, Any]:
        gate_url = self.gate_url_for(job) if str(getattr(job, "status", "")) == "deferred" else None
        return task_from_job(job, gate_url=gate_url)

    def _skill_topology(self, params: dict[str, Any], message: dict[str, Any]) -> str:
        """Which topology a message targets: the per-topology endpoint, else the message's
        `metadata.skill` / `metadata.swarmkit.topology`, else a lone topology in the workspace."""
        if self.topology:
            return self.topology
        meta = message.get("metadata") or {}
        skill = meta.get("skill") or (meta.get("swarmkit") or {}).get("topology")
        if skill:
            return str(skill)
        names = sorted(str(n) for n in self.rt.workspace.topologies)
        if len(names) == 1:
            return names[0]
        raise A2AError(
            INVALID_PARAMS,
            "which topology? set message.metadata.skill to one of the card's skill ids, or call "
            "the per-topology endpoint /a2a/{topology}",
        )

    # -- methods --

    async def handle(self, body: Any) -> dict[str, Any]:
        try:
            req_id, method, params = parse_rpc(body)
        except A2AError as exc:
            return rpc_error(None, exc.code, exc.message, exc.data)
        try:
            if method == "message/send":
                return _rpc_result(req_id, await self.send_message(params))
            if method == "tasks/get":
                return _rpc_result(req_id, await self.get_task(params))
            if method == "tasks/list":
                return _rpc_result(req_id, await self.list_tasks(params))
            if method == "tasks/cancel":
                return _rpc_result(req_id, await self.cancel_task(params))
            if method in ("agent/getAuthenticatedExtendedCard", "agent/getExtendedAgentCard"):
                raise A2AError(
                    UNSUPPORTED_OPERATION, "no extended card; the public card is complete"
                )
            if method in UNSUPPORTED_METHODS:
                raise A2AError(
                    PUSH_NOTIFICATION_NOT_SUPPORTED,
                    "push notifications are not supported; use message/stream or tasks/subscribe",
                )
            if method in ("message/stream", "tasks/subscribe", "tasks/resubscribe"):
                raise A2AError(
                    INVALID_REQUEST,
                    f"{method} is a streaming method; POST with Accept: text/event-stream",
                )
            raise A2AError(METHOD_NOT_FOUND, f"unknown method {method!r}")
        except A2AError as exc:
            return rpc_error(req_id, exc.code, exc.message, exc.data)

    async def send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        """`message/send` → submit a run. A message on an existing task is refused when that task
        is parked on a human gate — see the module docstring."""
        from ._services import ServiceError  # noqa: PLC0415

        message = params.get("message")
        if not isinstance(message, dict):
            raise A2AError(INVALID_PARAMS, "params.message is required")
        task_id = message.get("taskId")
        if task_id:
            job = await self._job_view(str(task_id))
            state = JOB_TO_TASK_STATE.get(str(job.status), "unknown")
            if state == "input-required":
                gate = self.gate_url_for(job)
                raise A2AError(
                    UNSUPPORTED_OPERATION,
                    "this task is waiting on a human gate that an A2A caller cannot resolve; "
                    "a person resolves it through the review queue"
                    + (f" ({gate})" if gate else ""),
                    {"gate_url": gate} if gate else None,
                )
            raise A2AError(
                UNSUPPORTED_OPERATION,
                f"task {task_id!r} is {state}; a follow-up message cannot continue it — send a "
                "new message (a new run) instead",
            )
        topology = self._skill_topology(params, message)
        text = message_text(message)
        if not text.strip():
            raise A2AError(INVALID_PARAMS, "the message has no text or data part")
        context_id = str(message.get("contextId") or uuid.uuid4().hex)
        try:
            job = await self.jobs.start(
                rt=self.rt,
                canary=self.canary,
                store=self.store,
                cfg=self.cfg,
                semaphore=self.semaphore,
                topology_name=topology,
                user_input=text,
                max_steps=int(params.get("configuration", {}).get("maxSteps") or 50),
                correlation_id=context_id,
                labels={LABEL_CONTEXT: context_id},
                attachments=message_attachments(message),
                source=SOURCE,
            )
        except ServiceError as exc:
            code = INVALID_PARAMS if exc.status in (400, 404, 422) else INTERNAL_ERROR
            raise A2AError(code, str(exc)) from exc
        # The task id IS the job id — no second identifier to keep in step.
        view = await self._job_view(job.id)
        task = self._task(view)
        task["contextId"] = context_id
        return task

    async def get_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("id")
        if not task_id:
            raise A2AError(INVALID_PARAMS, "params.id is required")
        return self._task(await self._job_view(str(task_id)))

    async def list_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        """Runs that arrived over A2A, newest first — the durable store filtered on `source`."""
        if self.store is None:
            return {"tasks": []}
        limit = int(params.get("pageSize") or 50)
        rows = self.store.list_jobs(limit=max(limit * 4, 200))
        tasks = [task_from_job(r) for r in rows if getattr(r, "source", None) == SOURCE]
        context = params.get("contextId")
        if context:
            tasks = [t for t in tasks if t["contextId"] == context]
        return {"tasks": tasks[:limit]}

    async def cancel_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """`tasks/cancel` → the cooperative stop. The task reports `canceled` only once the job
        reports `stopped`; until then it is still `working` with the stop requested."""
        from swarmkit_runtime.stop import request_stop  # noqa: PLC0415

        task_id = params.get("id")
        if not task_id:
            raise A2AError(INVALID_PARAMS, "params.id is required")
        job = await self._job_view(str(task_id))
        state = JOB_TO_TASK_STATE.get(str(job.status), "unknown")
        if state in TERMINAL_STATES:
            raise A2AError(TASK_NOT_CANCELABLE, f"task {task_id!r} is already {state}")
        if self.store is not None:
            request_stop(self.store, str(task_id), requested_by=self.actor)
        task = self._task(job)
        task["metadata"]["swarmkit"]["stop_requested"] = True
        return task

    async def stream(self, params: dict[str, Any], *, subscribe: bool) -> AsyncGenerator[str]:
        """`message/stream` (submit, then follow) and `tasks/subscribe` (follow an existing task)
        as SSE frames, each a JSON-RPC result carrying a status-update or artifact-update event."""
        import asyncio  # noqa: PLC0415

        if subscribe:
            task_id = str(params.get("id") or "")
            if not task_id:
                yield _frame(rpc_error(None, INVALID_PARAMS, "params.id is required"))
                return
        else:
            try:
                task = await self.send_message(params)
            except A2AError as exc:
                yield _frame(rpc_error(None, exc.code, exc.message, exc.data))
                return
            task_id = task["id"]
            yield _frame(_rpc_result(None, task))
        last_state = None
        sent_events = 0
        while True:
            try:
                job = await self._job_view(task_id)
            except A2AError as exc:
                yield _frame(rpc_error(None, exc.code, exc.message))
                return
            events = list(getattr(job, "events", None) or [])
            for ev in events[sent_events:]:
                yield _frame(
                    _rpc_result(
                        None,
                        {
                            "kind": "status-update",
                            "taskId": task_id,
                            "contextId": _labels(job).get(LABEL_CONTEXT) or task_id,
                            "status": {"state": "working", "timestamp": _now()},
                            "final": False,
                            "metadata": {"swarmkit": {"event": ev}},
                        },
                    )
                )
                sent_events += 1
            state = JOB_TO_TASK_STATE.get(str(job.status), "unknown")
            if state != last_state:
                last_state = state
                current = self._task(job)
                if state == "completed" and current.get("artifacts"):
                    for artifact in current["artifacts"]:
                        yield _frame(
                            _rpc_result(
                                None,
                                {
                                    "kind": "artifact-update",
                                    "taskId": task_id,
                                    "contextId": current["contextId"],
                                    "artifact": artifact,
                                    "lastChunk": True,
                                },
                            )
                        )
                yield _frame(
                    _rpc_result(
                        None,
                        {
                            "kind": "status-update",
                            "taskId": task_id,
                            "contextId": current["contextId"],
                            "status": current["status"],
                            "final": state in TERMINAL_STATES,
                        },
                    )
                )
                if state in TERMINAL_STATES:
                    return
            await asyncio.sleep(0.3)


def _frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"
