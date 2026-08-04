"""Demo: `auth: none` can name its operator, so a local gate becomes approvable.

The gap: `provider: none` grants wildcard scopes and authorises everything, but hands out the
identity `anonymous` — and the approval engine matches `client_id` against `members:` in
swarm/roles.yaml, never scopes. So `anonymous` was refused at a gate for being nobody, and a single
operator had to stand up an identity provider to click Approve on their own machine.

    uv run python packages/runtime/demos/named_local_operator.py
"""

from __future__ import annotations

import asyncio

from swarmkit_runtime.auth._none import NoneAuthProvider
from swarmkit_runtime.governance._approval import Role, RoleRegistry
from swarmkit_runtime.review._multiparty import membership_error

REGISTRY = RoleRegistry(
    roles={
        "approver": Role(
            id="approver",
            members=frozenset({"srijith"}),
            scopes=frozenset({"approvals:resolve"}),
        )
    }
)


async def _report(label: str, provider: NoneAuthProvider) -> None:
    identity = await provider.authenticate(None)  # type: ignore[arg-type]
    error = membership_error(
        REGISTRY, role="approver", scope="approvals:resolve", identity=identity.client_id
    )
    verdict = "REFUSED — " + error if error else "may resolve the gate"
    authorised = await provider.authorize(identity, "anything", "anything")
    print(f"  {label:<26} {identity.client_id:<14} {verdict}")
    print(f"  {'':<26} {'':<14} scopes={set(identity.scopes)} authorize()={authorised}")


async def main() -> None:
    print("\n  swarm/roles.yaml declares:  approver -> members: [srijith], scopes:")
    print("                              [approvals:resolve]\n")
    print("  " + "─" * 76)
    await _report("provider: none (default)", NoneAuthProvider())
    print()
    await _report(
        "identity: srijith", NoneAuthProvider(identity="srijith", identity_name="Srijith")
    )
    print("  " + "─" * 76)
    print(
        "\n  Both identities hold the SAME authority — wildcard scopes, authorize() True for\n"
        "  everything. Only the name differs, and the name is the only thing the approval\n"
        "  engine consults. `anonymous` was refused for being nobody, not for lacking rights.\n"
    )
    identity = await NoneAuthProvider(identity="srijith").authenticate(None)  # type: ignore[arg-type]
    print(f"  The audit still records provider={identity.provider!r} — asserted on loopback,")
    print("  not verified by an identity provider. Nothing is misrepresented.\n")


if __name__ == "__main__":
    asyncio.run(main())
