"""Demo: who may resolve a multi-party approval role-task (slice 1).

design/details/pipeline-gate-approval-ui.md. The resolver identity is the *authenticated caller*,
never a request-body field, and it is checked against the workspace role registry before the
resolution is recorded. This runs the real serve app + the real file-backed review queue against a
throwaway workspace (no model call, no API budget):

  1. A gate is opened for a 2-of-2 policy — security-reviewer (alice) and release-manager (bob).
  2. An unauthenticated caller is refused: `anonymous` is not a member of either role. The refusal
     names the reason; the item stays pending.
  3. A caller authenticated as alice resolves her role-task. The gate is still PENDING — one of two.
  4. A body-supplied `"identity": "bob"` does NOT let alice cast bob's vote: the body field is
     ignored, alice is not a member of release-manager, and the request is refused.
  5. Authenticated as bob, the second role-task resolves and the gate flips to APPROVED.
  6. The audit trail shows every attempt, allowed and denied, with the resolver identity.

Run it:

    uv run python packages/runtime/demos/review_resolve_identity.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_runtime.auth import AuthProvider
from swarmkit_runtime.auth._provider import AuthIdentity, AuthRequest
from swarmkit_runtime.governance._approval import ApprovalPolicy, GateStatus, evaluate
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import collect_resolutions, open_gate
from swarmkit_runtime.server import create_app

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"

ROLES_YAML = """\
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata:
  id: demo-roles
  name: Demo roles
roles:
  - id: security-reviewer
    members: [alice]
    scopes: [security:approve]
  - id: release-manager
    members: [bob]
    scopes: [security:approve]
"""

POLICY = ApprovalPolicy.from_dict(
    {
        "rules": [
            {
                "scope": "security:approve",
                "roles": ["security-reviewer", "release-manager"],
                "quorum": "all",
            }
        ]
    }
)

GATE_ID = "run-42:design"


class _FixedIdentityAuth(AuthProvider):
    """An auth provider that authenticates every request as one fixed identity.

    Serve derives the resolver from ``request.state.identity.client_id``, which a real deployment
    gets from the API-key / JWT provider. Standing in for one here keeps the demo about the
    *identity check* rather than credential plumbing — note the transport scopes deliberately do
    NOT include ``approvals:resolve``, which is reserved and un-grantable to a token (§8.7); the
    governance provider is what grants it to a human identity.
    """

    def __init__(self, client_id: str) -> None:
        self._id = client_id

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        return AuthIdentity(
            client_id=self._id,
            client_name=self._id,
            provider="demo",
            scopes=frozenset({"serve:read", "serve:run"}),
        )

    async def authorize(self, identity: AuthIdentity, resource: str, action: str) -> bool:
        return True

    @property
    def mode(self) -> str:
        return "demo"


def _client(ws: Path, *, as_identity: str | None) -> TestClient:
    auth = _FixedIdentityAuth(as_identity) if as_identity else None
    return TestClient(create_app(ws, auth_provider=auth, insecure=True))


def _try(ws: Path, item: str, *, as_identity: str | None, body: dict[str, Any]) -> None:
    who = as_identity or "(unauthenticated)"
    with _client(ws, as_identity=as_identity) as c:
        c.app.state.runtime._governance = GOV  # type: ignore[attr-defined]
        resp = c.post(f"/review/{item}/resolve", json=body)
    extra = f"  body={body}" if "identity" in body else ""
    if resp.status_code == 200:
        print(f"  ✓ {who:<18} → 200 {resp.json()['status']}{extra}")
    else:
        print(f"  ✗ {who:<18} → {resp.status_code} {resp.json()['detail']}{extra}")


GOV = MockGovernanceProvider(allowed_scopes=frozenset({"approvals:resolve"}))


def _gate(ws: Path, queue: FileReviewQueue) -> None:
    """Print the gate's real status — read back through the same engine the runtime uses."""
    registry = resolve_workspace(ws).role_registry
    res = collect_resolutions(queue, gate_id=GATE_ID, policy=POLICY)
    ev = evaluate(POLICY, registry, res)
    who = sorted(r.identity for r in res)
    status = "APPROVED" if ev.status is GateStatus.APPROVED else ev.status.value.upper()
    print(f"     gate: {len(res)} of 2 resolved by {who} — {status}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        shutil.copytree(EXAMPLE_WS, ws)
        (ws / "roles").mkdir(exist_ok=True)
        (ws / "roles" / "demo-roles.yaml").write_text(ROLES_YAML)

        queue = FileReviewQueue(ws)
        open_gate(queue, gate_id=GATE_ID, topology_id="run-42", agent_id="design", policy=POLICY)
        sec, rel = (
            f"mpa-{GATE_ID}-0-security-reviewer",
            f"mpa-{GATE_ID}-0-release-manager",
        )

        print("\n1. Gate opened — 2 role-tasks, both pending")
        for i in queue.list_pending():
            print(f"     {i.id}   role={i.output['role']}")

        print("\n2. Unauthenticated caller (NoneAuthProvider yields 'anonymous')")
        _try(ws, sec, as_identity=None, body={"outcome": "approve"})

        print("\n3. Authenticated as alice — a member of security-reviewer")
        _try(ws, sec, as_identity="alice", body={"outcome": "approve"})
        _gate(ws, queue)

        print("\n4. alice tries to cast bob's vote with a body-supplied identity")
        _try(ws, rel, as_identity="alice", body={"outcome": "approve", "identity": "bob"})

        print("\n5. Authenticated as bob — a member of release-manager")
        _try(ws, rel, as_identity="bob", body={"outcome": "approve"})
        _gate(ws, queue)

        print("\n6. Audit trail — every attempt, allowed and denied")
        for e in GOV.events:
            if e.event_type != "approval.role_task_resolved":
                continue
            print(
                f"     {e.policy_decision:<5} {e.payload['identity']!s:<10}"
                f" {e.payload['role']!s:<18} {e.policy_reason}"
            )
        print()


if __name__ == "__main__":
    main()
