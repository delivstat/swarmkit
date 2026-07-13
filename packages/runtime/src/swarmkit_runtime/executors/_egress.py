"""Enforced egress for the container sandbox (executor-container-sandbox.md, task #14).

``network: deny`` is trivial — ``--network none``, no route out. ``network: allowlist`` is the piece
worth building: the harness must reach *only* the named hosts (its model API, an HTTP MCP server)
and nothing else. Docker has no per-host egress ACL, so we stand up the standard shape:

  - an **internal** docker network (``--internal``: no internet route) the harness attaches to;
  - a small **forward proxy** (tinyproxy) on that internal network *and* a normal network, so it is
    the only path out — configured to allow only the ``allow`` hosts (default-deny);
  - ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``NO_PROXY`` injected into the harness so a well-behaved
    client routes through it, while the missing default route means a client that ignores the proxy
    simply can't reach anything.

Consistent with the rest of the feature, **SwarmKit publishes no proxy image**: the proxy is an
inline Dockerfile built locally, once, content-addressed + cached (same idea as build-in-sandbox).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ._protocol import ExecutorError

_PROXY_PORT = 8888
# The proxy image is data, not a published artifact: a minimal alpine + tinyproxy, built locally.
_PROXY_DOCKERFILE = "FROM alpine:3.20\nRUN apk add --no-cache tinyproxy\n"
_PROXY_IMAGE_PREFIX = "swarmkit-egress-proxy"


@dataclass(frozen=True)
class EgressWiring:
    """What the egress layer contributes to the harness launch: extra ``docker run`` args (network),
    inline env (proxy vars), and the container/network names to tear down."""

    network_args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


async def _run(runtime: str, *args: str, stdin: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        runtime,
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(stdin.encode() if stdin is not None else None)
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _proxy_image_tag() -> str:
    digest = hashlib.sha256(_PROXY_DOCKERFILE.encode()).hexdigest()[:12]
    return f"{_PROXY_IMAGE_PREFIX}:{digest}"


def _tinyproxy_conf(allow: Sequence[str]) -> str:
    """tinyproxy config: default-deny, allow only the listed hosts (exact match), permit HTTPS
    CONNECT. The ``allow`` hosts become anchored regexes in the filter file."""
    return (
        f"Port {_PROXY_PORT}\n"
        "Listen 0.0.0.0\n"
        "Timeout 600\n"
        "Allow 0.0.0.0/0\n"  # who may connect *to* the proxy (the harness); egress filtered below
        "FilterDefaultDeny Yes\n"
        "FilterExtended On\n"
        'Filter "/etc/tinyproxy/filter"\n'
        "ConnectPort 443\n"
        "ConnectPort 563\n"
    )


def _filter_file(allow: Sequence[str]) -> str:
    """One anchored regex per allowed host — default-deny means everything else is refused."""
    return "".join(f"^{re.escape(host)}$\n" for host in allow)


async def _ensure_proxy_image(runtime: str) -> str:
    """Build the tinyproxy image once (content-addressed); reuse if the tag already exists."""
    tag = _proxy_image_tag()
    code, _out, _err = await _run(runtime, "image", "inspect", tag)
    if code == 0:
        return tag
    code, _out, err = await _run(runtime, "build", "-t", tag, "-", stdin=_PROXY_DOCKERFILE)
    if code != 0:
        raise ExecutorError(f"failed to build the egress proxy image {tag}: {err.strip()}")
    return tag


@asynccontextmanager
async def egress_for(
    runtime: str, network: str, allow: Sequence[str], run_id: str
) -> AsyncIterator[EgressWiring]:
    """Provision the egress wiring for one harness run and tear it down on exit.

    - ``deny`` → ``--network none`` (no provisioning, nothing to clean up).
    - ``allowlist`` → an internal network + a filtered forward proxy; yields the network arg + the
      ``*_PROXY`` env the harness needs. Torn down (proxy container + network + temp conf) on exit.
    """
    if network != "allowlist":
        yield EgressWiring(network_args=("--network", "none"))
        return

    net = f"swarmkit-sbx-{run_id[:12]}"
    proxy = f"swarmkit-proxy-{run_id[:12]}"
    conf_dir = Path(tempfile.mkdtemp(prefix="swarmkit-egress-"))
    try:
        (conf_dir / "tinyproxy.conf").write_text(_tinyproxy_conf(allow), encoding="utf-8")
        (conf_dir / "filter").write_text(_filter_file(allow), encoding="utf-8")
        image = await _ensure_proxy_image(runtime)

        code, _out, err = await _run(runtime, "network", "create", "--internal", net)
        if code != 0:
            raise ExecutorError(f"failed to create egress network {net}: {err.strip()}")
        code, _out, err = await _run(
            runtime,
            "run",
            "-d",
            "--name",
            proxy,
            "--network",
            net,
            "-v",
            f"{conf_dir}:/etc/tinyproxy:ro",
            image,
            "tinyproxy",
            "-d",
            "-c",
            "/etc/tinyproxy/tinyproxy.conf",
        )
        if code != 0:
            raise ExecutorError(f"failed to start egress proxy {proxy}: {err.strip()}")
        # Give the proxy a route to the internet (it is dual-homed: internal net + default bridge).
        code, _out, err = await _run(runtime, "network", "connect", "bridge", proxy)
        if code != 0:
            raise ExecutorError(f"failed to connect egress proxy to bridge: {err.strip()}")

        proxy_url = f"http://{proxy}:{_PROXY_PORT}"
        yield EgressWiring(
            network_args=("--network", net),
            env={
                "HTTPS_PROXY": proxy_url,
                "HTTP_PROXY": proxy_url,
                "https_proxy": proxy_url,
                "http_proxy": proxy_url,
                "NO_PROXY": "localhost,127.0.0.1",
                "no_proxy": "localhost,127.0.0.1",
            },
        )
    finally:
        await _run(runtime, "rm", "-f", proxy)
        await _run(runtime, "network", "rm", net)
        shutil.rmtree(conf_dir, ignore_errors=True)


__all__ = ["EgressWiring", "egress_for"]
