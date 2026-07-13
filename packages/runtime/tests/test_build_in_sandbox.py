"""Build-in-sandbox: derive the harness image from a build block (executor-container-sandbox.md).

Unit-covers the three build front-ends lowering to one Dockerfile, content-addressed tagging +
reuse, and build failure surfacing — with a fake runtime. A gated real-docker e2e builds + runs.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from swarmkit_runtime.executors import ExecutorError
from swarmkit_runtime.executors import _image as I
from swarmkit_runtime.executors._adapter_spec import BuildSpec
from swarmkit_runtime.executors._image import build_harness_image, image_tag, resolve_dockerfile

# --- lowering to a Dockerfile -------------------------------------------------------------------


def test_base_install_lowers_to_from_plus_runs(tmp_path: Path) -> None:
    build = BuildSpec(base="node:22-slim", install=("npm i -g a", "npm i -g b"))
    text, ctx = resolve_dockerfile(build, tmp_path)
    assert text == "FROM node:22-slim\nRUN npm i -g a\nRUN npm i -g b\n"
    assert ctx == tmp_path


def test_inline_dockerfile_verbatim(tmp_path: Path) -> None:
    build = BuildSpec(dockerfile_inline="FROM alpine\nRUN echo hi\n")
    text, ctx = resolve_dockerfile(build, tmp_path)
    assert text == "FROM alpine\nRUN echo hi\n"
    assert ctx == tmp_path


def test_dockerfile_path_read_relative_to_workspace(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    df = tmp_path / "sub" / "Harness.Dockerfile"
    df.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    text, ctx = resolve_dockerfile(BuildSpec(dockerfile="sub/Harness.Dockerfile"), tmp_path)
    assert text == "FROM python:3.12-slim\n"
    assert ctx == tmp_path / "sub"  # context is the Dockerfile's directory


def test_missing_dockerfile_path_errors(tmp_path: Path) -> None:
    with pytest.raises(ExecutorError, match="not found"):
        resolve_dockerfile(BuildSpec(dockerfile="nope.Dockerfile"), tmp_path)


# --- content-addressed tag ----------------------------------------------------------------------


def test_tag_stable_and_namespaced() -> None:
    t = image_tag("claude-code", "FROM node:22-slim\n")
    assert t == image_tag("claude-code", "FROM node:22-slim\n")  # stable
    assert t.startswith("swarmkit-harness/claude-code:")


def test_tag_changes_with_dockerfile_or_adapter() -> None:
    a = image_tag("claude-code", "FROM node:22-slim\n")
    assert a != image_tag("claude-code", "FROM node:20-slim\n")  # dockerfile change
    assert a != image_tag("opencode", "FROM node:22-slim\n")  # adapter change


# --- build + cache (fake runtime) ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_builds_when_absent_then_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []
    present = {"v": False}

    async def _fake_run(runtime: str, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return (0 if present["v"] else 1, "", "")
        if args[0] == "build":
            present["v"] = True
            return (0, "", "")
        return (0, "", "")

    monkeypatch.setattr(I, "_run", _fake_run)
    build = BuildSpec(base="alpine", install=("true",))

    tag1 = await build_harness_image("docker", "my-harness", build, tmp_path)
    assert any(a[0] == "build" for a in calls)  # built the first time
    assert tag1.startswith("swarmkit-harness/my-harness:")

    calls.clear()
    tag2 = await build_harness_image("docker", "my-harness", build, tmp_path)
    assert tag2 == tag1
    assert not any(a[0] == "build" for a in calls)  # cached second time


@pytest.mark.asyncio
async def test_build_failure_surfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run(runtime: str, *args: str) -> tuple[int, str, str]:
        if args[:2] == ("image", "inspect"):
            return (1, "", "")
        return (1, "", "step 3 failed: package not found")

    monkeypatch.setattr(I, "_run", _fake_run)
    with pytest.raises(ExecutorError, match="failed to build harness image"):
        await build_harness_image("docker", "h", BuildSpec(base="alpine"), tmp_path)


# --- gated real-docker e2e ----------------------------------------------------------------------

_E2E = os.environ.get("SWARMKIT_E2E") == "1"


@pytest.mark.asyncio
async def test_e2e_build_produces_a_runnable_image(tmp_path: Path) -> None:
    """Really build a derived image from a base+install block and run it — proves the
    no-local-install path: nothing is installed on the host, the tool lives in the built image."""
    if not _E2E or shutil.which("docker") is None:
        pytest.skip("set SWARMKIT_E2E=1 with docker on PATH to run the real build e2e")
    import asyncio  # noqa: PLC0415

    # Install a marker "tool" into the image; then run it.
    install = (
        "printf '#!/bin/sh\\necho BUILT_OK\\n' > /usr/local/bin/harness "
        "&& chmod +x /usr/local/bin/harness"
    )
    build = BuildSpec(base="alpine:3.20", install=(install,))
    tag = await build_harness_image("docker", "e2e-harness", build, tmp_path)
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        tag,
        "harness",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()
    assert b"BUILT_OK" in out
    # cleanup the built image
    rm = await asyncio.create_subprocess_exec(
        "docker",
        "rmi",
        "-f",
        tag,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await rm.communicate()
