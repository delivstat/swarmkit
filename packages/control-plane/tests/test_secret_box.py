"""SecretBox backends — local Fernet + Vault/OpenBao Transit (design 19, Phase 2).

The Transit box is unit-tested against an httpx MockTransport that mimics the Transit contract
(base64 plaintext ↔ ``vault:v1:…`` ciphertext), and — when ``SWARMKIT_TEST_VAULT_ADDR`` is set —
against a real vault (deselected by default, like the Postgres suite).
"""

from __future__ import annotations

import base64
import json
import os

import httpx
import pytest
from cryptography.fernet import Fernet
from swarmkit_control_plane._secret_box import (
    FernetSecretBox,
    SecretBoxError,
    VaultTransitSecretBox,
    default_secret_box,
)

# --- local Fernet ---------------------------------------------------------------


def test_fernet_roundtrip() -> None:
    box = FernetSecretBox(Fernet.generate_key())
    assert box.decrypt(box.encrypt("secret")) == "secret"


# --- Vault Transit (mocked) -----------------------------------------------------


def _fake_transit(seen: list[httpx.Request]) -> httpx.MockTransport:
    """A stand-in Transit engine: encrypt wraps base64 plaintext, decrypt unwraps it."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if "/v1/transit/encrypt/" in request.url.path:
            return httpx.Response(
                200, json={"data": {"ciphertext": "vault:v1:" + body["plaintext"]}}
            )
        if "/v1/transit/decrypt/" in request.url.path:
            b64 = body["ciphertext"].split("vault:v1:", 1)[1]
            return httpx.Response(200, json={"data": {"plaintext": b64}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _box(seen: list[httpx.Request]) -> VaultTransitSecretBox:
    return VaultTransitSecretBox(
        addr="http://vault:8200", token="t", key_name="k", transport=_fake_transit(seen)
    )


def test_transit_roundtrip() -> None:
    seen: list[httpx.Request] = []
    box = _box(seen)
    assert box.decrypt(box.encrypt("hunter2")) == "hunter2"


def test_transit_encrypt_sends_base64_to_the_key_path() -> None:
    seen: list[httpx.Request] = []
    ct = _box(seen).encrypt("hunter2")
    assert ct.startswith("vault:v1:")
    req = seen[0]
    assert req.url.path == "/v1/transit/encrypt/k"
    assert req.headers["x-vault-token"] == "t"
    assert json.loads(req.content)["plaintext"] == base64.b64encode(b"hunter2").decode()


def test_transit_raises_on_error_status() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="permission denied")

    box = VaultTransitSecretBox(
        addr="http://vault:8200", token="t", key_name="k", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(SecretBoxError):
        box.encrypt("x")


# --- factory selection ----------------------------------------------------------


def test_factory_selects_vault_when_addr_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_CONTROL_PLANE_VAULT_ADDR", "http://vault:8200")
    monkeypatch.setenv("SWARMKIT_CONTROL_PLANE_VAULT_TOKEN", "t")
    assert isinstance(default_secret_box(), VaultTransitSecretBox)


def test_factory_selects_fernet_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_CONTROL_PLANE_VAULT_ADDR", raising=False)
    monkeypatch.setenv("SWARMKIT_CONTROL_PLANE_SECRET_KEY", Fernet.generate_key().decode())
    assert isinstance(default_secret_box(), FernetSecretBox)


# --- real vault (gated) ---------------------------------------------------------


@pytest.mark.integration
def test_transit_against_real_vault() -> None:
    """Roundtrip against a real Vault/OpenBao Transit engine — runs only when the vault env is set
    (deselected by default). Ensures the transit key exists, then encrypt→decrypt."""
    addr = os.environ.get("SWARMKIT_TEST_VAULT_ADDR")
    token = os.environ.get("SWARMKIT_TEST_VAULT_TOKEN")
    if not (addr and token):
        pytest.skip(
            "set SWARMKIT_TEST_VAULT_ADDR + SWARMKIT_TEST_VAULT_TOKEN to run the vault test"
        )
    key = "swarmkit-control-plane-test"
    with httpx.Client(base_url=addr, headers={"X-Vault-Token": token}, timeout=10) as c:
        c.post(
            "/v1/sys/mounts/transit", json={"type": "transit"}
        )  # idempotent-ish; ignore 400 if mounted
        c.post(f"/v1/transit/keys/{key}", json={})
    box = VaultTransitSecretBox(addr=addr, token=token, key_name=key)
    assert box.decrypt(box.encrypt("real-secret")) == "real-secret"
