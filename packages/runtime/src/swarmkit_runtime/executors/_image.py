"""Build-in-sandbox: provision the harness into a derived image (executor-container-sandbox.md #19).

A ``sandbox.build`` block means the harness runs in-container with **no local install** — the user
brings only their API key. The three build front-ends (``base`` + ``install`` | ``dockerfile`` |
``dockerfile_inline``) all lower to one Dockerfile, which we build **once**, tag content-addressed
by ``(adapter_id, resolved Dockerfile)``, and reuse on every subsequent run. Consistent with the
rest of the feature, no image is published — it is built locally on first use.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path

from ._adapter_spec import BuildSpec
from ._protocol import ExecutorError

_HARNESS_IMAGE_PREFIX = "swarmkit-harness"


def resolve_dockerfile(build: BuildSpec, workspace_root: Path) -> tuple[str, Path]:
    """Lower a build front-end to ``(dockerfile_text, context_dir)``.

    - ``base`` (+ ``install``) → ``FROM <base>`` + a ``RUN`` per step; context = the workspace root.
    - ``dockerfile_inline`` → the text verbatim; context is the workspace root.
    - ``dockerfile`` → read the file (relative → workspace root); context is the file's directory.
    """
    if build.dockerfile_inline is not None:
        return build.dockerfile_inline, workspace_root
    if build.dockerfile is not None:
        path = Path(build.dockerfile)
        resolved = path if path.is_absolute() else (workspace_root / path)
        if not resolved.is_file():
            raise ExecutorError(f"sandbox.build.dockerfile not found: {resolved}")
        return resolved.read_text(encoding="utf-8"), resolved.parent
    if build.base is not None:
        lines = [f"FROM {build.base}"]
        lines += [f"RUN {step}" for step in build.install]
        return "\n".join(lines) + "\n", workspace_root
    # Schema's oneOf guarantees a front-end; belt-and-braces for hand-built specs.
    raise ExecutorError("sandbox.build must set one of base, dockerfile, or dockerfile_inline")


def image_tag(adapter_id: str, dockerfile_text: str) -> str:
    """Content-addressed tag: rebuilds only when the adapter id or resolved Dockerfile changes.
    (A ``dockerfile`` that COPYs changing context is not re-hashed — a documented v1 limitation.)"""
    digest = hashlib.sha256(f"{adapter_id}\x00{dockerfile_text}".encode()).hexdigest()[:12]
    return f"{_HARNESS_IMAGE_PREFIX}/{adapter_id}:{digest}"


async def _run(runtime: str, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        runtime, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def build_harness_image(
    runtime: str, adapter_id: str, build: BuildSpec, workspace_root: Path
) -> str:
    """Build (once, cached) and return the derived image tag. No-op when the tag already exists."""
    dockerfile_text, context = resolve_dockerfile(build, workspace_root)
    tag = image_tag(adapter_id, dockerfile_text)

    code, _out, _err = await _run(runtime, "image", "inspect", tag)
    if code == 0:
        return tag  # already built

    # Write the resolved Dockerfile to a temp file and build with the chosen context so COPY works.
    with tempfile.TemporaryDirectory(prefix="swarmkit-build-") as tmp:
        dockerfile = Path(tmp) / "Dockerfile"
        dockerfile.write_text(dockerfile_text, encoding="utf-8")
        code, _out, err = await _run(
            runtime, "build", "-t", tag, "-f", str(dockerfile), str(context)
        )
    if code != 0:
        raise ExecutorError(
            f"failed to build harness image {tag} for {adapter_id!r}: {err.strip()}"
        )
    return tag


__all__ = ["build_harness_image", "image_tag", "resolve_dockerfile"]
