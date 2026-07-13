"""Demo: the opt-in container sandbox for harness executors (executor-container-sandbox.md).

Walks the whole feature against a real container runtime (falls back to a clear notice if none is
present, or if the SWARMKIT_DISABLE_CONTAINER_SANDBOX switch is set):

  1. Run a harness command inside a resource-limited container; its edit reaches the host worktree.
  2. network=deny blocks all egress.
  3. network=allowlist permits only listed hosts (via a locally-built egress proxy).
  4. build-in-sandbox: provision the "harness" into an image with NO local install, then run it.
  5. sandbox.mounts: a knowledge-base dir mounted read-only, read from inside the container.
  6. the SWARMKIT_DISABLE_CONTAINER_SANDBOX switch forces the native worktree.

Run it (needs docker or podman + alpine):

    uv run python packages/runtime/demos/container_sandbox.py
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from swarmkit_runtime.executors import SandboxSpec, collect_diff, container_sandbox
from swarmkit_runtime.executors._adapter_spec import BuildSpec, MountSpec
from swarmkit_runtime.executors._egress import egress_for
from swarmkit_runtime.executors._image import build_harness_image

_IMAGE = "alpine:latest"


def _bar(label: str) -> None:
    print(f"\n--- {label}")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="swarmkit-sbx-demo-"))
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "demo@demo")
    _git(root, "config", "user.name", "demo")
    (root / "app.py").write_text("print('hello')\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


async def _run(*argv: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def main() -> None:
    if shutil.which("docker") is None and shutil.which("podman") is None:
        print("No container runtime (docker|podman) on PATH — the container sandbox is opt-in and")
        print("this demo needs one. Install docker/podman, or on a real workspace set")
        print("SWARMKIT_DISABLE_CONTAINER_SANDBOX=1 to run harnesses in the native worktree.")
        return

    repo = _repo()

    _bar("1. Run a harness in a resource-limited container; its edit reaches the host worktree")
    spec = SandboxSpec(kind="container", image=_IMAGE, network="deny", memory="256m", pids=256)
    async with container_sandbox(repo, "HEAD", spec, env_keys=("ANTHROPIC_API_KEY",)) as handle:
        print(f"  exec-prefix: {' '.join(handle.exec_prefix)}")
        await _run(*handle.exec_prefix, "sh", "-c", "echo 'print(42)' >> /workspace/app.py")
        diff = await collect_diff(handle)
        print(f"  diff collected on the host:\n    {diff.strip().splitlines()[-1]}")

    _bar("2. network=deny blocks all egress")
    async with egress_for("docker", "deny", (), "demo-deny") as eg:
        _code, out = await _run(
            "docker",
            "run",
            "--rm",
            *eg.network_args,
            _IMAGE,
            "sh",
            "-c",
            "wget -T 5 -q -O- http://1.1.1.1 || echo BLOCKED",
        )
        print(f"  outbound call → {out.strip()}")

    _bar("3. network=allowlist permits only listed hosts (via a locally-built egress proxy)")
    async with egress_for("docker", "allowlist", ["example.com"], "demoallow123") as eg:
        proxy = [a for k, v in eg.env.items() for a in ("-e", f"{k}={v}")]
        for host in ("example.com", "api.github.com"):
            probe = f"wget -T 8 -q -O- https://{host}/ >/dev/null 2>&1 && echo OK || echo DENIED"
            _code, out = await _run(
                "docker", "run", "--rm", *eg.network_args, *proxy, _IMAGE, "sh", "-c", probe
            )
            print(f"  {host:<18} → {out.strip()}")

    _bar("4. build-in-sandbox: provision the harness into an image (no local install), then run it")
    install = (
        "printf '#!/bin/sh\\necho i-am-the-harness\\n' > /usr/local/bin/harness "
        "&& chmod +x /usr/local/bin/harness"
    )
    build = BuildSpec(base="alpine:3.20", install=(install,))
    tag = await build_harness_image("docker", "demo-harness", build, repo)
    print(f"  built (content-addressed, cached): {tag}")
    _code, out = await _run("docker", "run", "--rm", tag, "harness")
    print(f"  ran the in-image tool → {out.strip()}")
    await _run("docker", "rmi", "-f", tag)

    _bar("5. sandbox.mounts: a knowledge-base dir mounted read-only")
    (repo / "kb").mkdir(exist_ok=True)
    (repo / "kb" / "facts.md").write_text("the answer is 42\n")
    mspec = SandboxSpec(
        kind="container",
        image=_IMAGE,
        network="deny",
        mounts=(MountSpec(source="kb", target="/kb", mode="ro"),),
    )
    async with container_sandbox(repo, "HEAD", mspec) as handle:
        _code, out = await _run(*handle.exec_prefix, "cat", "/kb/facts.md")
        print(f"  read /kb/facts.md inside the container → {out.strip()}")

    _bar("6. The disable switch always wins")
    print("  SWARMKIT_DISABLE_CONTAINER_SANDBOX=1 forces the native git-worktree sandbox for every")
    print("  archetype regardless of its adapter — the escape hatch for a box with no runtime.")

    shutil.rmtree(repo, ignore_errors=True)
    print(
        "\nOK — opt-in container isolation: run, deny/allowlist egress, build-no-install, mounts."
    )


if __name__ == "__main__":
    asyncio.run(main())
