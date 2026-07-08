"""Connector-function type aliases + the drafted-artifact parser — shared by the app factory
and the route modules (in their own module to avoid an _app ⇄ routes import cycle)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from swarmkit_control_plane._artifacts import KINDS as ARTIFACT_KINDS

# (endpoint, token_ref) -> serve /capabilities body
VerifyFn = Callable[[str, str], Awaitable[dict[str, Any]]]
# (endpoint, token_ref) -> serve /fleet/state body (full InstanceState)
StateFn = Callable[[str, str], Awaitable[dict[str, Any]]]
# (endpoint, enroll_token, fleet_id, requested_scope) -> {membership_id, credential, instance_state}
RegisterFn = Callable[[str, str, str, "str | None"], Awaitable[dict[str, Any]]]
# (endpoint, membership_key) -> {membership_id, credential} (rotated key)
RefreshFn = Callable[[str, str], Awaitable[dict[str, Any]]]
# (endpoint, token_ref, kind, artifact_id, content) -> serve response
DeployFn = Callable[[str, str, str, str, Any], Awaitable[dict[str, Any]]]
# (endpoint, token_ref) -> serve /jobs list
JobsFn = Callable[[str, str], Awaitable[list[dict[str, Any]]]]
# (endpoint, token_ref, topology, message) -> {"reply", "status"}
AuthorFn = Callable[[str, str, str, str], Awaitable[dict[str, Any]]]
# (endpoint, token_ref, eval_topology, payload) -> eval summary dict
EvalFn = Callable[[str, str, str, str], Awaitable[dict[str, Any]]]


def extract_artifact(reply: str) -> dict[str, Any] | None:
    """Best-effort parse of a drafted artifact from an authoring reply. The authoring swarm
    may end its turn with a JSON envelope {kind, id, content}; if the reply parses to one
    (tolerating text around it), return {kind, id, content} for the UI to preview and propose.
    Otherwise the reply is just conversation and this returns None."""
    text = (reply or "").strip()
    if not text:
        return None
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for chunk in candidates:
        try:
            obj = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("kind") in ARTIFACT_KINDS and obj.get("id"):
            return {"kind": obj["kind"], "id": obj["id"], "content": obj.get("content")}
    return None
