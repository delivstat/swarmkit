"""A fleet deploy writes the adopted file's text, not a re-serialised dict.

`/fleet/state` carries each artifact's `yaml` next to its parsed `content`; a panel that adopted the
artifact sends both back on deploy. The signature covers `content`, so the text is written only when
it parses to exactly that — otherwise a deploy could write what was never signed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import copy_workspace
from fastapi.testclient import TestClient
from swarmkit_runtime.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
ADMIN_TOKEN = "admin-transport-token"  # test literal

# The example workspace's own topology, re-annotated: valid to the resolver, and carrying comments
# a re-serialised dict would lose.
_TEXT = (EXAMPLE_WS / "topologies" / "hello.yaml").read_text().replace(
    "version: 0.1.0", "version: 2.0.0        # deployed by the fleet", 1
) + "# keep this comment\n"
_CONTENT: dict[str, Any] = yaml.safe_load(_TEXT)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    return copy_workspace(EXAMPLE_WS, tmp_path / "ws")


@pytest.fixture()
def client(workspace: Path) -> Iterator[TestClient]:
    from swarmkit_runtime.auth import APIKeyAuthProvider  # noqa: PLC0415

    auth = APIKeyAuthProvider(
        keys=[{"key_ref": ADMIN_TOKEN, "client_id": "operator", "tier": "admin"}]
    )
    with TestClient(create_app(workspace, auth_provider=auth)) as c:
        yield c


def _hdr() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def test_fleet_state_carries_the_file_text(client: TestClient) -> None:
    entry = client.get("/fleet/state", headers=_hdr()).json()["artifacts"]["topologies"][0]
    assert entry["id"] == "hello"
    assert entry["yaml"].startswith("apiVersion: swarmkit/v1")
    assert "content" in entry  # the parsed form stays for adopt / hashing
    manifest = client.get("/fleet/state/manifest", headers=_hdr()).json()
    assert "yaml" not in manifest["artifacts"]["topologies"][0]


def test_deploy_with_text_writes_it_verbatim(client: TestClient, workspace: Path) -> None:
    r = client.put(
        "/api/topologies/hello", json={"content": _CONTENT, "yaml": _TEXT}, headers=_hdr()
    )
    assert r.status_code == 200, r.text
    assert (workspace / "topologies" / "hello.yaml").read_text() == _TEXT


def test_deploy_without_text_serialises_the_content(client: TestClient, workspace: Path) -> None:
    r = client.put("/api/topologies/hello", json={"content": _CONTENT}, headers=_hdr())
    assert r.status_code == 200, r.text
    on_disk = (workspace / "topologies" / "hello.yaml").read_text()
    assert "# keep this comment" not in on_disk
    assert "version: 2.0.0" in on_disk


def test_text_that_is_not_the_signed_content_is_refused(
    client: TestClient, workspace: Path
) -> None:
    before = (workspace / "topologies" / "hello.yaml").read_text()
    tampered = _TEXT.replace("archetype: greeter", "archetype: nobody")
    assert tampered != _TEXT
    r = client.put(
        "/api/topologies/hello", json={"content": _CONTENT, "yaml": tampered}, headers=_hdr()
    )
    assert r.status_code == 400
    assert "does not match" in r.json()["detail"]
    assert (workspace / "topologies" / "hello.yaml").read_text() == before
    broken = client.put(
        "/api/topologies/hello", json={"content": _CONTENT, "yaml": "a: [b"}, headers=_hdr()
    )
    assert broken.status_code == 400
