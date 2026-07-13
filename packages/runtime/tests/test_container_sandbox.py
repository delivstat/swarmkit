"""Container sandbox provisioner (executor-container-sandbox.md, task #13).

Proves the `docker run …` exec-prefix is assembled correctly — runtime detection, worktree mount,
resource limits, network mode, env forwarding, image resolution, and the fail-loud paths — without
a real container runtime. The gated e2e (needs a real runtime) proves fidelity.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from swarmkit_runtime.executors import ExecutorError, SandboxSpec, container_sandbox
from swarmkit_runtime.executors import _container as C
from swarmkit_runtime.executors._adapter_spec import BuildSpec


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tiny real git repo so the wrapped worktree_sandbox can provision a checkout."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f.txt").write_text("hi\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend docker is on PATH; nothing else."""
    monkeypatch.delenv("SWARMKIT_CONTAINER_RUNTIME", raising=False)
    monkeypatch.delenv("SWARMKIT_HARNESS_IMAGE", raising=False)
    monkeypatch.setattr(
        shutil, "which", lambda name: f"/usr/bin/{name}" if name == "docker" else None
    )


# --- runtime detection --------------------------------------------------------------------------


def test_prefers_docker_then_podman(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_CONTAINER_RUNTIME", raising=False)
    monkeypatch.setattr(shutil, "which", lambda n: "/x" if n in ("docker", "podman") else None)
    assert C._resolve_runtime() == "docker"
    monkeypatch.setattr(shutil, "which", lambda n: "/x" if n == "podman" else None)
    assert C._resolve_runtime() == "podman"


def test_runtime_override_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_CONTAINER_RUNTIME", "podman")
    monkeypatch.setattr(shutil, "which", lambda n: "/x" if n == "podman" else None)
    assert C._resolve_runtime() == "podman"


def test_no_runtime_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_CONTAINER_RUNTIME", raising=False)
    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(ExecutorError, match="no container runtime"):
        C._resolve_runtime()


def test_override_not_on_path_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_CONTAINER_RUNTIME", "nerdctl")
    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(ExecutorError, match="nerdctl"):
        C._resolve_runtime()


# --- image resolution ---------------------------------------------------------------------------


def test_validate_image_source_ok_with_image_build_or_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SWARMKIT_HARNESS_IMAGE", raising=False)
    C._validate_image_source(SandboxSpec(kind="container", image="my:tag"))  # no raise
    C._validate_image_source(SandboxSpec(kind="container", build=BuildSpec(base="alpine")))
    monkeypatch.setenv("SWARMKIT_HARNESS_IMAGE", "env-img:1")
    C._validate_image_source(SandboxSpec(kind="container"))


def test_no_image_source_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_HARNESS_IMAGE", raising=False)
    with pytest.raises(ExecutorError, match="no image is configured"):
        C._validate_image_source(SandboxSpec(kind="container"))


@pytest.mark.asyncio
async def test_resolve_image_prefers_prebuilt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SWARMKIT_HARNESS_IMAGE", raising=False)
    got = await C._resolve_image("docker", SandboxSpec(image="my:tag"), "h", tmp_path)
    assert got == "my:tag"
    monkeypatch.setenv("SWARMKIT_HARNESS_IMAGE", "env-img:1")
    got = await C._resolve_image("docker", SandboxSpec(kind="container"), "h", tmp_path)
    assert got == "env-img:1"


# --- exec-prefix assembly -----------------------------------------------------------------------


def test_exec_prefix_full_shape() -> None:
    spec = SandboxSpec(
        kind="container",
        image="my:tag",
        network="deny",
        cpus="2",
        memory="2g",
        pids=512,
    )
    prefix = C._build_exec_prefix(
        "docker",
        Path("/tmp/wt"),
        spec,
        ("ANTHROPIC_API_KEY", "FOO"),
        "my:tag",
        Path("/tmp/wt"),
        network_args=("--network", "none"),
    )
    assert prefix[0:4] == ("docker", "run", "--rm", "-i")
    assert "-v" in prefix and "/tmp/wt:/workspace" in prefix
    assert prefix[prefix.index("-w") + 1] == "/workspace"
    assert "--cpus" in prefix and "2" in prefix
    assert "--memory" in prefix and "2g" in prefix
    assert "--pids-limit" in prefix and "512" in prefix
    assert "--network" in prefix and "none" in prefix
    # env forwarded by name only — values never enter argv/image
    assert "ANTHROPIC_API_KEY" in prefix and "FOO" in prefix
    assert prefix[-1] == "my:tag"  # image is last, args come before it


def test_allowlist_injects_proxy_env_inline() -> None:
    prefix = C._build_exec_prefix(
        "docker",
        Path("/tmp/wt"),
        SandboxSpec(kind="container", image="i", network="allowlist"),
        (),
        "i",
        Path("/tmp/wt"),
        network_args=("--network", "swarmkit-sbx-abc"),
        inline_env={"HTTPS_PROXY": "http://swarmkit-proxy-abc:8888"},
    )
    assert "--network" in prefix and "swarmkit-sbx-abc" in prefix
    # inline env is KEY=VALUE (a non-secret proxy var), distinct from name-only forwarding
    assert "HTTPS_PROXY=http://swarmkit-proxy-abc:8888" in prefix


def test_mounts_are_bind_mounted_relative_to_workspace() -> None:
    from swarmkit_runtime.executors._adapter_spec import MountSpec  # noqa: PLC0415

    spec = SandboxSpec(
        kind="container",
        image="i",
        mounts=(
            MountSpec(source="knowledge", target="/kb", mode="ro"),
            MountSpec(source="/abs/cfg", target="/cfg", mode="rw"),
        ),
    )
    prefix = C._build_exec_prefix(
        "docker", Path("/tmp/wt"), spec, (), "i", Path("/ws"), network_args=("--network", "none")
    )
    # relative source resolves under the workspace root; absolute is used as-is; mode preserved
    assert "/ws/knowledge:/kb:ro" in prefix
    assert "/abs/cfg:/cfg:rw" in prefix


def test_minimal_prefix_no_limits() -> None:
    prefix = C._build_exec_prefix(
        "podman",
        Path("/w"),
        SandboxSpec(kind="container", image="i"),
        (),
        "i",
        Path("/w"),
        network_args=("--network", "none"),
    )
    assert prefix == (
        "podman",
        "run",
        "--rm",
        "-i",
        "--add-host",
        "host.docker.internal:host-gateway",
        "-v",
        "/w:/workspace",
        "-w",
        "/workspace",
        "--network",
        "none",
        "i",
    )


# --- provisioning (fake runtime, real worktree) -------------------------------------------------


@pytest.mark.asyncio
async def test_provision_yields_container_handle(repo: Path, fake_docker: None) -> None:
    spec = SandboxSpec(kind="container", image="my:tag", network="deny")
    async with container_sandbox(repo, "HEAD", spec, env_keys=("ANTHROPIC_API_KEY",)) as handle:
        assert handle.kind == "container"
        assert handle.network == "deny"
        assert handle.exec_prefix[0] == "docker"
        assert handle.exec_prefix[-1] == "my:tag"
        assert "ANTHROPIC_API_KEY" in handle.exec_prefix
        # the worktree was really provisioned under the handle root
        assert (handle.root / "f.txt").read_text() == "hi\n"
    # worktree cleaned up on exit
    assert not handle.root.exists()


@pytest.mark.asyncio
async def test_provision_no_runtime_fails_before_worktree(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SWARMKIT_CONTAINER_RUNTIME", raising=False)
    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(ExecutorError, match="no container runtime"):
        async with container_sandbox(repo, "HEAD", SandboxSpec(kind="container", image="i")):
            pass


# --- gated e2e: a real container edits the worktree; the host diff sees it -----------------------

_E2E = os.environ.get("SWARMKIT_E2E") == "1"
_IMAGE = os.environ.get("SWARMKIT_E2E_IMAGE", "alpine:latest")


@pytest.mark.asyncio
async def test_e2e_container_edit_reaches_host_diff(repo: Path) -> None:
    """With a real runtime + image, provision a container, run a command inside it that edits the
    bind-mounted worktree, and confirm the host-side `collect_diff` sees the change. Proves the
    whole boundary — mount is rw, the harness's writes land on the host, resource/network flags are
    accepted by the real runtime."""
    if not _E2E or shutil.which("docker") is None:
        pytest.skip(
            "set SWARMKIT_E2E=1 with docker on PATH (+ alpine) to run the real container e2e"
        )

    from swarmkit_runtime.executors import collect_diff  # noqa: PLC0415

    spec = SandboxSpec(kind="container", image=_IMAGE, network="deny", memory="256m", pids=256)
    async with container_sandbox(repo, "HEAD", spec) as handle:
        # Run the container the same way the engine would: exec_prefix + an in-container command.
        argv = [*handle.exec_prefix, "sh", "-c", "echo edited > /workspace/f.txt"]
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _out, err = await proc.communicate()
        assert proc.returncode == 0, err.decode()
        # The container's write landed on the host worktree.
        assert (handle.root / "f.txt").read_text() == "edited\n"
        diff = await collect_diff(handle)
        assert "edited" in diff and "f.txt" in diff


@pytest.mark.asyncio
async def test_e2e_extra_mount_is_readable_in_container(repo: Path) -> None:
    """A real container can read an extra sandbox.mount (e.g. a knowledge-base dir)."""
    if not _E2E or shutil.which("docker") is None:
        pytest.skip("set SWARMKIT_E2E=1 with docker on PATH (+ alpine) to run the real mount e2e")
    from swarmkit_runtime.executors._adapter_spec import MountSpec  # noqa: PLC0415

    (repo / "kb").mkdir()
    (repo / "kb" / "note.txt").write_text("KB_CONTENT\n")
    spec = SandboxSpec(
        kind="container",
        image=_IMAGE,
        network="deny",
        mounts=(MountSpec(source="kb", target="/kb", mode="ro"),),
    )
    async with container_sandbox(repo, "HEAD", spec) as handle:
        argv = [*handle.exec_prefix, "cat", "/kb/note.txt"]
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        assert proc.returncode == 0, err.decode()
        assert b"KB_CONTENT" in out
