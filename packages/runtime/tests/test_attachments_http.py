"""``POST /run/{topology}`` with attachments — the request surface and where it refuses.

``design/details/images-on-both-executors.md``. The point of validating at this boundary is that a
caller gets an answer *to the request*: a job id means the file was readable. Left to the run, a
missing path becomes a job that fails a second later, and the caller has to poll to find out — a
failure shape people stop checking for.
"""

from __future__ import annotations

import base64
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.server import create_app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A copy of the hello-swarm workspace, so attachments can be written into its root.

    Entered as a context manager because the workspace is resolved in the app's lifespan — a bare
    ``TestClient`` answers 503 to everything, which reads like a routing bug rather than a missing
    startup.
    """
    src = Path(__file__).resolve().parents[3] / "examples" / "hello-swarm" / "workspace"
    ws = tmp_path / "workspace"
    shutil.copytree(src, ws)
    (ws / "shots").mkdir()
    (ws / "shots" / "gate.png").write_bytes(PNG)
    (ws / "notes.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 16)
    with TestClient(create_app(ws)) as c:
        yield c


def _topology(client: TestClient) -> str:
    body = client.get("/topologies").json()
    names = body["topologies"] if isinstance(body, dict) else body
    first = sorted(names)[0]
    return str(first["name"] if isinstance(first, dict) else first)


def _post(client: TestClient, **body: object) -> object:
    return client.post(f"/run/{_topology(client)}", json={"input": "hi", **body})


# --- accepted ----------------------------------------------------------------------------------


def test_a_readable_attachment_starts_a_job(client: TestClient) -> None:
    resp = _post(client, attachments=[{"path": "shots/gate.png"}])
    assert resp.status_code == 200  # type: ignore[attr-defined]
    assert resp.json()["job_id"]  # type: ignore[attr-defined]


def test_inline_base64_is_accepted(client: TestClient) -> None:
    resp = _post(
        client, attachments=[{"data": base64.b64encode(PNG).decode(), "name": "inline.png"}]
    )
    assert resp.status_code == 200  # type: ignore[attr-defined]


def test_no_attachments_field_is_unchanged(client: TestClient) -> None:
    """Every existing caller takes this path; the field must be entirely optional."""
    resp = client.post(f"/run/{_topology(client)}", json={"input": "hi"})
    assert resp.status_code == 200


# --- refused at the boundary, with a reason ----------------------------------------------------


def test_missing_file_is_refused_on_the_request(client: TestClient) -> None:
    resp = _post(client, attachments=[{"path": "nope.png"}])
    assert resp.status_code == 422  # type: ignore[attr-defined]
    assert "nope.png" in resp.json()["detail"]  # type: ignore[attr-defined]


def test_path_escaping_the_workspace_is_refused(client: TestClient) -> None:
    resp = _post(client, attachments=[{"path": "../../etc/passwd"}])
    assert resp.status_code == 422  # type: ignore[attr-defined]
    assert "escapes the workspace" in resp.json()["detail"]  # type: ignore[attr-defined]


def test_absolute_path_is_refused(client: TestClient) -> None:
    resp = _post(client, attachments=[{"path": "/etc/passwd"}])
    assert resp.status_code == 422  # type: ignore[attr-defined]


def test_a_type_that_cannot_be_carried_is_refused_before_the_run(client: TestClient) -> None:
    """A PDF is knowable-bad at request time, so it must not cost a job row and a slot."""
    resp = _post(client, attachments=[{"path": "notes.pdf"}])
    assert resp.status_code == 422  # type: ignore[attr-defined]
    assert "application/pdf" in resp.json()["detail"]  # type: ignore[attr-defined]


def test_neither_source_is_refused(client: TestClient) -> None:
    resp = _post(client, attachments=[{"name": "x.png"}])
    assert resp.status_code == 422  # type: ignore[attr-defined]


def test_both_sources_is_refused(client: TestClient) -> None:
    resp = _post(
        client,
        attachments=[{"path": "shots/gate.png", "data": base64.b64encode(PNG).decode()}],
    )
    assert resp.status_code == 422  # type: ignore[attr-defined]


# --- the fields that do not exist, refused as unknown rather than ignored -----------------------


@pytest.mark.parametrize("field", ["type", "media_type"])
def test_declaring_a_type_is_a_422_not_a_silent_ignore(client: TestClient, field: str) -> None:
    """``extra="forbid"``. Ignored silently, a caller would believe their declared type was honoured
    while the bytes said something else."""
    resp = _post(client, attachments=[{"path": "shots/gate.png", field: "image/png"}])
    assert resp.status_code == 422  # type: ignore[attr-defined]


def test_url_source_is_refused(client: TestClient) -> None:
    """Refused on purpose: the runtime does not fetch caller-supplied addresses. Some providers
    accept URLs, so this will look like a missing feature to someone later."""
    resp = _post(client, attachments=[{"url": "https://example.com/x.png"}])
    assert resp.status_code == 422  # type: ignore[attr-defined]


def test_stream_source_is_refused(client: TestClient) -> None:
    """An attachment is read more than once — the tool loop re-sends the history every turn."""
    resp = _post(client, attachments=[{"stream": "fd://3"}])
    assert resp.status_code == 422  # type: ignore[attr-defined]


def test_handling_accepts_only_the_two_intents(client: TestClient) -> None:
    ok = _post(client, attachments=[{"path": "shots/gate.png", "handling": "native"}])
    assert ok.status_code == 200  # type: ignore[attr-defined]
    bad = _post(client, attachments=[{"path": "shots/gate.png", "handling": "magic"}])
    assert bad.status_code == 422  # type: ignore[attr-defined]
