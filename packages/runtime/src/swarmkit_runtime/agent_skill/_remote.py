"""The A2A client side — enough of the protocol to be another agent's caller.

Card resolution (RFC 8615 well-known URL, or any URL that returns a card), ``message/send``,
``tasks/get`` polling, ``tasks/cancel``, and a follow-up ``message/send`` on a task that asked a
question. Streaming is not used on the client: polling ``tasks/get`` is enough to know when a
task finished or asked something, and it makes the client indifferent to whether the remote
card says ``streaming: true``.

The wire shapes are the JSON-RPC binding our own server speaks (``server/_a2a.py``), so the first
conformance test is SwarmKit calling SwarmKit.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})


class RemoteAgentError(RuntimeError):
    """The remote agent could not be reached, refused the call, or answered malformed JSON-RPC."""


#: URI of the SwarmKit A2A federation extension a card advertises (a2a-federation.md).
SWARMKIT_A2A_EXTENSION = "https://swarmkit.dev/a2a/federation/v1"


@dataclass(frozen=True)
class AgentCard:
    url: str
    name: str
    skills: tuple[str, ...]
    streaming: bool
    #: The federation extension's `params` when the remote is a SwarmKit instance, else None —
    #: {runtime, returns_usage, returns_observability, honors_budget}. Presence == "is SwarmKit".
    swarmkit: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class RemoteTask:
    """One task as the remote reported it, reduced to what the executor decides on."""

    id: str
    context_id: str
    state: str
    #: The agent's status message text — the question, when `input-required`; the failure reason,
    #: when `failed`.
    message: str = ""
    #: Text and data parts of the artifacts, concatenated, when `completed`.
    artifact: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


def _parts_text(parts: Any) -> str:
    out: list[str] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind")
        if kind == "text" and part.get("text"):
            out.append(str(part["text"]))
        elif kind == "data" and part.get("data") is not None:
            out.append(json.dumps(part["data"]))
    return "\n".join(out)


def task_from_wire(task: dict[str, Any]) -> RemoteTask:
    status = task.get("status") or {}
    message = status.get("message") or {}
    artifact = "\n".join(
        _parts_text(a.get("parts")) for a in task.get("artifacts") or [] if isinstance(a, dict)
    )
    return RemoteTask(
        id=str(task.get("id") or ""),
        context_id=str(task.get("contextId") or ""),
        state=str(status.get("state") or "unknown"),
        message=_parts_text(message.get("parts")),
        artifact=artifact,
        raw=task,
    )


class A2AClient:
    """A thin JSON-RPC client over one ``httpx.AsyncClient``.

    ``transport`` lets a test hand in an ASGI app instead of a network; ``bearer`` is the resolved
    credential, sent on every call to the task API (the card fetch is public by construction).
    """

    def __init__(
        self,
        *,
        bearer: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._bearer = bearer
        self._transport = transport
        self._timeout = timeout_s

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self._bearer}"} if self._bearer else {}
        return httpx.AsyncClient(transport=self._transport, headers=headers, timeout=self._timeout)

    async def fetch_card(self, card_url: str) -> AgentCard:
        try:
            async with self._client() as client:
                resp = await client.get(card_url)
        except httpx.HTTPError as exc:
            raise RemoteAgentError(f"could not fetch the agent card at {card_url}: {exc}") from exc
        if resp.status_code != 200:
            raise RemoteAgentError(
                f"the agent card at {card_url} answered {resp.status_code}"
                + (" — A2A may not be enabled there" if resp.status_code == 404 else "")
            )
        try:
            card = resp.json()
        except ValueError as exc:
            raise RemoteAgentError(f"the agent card at {card_url} is not JSON") from exc
        if not isinstance(card, dict) or not card.get("url"):
            raise RemoteAgentError(f"the agent card at {card_url} has no `url`")
        skills = tuple(
            str(s.get("id"))
            for s in card.get("skills") or []
            if isinstance(s, dict) and s.get("id")
        )
        caps = card.get("capabilities") or {}
        swarmkit = None
        for ext in caps.get("extensions") or []:
            if isinstance(ext, dict) and ext.get("uri") == SWARMKIT_A2A_EXTENSION:
                params = ext.get("params")
                swarmkit = dict(params) if isinstance(params, dict) else {}
                break
        return AgentCard(
            url=str(card["url"]),
            name=str(card.get("name") or card_url),
            skills=skills,
            streaming=bool(caps.get("streaming", False)),
            swarmkit=swarmkit,
            raw=card,
        )

    async def rpc(self, url: str, method: str, params: dict[str, Any]) -> Any:
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        try:
            async with self._client() as client:
                resp = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise RemoteAgentError(f"{method} to {url} failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise RemoteAgentError(
                f"{url} refused the credential ({resp.status_code}); check `credentials_ref`"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RemoteAgentError(f"{method} to {url}: non-JSON reply {resp.status_code}") from exc
        if not isinstance(payload, dict):
            raise RemoteAgentError(f"{method} to {url}: malformed JSON-RPC reply")
        if payload.get("error"):
            err = payload["error"]
            raise RemoteAgentError(
                f"{method}: remote error {err.get('code')}: {err.get('message')}"
            )
        return payload.get("result")

    async def send(
        self,
        url: str,
        text: str,
        *,
        skill_id: str | None,
        context_id: str | None,
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
    ) -> RemoteTask:
        parts: list[dict[str, Any]] = [{"kind": "text", "text": text}]
        if data:
            parts.append({"kind": "data", "data": data})
        message: dict[str, Any] = {
            "kind": "message",
            "role": "user",
            "messageId": uuid.uuid4().hex,
            "parts": parts,
        }
        metadata: dict[str, Any] = {}
        if skill_id:
            metadata["skill"] = skill_id
        # Forward the caller's remaining allowance so a SwarmKit callee caps the child run
        # (a2a-federation.md). A non-SwarmKit callee ignores the extra key.
        if budget:
            metadata["swarmkit"] = {"budget": budget}
        if metadata:
            message["metadata"] = metadata
        if context_id:
            message["contextId"] = context_id
        if task_id:
            message["taskId"] = task_id
        result = await self.rpc(url, "message/send", {"message": message})
        if not isinstance(result, dict):
            raise RemoteAgentError("message/send returned no task")
        # A2A allows a Message reply (no task) for trivial exchanges; treat it as a completed task
        # with the message as the artifact so the caller has one shape to read.
        if result.get("kind") == "message":
            return RemoteTask(
                id=str(result.get("taskId") or ""),
                context_id=str(result.get("contextId") or context_id or ""),
                state="completed",
                artifact=_parts_text(result.get("parts")),
                raw=result,
            )
        return task_from_wire(result)

    async def get(self, url: str, task_id: str) -> RemoteTask:
        result = await self.rpc(url, "tasks/get", {"id": task_id})
        if not isinstance(result, dict):
            raise RemoteAgentError("tasks/get returned no task")
        return task_from_wire(result)

    async def cancel(self, url: str, task_id: str) -> None:
        try:
            await self.rpc(url, "tasks/cancel", {"id": task_id})
        except RemoteAgentError:
            # A task that already finished is "not cancelable"; the intent was to stop waiting,
            # and we have.
            return

    async def wait(
        self,
        url: str,
        task: RemoteTask,
        *,
        timeout_s: float,
        poll_interval: float = 0.5,
        on_state: Callable[[RemoteTask], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> RemoteTask:
        """Poll until the task is terminal or asks for input. Raises ``TimeoutError`` past
        ``timeout_s`` — after cancelling the remote task, so nothing keeps running unwatched."""
        start = clock()
        last_state = None
        while True:
            if task.state != last_state:
                last_state = task.state
                if on_state is not None:
                    on_state(task)
            if task.state in TERMINAL_STATES or task.state == "input-required":
                return task
            if clock() - start > timeout_s:
                await self.cancel(url, task.id)
                raise TimeoutError(
                    f"remote task {task.id} still {task.state} after {int(timeout_s)}s; cancelled"
                )
            await asyncio.sleep(poll_interval)
            task = await self.get(url, task.id)
