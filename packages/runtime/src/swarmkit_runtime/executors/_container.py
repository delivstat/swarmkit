"""Container isolation tier for harness executors (executor-container-sandbox.md).

The enforced boundary the worktree only advises: a harness runs inside a docker|podman container
with resource limits and enforced egress, the worktree bind-mounted read-write so its diff still
reaches the host. Opt-in via the adapter ``sandbox`` block; a global disable switch always wins.

This module owns provisioning + teardown; ``_declarative._open_stream`` runs ``argv`` behind the
handle's ``exec_prefix`` (the ``docker run … <image>`` wrapper). The provisioner itself
(runtime detection, build-in-sandbox, mounts, egress proxy) lands in tasks #13/#14/#19/#20; this
seam gives the config tier a real, reachable branch that fails loud until then.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from ._adapter_spec import SandboxSpec
from ._protocol import ExecutorError
from ._run import SandboxHandle


@asynccontextmanager
async def container_sandbox(
    repo_root: Path | str, base_ref: str, spec: SandboxSpec
) -> AsyncIterator[SandboxHandle]:
    """Provision a container sandbox for the harness and yield its :class:`SandboxHandle`.

    Not yet implemented — the provisioner lands in task #13. Until then this raises a clear
    :class:`ExecutorError` rather than silently running unsandboxed: an archetype that opted into a
    container must never quietly fall back (that would be a security lie). The escape hatch is the
    explicit ``SWARMKIT_DISABLE_CONTAINER_SANDBOX`` switch, handled upstream in ``_sandbox_for``.
    """
    raise ExecutorError(
        "container sandbox is configured (sandbox.kind: container) but the provisioner is not yet "
        "available in this build. Set SWARMKIT_DISABLE_CONTAINER_SANDBOX=1 to run in the native "
        "worktree, or use an adapter without a container sandbox."
    )
    yield  # pragma: no cover  — makes this an async generator; the raise above always fires


__all__ = ["container_sandbox"]
