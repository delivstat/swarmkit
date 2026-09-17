"""An artifact that fails validation is not left on disk.

`put_yaml` wrote the file, validated the workspace, and reloaded only when valid — and when it was
NOT valid the file stayed written. The running serve kept the old artifact, the disk held the
broken one, the response said `valid: false` as though nothing had happened, and the next
`swarmkit serve` refused the whole workspace. The portal's YAML editor saves through this route.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"

_BAD = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: hello, version: 9.9.9}
agents:
  root: {id: assistant, role: boss}
"""


@pytest.fixture()
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    dst = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, dst, ignore=shutil.ignore_patterns(".swarmkit"))
    return dst


def test_an_invalid_put_restores_the_previous_file(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    before = (ws / "topologies" / "hello.yaml").read_text()
    with TestClient(create_app(ws)) as client:
        resp = client.put("/api/topologies/hello", json={"yaml": _BAD})
        assert resp.status_code == 200 and resp.json()["valid"] is False
        assert (ws / "topologies" / "hello.yaml").read_text() == before
        # And the workspace still serves.
        assert "hello" in client.get("/topologies").json()


def test_an_invalid_create_leaves_no_file(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(ws)) as client:
        resp = client.post(
            "/api/topologies", json={"yaml": _BAD.replace("name: hello", "name: bad")}
        )
        assert resp.json()["valid"] is False
        assert not (ws / "topologies" / "bad.yaml").exists()
