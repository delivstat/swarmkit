"""Container isolation tier for harness executors (executor-container-sandbox.md).

The enforced boundary the worktree only advises: a harness runs inside a docker|podman container
with resource limits and (task #14) enforced egress, the worktree bind-mounted read-write so its
diff still reaches the host. Opt-in via the adapter ``sandbox`` block; a global disable switch
(handled upstream in ``_sandbox_for``) always wins.

Shape: **run-per-invocation.** We provision the same git worktree the native path uses, then build
a ``<runtime> run … <image>`` **exec-prefix** on the yielded :class:`SandboxHandle`. The generic
engine (``_declarative._open_stream``) launches ``exec_prefix + argv``, so the harness argv runs
*inside* the container at the mounted worktree. Auth reaches the container via ``-e <KEY>``
forwarding — the value comes from the launch process env (built by ``_launch_env`` with the same
auth-stripping), never the argv and never the image (same rule as the MCP Docker sandbox).

Provisioning + teardown are core's job; the container is ``--rm`` (auto-removed) and the worktree is
torn down by the wrapped :func:`worktree_sandbox`.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ._adapter_spec import SandboxSpec
from ._egress import egress_for
from ._image import build_harness_image
from ._mcp_reach import mcp_reachability
from ._protocol import ExecutorError
from ._run import SandboxHandle
from ._sandbox import worktree_sandbox

# Where the worktree is mounted inside the container; the harness runs here.
_CONTAINER_WORKDIR = "/workspace"

_logger = logging.getLogger(__name__)


def _resolve_runtime() -> str:
    """Pick the container runtime: ``$SWARMKIT_CONTAINER_RUNTIME`` if set + present, else docker,
    else podman. Fail loud when none is on PATH — an archetype that opted into a container must
    never quietly run unsandboxed."""
    override = os.environ.get("SWARMKIT_CONTAINER_RUNTIME", "").strip()
    if override:
        if shutil.which(override):
            return override
        raise ExecutorError(
            f"SWARMKIT_CONTAINER_RUNTIME={override!r} is not on PATH. Install it, or unset it to "
            "auto-detect docker|podman, or set SWARMKIT_DISABLE_CONTAINER_SANDBOX=1 for worktree."
        )
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            return candidate
    raise ExecutorError(
        "sandbox.kind is container but no container runtime (docker|podman) is on PATH. Install "
        "one, set $SWARMKIT_CONTAINER_RUNTIME, or set SWARMKIT_DISABLE_CONTAINER_SANDBOX=1 to run "
        "in the native worktree."
    )


def _validate_image_source(spec: SandboxSpec) -> None:
    """Fail fast (before provisioning) when no image source is configured. A prebuilt ``image`` or
    ``$SWARMKIT_HARNESS_IMAGE`` or a ``build`` block is required — never a guessed base image."""
    if spec.image or os.environ.get("SWARMKIT_HARNESS_IMAGE", "").strip() or spec.build is not None:
        return
    raise ExecutorError(
        "sandbox.kind is container but no image is configured. Set sandbox.image, sandbox.build, "
        "or $SWARMKIT_HARNESS_IMAGE — SwarmKit publishes no default image, and will not guess one."
    )


async def _resolve_image(
    runtime: str, spec: SandboxSpec, adapter_id: str, workspace_root: Path
) -> str:
    """The image the harness runs in. A prebuilt ``sandbox.image`` (or ``$SWARMKIT_HARNESS_IMAGE``)
    wins; else a ``build`` block is built once + cached (build-in-sandbox); else a clear error."""
    prebuilt = spec.image or os.environ.get("SWARMKIT_HARNESS_IMAGE", "").strip()
    if prebuilt:
        return prebuilt
    if spec.build is not None:
        return await build_harness_image(runtime, adapter_id, spec.build, workspace_root)
    _validate_image_source(spec)  # raises — no source at all
    raise AssertionError  # unreachable; keeps mypy happy about the return


def _resource_args(spec: SandboxSpec) -> list[str]:
    args: list[str] = []
    if spec.cpus:
        args += ["--cpus", str(spec.cpus)]
    if spec.memory:
        args += ["--memory", str(spec.memory)]
    if spec.pids is not None:
        args += ["--pids-limit", str(spec.pids)]
    return args


def _mount_args(host_worktree: Path, spec: SandboxSpec, workspace_root: Path) -> list[str]:
    """The worktree is bind-mounted read-write at the workdir so the harness's writes land on the
    host worktree (where ``collect_diff`` reads them), plus any extra ``sandbox.mounts`` (a KB dir,
    shared config, a second source tree). A mount ``source`` relative to the workspace root."""
    args = ["-v", f"{host_worktree}:{_CONTAINER_WORKDIR}", "-w", _CONTAINER_WORKDIR]
    for mount in spec.mounts:
        src = Path(mount.source)
        resolved = src if src.is_absolute() else (workspace_root / src)
        args += ["-v", f"{resolved.resolve()}:{mount.target}:{mount.mode}"]
    return args


def _env_forward_args(env_keys: Sequence[str]) -> list[str]:
    """One ``-e <KEY>`` per harness env var (no value): the runtime forwards it from the launch
    process env into the container. Secrets thus never appear in the argv or the image."""
    args: list[str] = []
    for key in env_keys:
        args += ["-e", key]
    return args


def _env_inline_args(env: dict[str, str]) -> list[str]:
    """One ``-e KEY=VALUE`` per non-secret inline var (the egress proxy vars). Sorted for stable
    argv (testability)."""
    args: list[str] = []
    for key in sorted(env):
        args += ["-e", f"{key}={env[key]}"]
    return args


def _build_exec_prefix(
    runtime: str,
    host_worktree: Path,
    spec: SandboxSpec,
    env_keys: Sequence[str],
    image: str,
    workspace_root: Path,
    *,
    network_args: Sequence[str] = (),
    inline_env: dict[str, str] | None = None,
) -> tuple[str, ...]:
    return (
        runtime,
        "run",
        "--rm",
        "-i",
        *_mount_args(host_worktree, spec, workspace_root),
        *_resource_args(spec),
        *network_args,
        *_env_forward_args(env_keys),
        *_env_inline_args(inline_env or {}),
        image,
    )


def _effective_allow(spec: SandboxSpec, mcp_configs: Mapping[str, Any]) -> tuple[str, ...]:
    """The egress allowlist plus any **http** MCP servers' hostnames (so a containerized harness can
    reach them), deduped. **stdio** MCP servers can't cross the container boundary — warn, don't
    bridge (a v1 limitation; sidecar/shim deferred)."""
    if not spec.is_container or spec.network != "allowlist":
        return spec.allow
    http_hosts, stdio_ids = mcp_reachability(mcp_configs)
    if stdio_ids:
        _logger.warning(
            "container sandbox with network=allowlist cannot reach stdio MCP server(s) %s — they "
            "speak over local pipes; use an http MCP server or drop the container sandbox for them",
            ", ".join(stdio_ids),
        )
    return tuple(dict.fromkeys([*spec.allow, *http_hosts]))


@asynccontextmanager
async def container_sandbox(
    repo_root: Path | str,
    base_ref: str,
    spec: SandboxSpec,
    *,
    adapter_id: str = "harness",
    env_keys: Sequence[str] = (),
    mcp_configs: Mapping[str, Any] | None = None,
) -> AsyncIterator[SandboxHandle]:
    """Provision a container sandbox for the harness and yield its :class:`SandboxHandle`.

    Fails loud (``ExecutorError``) when no container runtime or resolvable image is available —
    never a silent unsandboxed run. Image resolution builds a ``sandbox.build`` block once + caches
    it (build-in-sandbox). The worktree checkout + teardown are the wrapped
    :func:`worktree_sandbox`; egress (``deny`` → ``--network none``; ``allowlist`` → an internal
    network + filtered proxy) is the wrapped :func:`egress_for`; the container is ``--rm``.
    ``env_keys`` are the harness's declared env vars, forwarded from the launch process env.
    ``mcp_configs`` extends an allowlist with http MCP servers' hostnames (stdio ones warn).
    """
    runtime = _resolve_runtime()
    _validate_image_source(spec)  # fail fast before provisioning anything
    workspace_root = Path(repo_root).resolve()
    allow = _effective_allow(spec, mcp_configs or {})
    run_id = uuid.uuid4().hex
    async with (
        worktree_sandbox(repo_root, base_ref) as wt,
        egress_for(runtime, spec.network, allow, run_id) as eg,
    ):
        image = await _resolve_image(runtime, spec, adapter_id, workspace_root)
        prefix = _build_exec_prefix(
            runtime,
            wt.root,
            spec,
            env_keys,
            image,
            workspace_root,
            network_args=eg.network_args,
            inline_env=eg.env,
        )
        yield SandboxHandle(
            root=wt.root, kind="container", network=spec.network, exec_prefix=prefix
        )


__all__ = ["container_sandbox"]
