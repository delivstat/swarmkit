"""Panel-side register handshake + encrypted credential storage (design 19, Phase 2 slice 3).

The panel joins an instance with an enrollment token, stores the returned membership secret
**encrypted at rest** (never plaintext), and caches the instance's full state — in one call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._credential_store import CredentialStore
from swarmkit_control_plane._secret_box import FernetSecretBox
from swarmkit_control_plane._tables import instance_credential

_REGISTER_RESULT = {
    "membership_id": "mem-123",
    "credential": {
        "type": "api_key",
        "value": "super-secret-key",
        "scope": "manage",
        "fingerprint": "abc123",
    },
    "instance_state": {
        "workspace_id": "sterling-oms",
        "schema_version": "1.7.0",
        "artifacts": {"topologies": [{"id": "t1"}], "skills": [], "archetypes": [], "triggers": []},
    },
}


# --- SecretBox + CredentialStore ------------------------------------------------


def test_secret_box_roundtrip_and_ciphertext_differs() -> None:
    box = FernetSecretBox(Fernet.generate_key())
    ct = box.encrypt("hunter2")
    assert ct != "hunter2"
    assert box.decrypt(ct) == "hunter2"


def test_credential_store_encrypts_at_rest(tmp_path: Path) -> None:
    box = FernetSecretBox(Fernet.generate_key())
    store = CredentialStore(tmp_path / "cp.sqlite", secret_box=box)
    store.put_credential(
        "i1",
        membership_id="m1",
        fleet_id="f1",
        scope="monitor",
        fingerprint="fp",
        secret="the-secret",
    )
    # the raw row holds ciphertext, not the secret.
    from sqlalchemy import select  # noqa: PLC0415

    with store.engine.connect() as conn:
        row = conn.execute(select(instance_credential)).mappings().first()
    assert row is not None
    assert "the-secret" not in str(dict(row))
    assert box.decrypt(row["ciphertext"]) == "the-secret"
    # the store decrypts on read; metadata never exposes the secret.
    assert store.get_secret("i1") == "the-secret"
    assert "secret" not in store.get_metadata("i1")  # type: ignore[operator]
    assert store.get_metadata("i1")["scope"] == "monitor"  # type: ignore[index]


# --- the /register route --------------------------------------------------------


def _client(
    tmp_path: Path,
    refresh_calls: list[str] | None = None,
    leave_calls: list[tuple[str, str]] | None = None,
) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def register_fn(
        endpoint: str, enroll_token: str, fleet_id: str, requested_scope: str | None
    ) -> dict[str, Any]:
        return _REGISTER_RESULT

    async def refresh_fn(endpoint: str, membership_key: str) -> dict[str, Any]:
        if refresh_calls is not None:
            refresh_calls.append(membership_key)  # capture the (decrypted) key the panel used
        return {
            "membership_id": "mem-123",
            "credential": {
                "type": "api_key",
                "value": "rotated-key",
                "scope": "manage",
                "fingerprint": "def456",
            },
        }

    async def leave_fn(endpoint: str, membership_key: str, membership_id: str) -> dict[str, Any]:
        if leave_calls is not None:
            leave_calls.append((membership_key, membership_id))  # (decrypted key, id) used
        return {"ejected": membership_id}

    return TestClient(
        create_app(
            registry,
            verify=verify,
            register_fn=register_fn,
            refresh_fn=refresh_fn,
            leave_fn=leave_fn,
        )
    )


def _enroll(client: TestClient, connection: str = "direct") -> str:
    r = client.post(
        "/instances",
        json={
            "name": "edge",
            "endpoint": "http://edge:8000",
            "connection": connection,
            "tier": "read",
        },
    )
    return str(r.json()["id"])


def test_register_stores_credential_caches_state_and_marks_healthy(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client)
    r = client.post(f"/instances/{iid}/register", json={"enroll_token": "join-code"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["membership_id"] == "mem-123" and body["scope"] == "manage"
    assert body["counts"]["topologies"] == 1
    # state cached (works offline) + instance now healthy.
    assert client.get(f"/instances/{iid}/state").json()["state"]["workspace_id"] == "sterling-oms"
    assert client.get(f"/instances/{iid}").json()["health"] == "healthy"


def test_register_rejects_poll_mode(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client, connection="poll")
    assert client.post(f"/instances/{iid}/register", json={"enroll_token": "x"}).status_code == 409


def test_register_unknown_instance_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/instances/nope/register", json={"enroll_token": "x"}).status_code == 404


def test_refresh_decrypts_uses_and_re_stores_the_credential(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(tmp_path, refresh_calls=calls)
    iid = _enroll(client)
    client.post(f"/instances/{iid}/register", json={"enroll_token": "join"})

    r = client.post(f"/instances/{iid}/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["fingerprint"] == "def456"  # rotated fingerprint returned
    # the panel decrypted the stored credential and used it as the key.
    assert calls == ["super-secret-key"]
    # the rotated key is now stored (encrypted) — a second refresh uses the NEW key.
    client.post(f"/instances/{iid}/refresh")
    assert calls == ["super-secret-key", "rotated-key"]


def test_refresh_before_register_is_400(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client)
    assert client.post(f"/instances/{iid}/refresh").status_code == 400


# --- membership visibility + leave (design 20, Phase 3 slice 3) --------------


def test_get_membership_returns_this_fleets_metadata(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client)
    # 404 before registering — this fleet holds no membership yet.
    assert client.get(f"/instances/{iid}/membership").status_code == 404
    client.post(f"/instances/{iid}/register", json={"enroll_token": "join"})
    meta = client.get(f"/instances/{iid}/membership").json()
    assert meta["membership_id"] == "mem-123" and meta["scope"] == "manage"
    assert "secret" not in meta and "ciphertext" not in meta  # never a secret


def test_leave_revokes_with_the_key_and_forgets_the_credential(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    client = _client(tmp_path, leave_calls=calls)
    iid = _enroll(client)
    client.post(f"/instances/{iid}/register", json={"enroll_token": "join"})

    r = client.delete(f"/instances/{iid}/membership")
    assert r.status_code == 200, r.text
    assert r.json()["membership_id"] == "mem-123"
    # the panel self-left using the DECRYPTED membership key + its id.
    assert calls == [("super-secret-key", "mem-123")]
    # the stored credential is forgotten — membership is gone, a second leave is 404.
    assert client.get(f"/instances/{iid}/membership").status_code == 404
    assert client.delete(f"/instances/{iid}/membership").status_code == 404


def test_leave_without_membership_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = _enroll(client)
    assert client.delete(f"/instances/{iid}/membership").status_code == 404
