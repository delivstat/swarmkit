"""Enforced egress for the container sandbox (executor-container-sandbox.md, task #14).

Unit-covers the proxy config/filter generation and the deny/allowlist wiring with no runtime; a
gated real-docker e2e proves `deny` blocks all egress and `allowlist` permits only listed hosts.
"""

from __future__ import annotations

import asyncio
import os
import shutil

import pytest
from swarmkit_runtime.executors import _egress as E
from swarmkit_runtime.executors._egress import EgressWiring, egress_for

# --- config generation --------------------------------------------------------------------------


def test_filter_file_anchors_each_allowed_host() -> None:
    f = E._filter_file(["api.anthropic.com", "example.org"])
    assert f == "^api\\.anthropic\\.com$\n^example\\.org$\n"  # dots escaped, anchored


def test_conf_is_default_deny_with_connect() -> None:
    conf = E._tinyproxy_conf(["api.anthropic.com"])
    assert "FilterDefaultDeny Yes" in conf  # everything not listed is refused
    assert 'Filter "/etc/tinyproxy/filter"' in conf
    assert "ConnectPort 443" in conf  # HTTPS CONNECT permitted


def test_proxy_image_tag_is_content_addressed() -> None:
    assert E._proxy_image_tag() == E._proxy_image_tag()  # stable
    assert E._proxy_image_tag().startswith("swarmkit-egress-proxy:")


# --- deny wiring (no runtime) -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deny_yields_network_none_without_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = []

    async def _boom(*a: object, **k: object) -> tuple[int, str, str]:
        called.append(a)
        return (0, "", "")

    monkeypatch.setattr(E, "_run", _boom)
    async with egress_for("docker", "deny", (), "run123") as wiring:
        assert wiring == EgressWiring(network_args=("--network", "none"))
    assert called == []  # deny provisions nothing


# --- allowlist wiring (fake runtime — assert the orchestration, not real docker) ----------------


@pytest.mark.asyncio
async def test_allowlist_provisions_proxy_and_yields_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    async def _fake_run(runtime: str, *args: str, stdin: str | None = None) -> tuple[int, str, str]:
        calls.append((runtime, *args))
        if args[:2] == ("image", "inspect"):
            return (1, "", "no such image")  # force a build
        return (0, "", "")

    monkeypatch.setattr(E, "_run", _fake_run)

    async with egress_for("docker", "allowlist", ["api.anthropic.com"], "abcdef012345") as wiring:
        assert wiring.network_args == ("--network", "swarmkit-sbx-abcdef012345")
        assert wiring.env["HTTPS_PROXY"] == "http://swarmkit-proxy-abcdef012345:8888"
        assert wiring.env["NO_PROXY"] == "localhost,127.0.0.1"

    flat = [" ".join(str(x) for x in c) for c in calls]
    assert any("build -t swarmkit-egress-proxy" in c for c in flat)  # built the proxy image
    assert any("network create --internal swarmkit-sbx-abcdef012345" in c for c in flat)
    assert any("run -d --name swarmkit-proxy-abcdef012345" in c for c in flat)
    assert any("network connect bridge swarmkit-proxy-abcdef012345" in c for c in flat)
    # torn down on exit
    assert any("rm -f swarmkit-proxy-abcdef012345" in c for c in flat)
    assert any("network rm swarmkit-sbx-abcdef012345" in c for c in flat)


@pytest.mark.asyncio
async def test_allowlist_reuses_existing_proxy_image(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _fake_run(runtime: str, *args: str, stdin: str | None = None) -> tuple[int, str, str]:
        calls.append(" ".join(args))
        return (0, "", "")  # image inspect succeeds → no build

    monkeypatch.setattr(E, "_run", _fake_run)
    async with egress_for("docker", "allowlist", ["h"], "runid0000000"):
        pass
    assert not any(c.startswith("build ") for c in calls)  # cached image reused


# --- gated real-docker e2e: deny blocks, allowlist permits only listed hosts ---------------------

_E2E = os.environ.get("SWARMKIT_E2E") == "1"


@pytest.mark.asyncio
async def test_e2e_deny_blocks_egress() -> None:
    if not _E2E or shutil.which("docker") is None:
        pytest.skip("set SWARMKIT_E2E=1 with docker on PATH to run the real egress e2e")
    async with egress_for("docker", "deny", (), "e2edeny00000") as wiring:
        # --network none: even a raw TCP dial to a public IP must fail.
        argv = [
            "docker",
            "run",
            "--rm",
            *wiring.network_args,
            "alpine:latest",
            "sh",
            "-c",
            "wget -T 5 -q -O- http://1.1.1.1 || echo BLOCKED",
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, _err = await proc.communicate()
        assert b"BLOCKED" in out


@pytest.mark.asyncio
async def test_e2e_allowlist_permits_only_listed_hosts() -> None:
    if not _E2E or shutil.which("docker") is None:
        pytest.skip("set SWARMKIT_E2E=1 with docker on PATH to run the real egress e2e")
    async with egress_for("docker", "allowlist", ["example.com"], "e2eallow0000") as wiring:
        # Inject the full proxy env the real launch does (busybox wget honours lowercase *_proxy).
        proxy_args: list[str] = []
        for key, value in wiring.env.items():
            proxy_args += ["-e", f"{key}={value}"]

        async def _reach(host: str) -> str:
            argv = [
                "docker",
                "run",
                "--rm",
                *wiring.network_args,
                *proxy_args,
                "alpine:latest",
                "sh",
                "-c",
                f"wget -T 8 -q -O- https://{host}/ >/dev/null 2>&1 && echo OK || echo DENIED",
            ]
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, _err = await proc.communicate()
            return out.decode()

        assert "OK" in await _reach("example.com")  # allowlisted
        assert "DENIED" in await _reach("api.github.com")  # not on the list
