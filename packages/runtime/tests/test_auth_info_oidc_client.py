"""`/auth-info` advertises the browser's OIDC client registration.

Reported against swarmkit-webui 0.6.1 / runtime 1.133.0. The portal read `client_id` from
`NEXT_PUBLIC_OIDC_CLIENT_ID`, which Next.js inlines at BUILD time. In the published artifact it was
never inlined — the bundle carried the literal source text `NEXT_PUBLIC_OIDC_CLIENT_ID)?t:""`, a
live property read against the browser `process` polyfill whose `env` is `{}` — so it evaluated to
`""` on every load and `signinRedirect()` went out with `client_id=`. Every IdP rejects that.

There was no runtime escape hatch: the static export contains no `env.js`, no `window.__ENV`. So the
published wheel could not be pointed at *any* identity provider. An operator who configured
`provider: jwt` correctly — valid issuer, reachable JWKS, correct audience — still could not
sign in, with nothing in the UI or the server log explaining why.

The server was the one place that could answer. `/auth-info` already exists so "a client renders
the right login gate before it holds a token", and already carries `issuer` and `audience`; the
client id is part of the same answer. Serving it makes it workspace configuration, so one published
wheel works for every deployment.

The server never validates against these values — it advertises them. Token validation is unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("jwt")

from swarmkit_runtime.auth._jwt import JWTAuthProvider
from swarmkit_runtime.auth._registry import default_registry

ISSUER = "https://id.example.com/application/o/swarmkit/"


def _provider(**kw: Any) -> JWTAuthProvider:
    return JWTAuthProvider(issuer=ISSUER, audience="swarmkit", **kw)


def test_client_id_is_advertised_when_configured() -> None:
    """The fix, at its narrowest: the value the browser cannot obtain any other way."""
    info = _provider(client_id="swarmkit-portal").public_info()
    assert info["oidc"]["client_id"] == "swarmkit-portal"


def test_scope_is_advertised_when_configured() -> None:
    info = _provider(scope="openid profile email swarmkit").public_info()
    assert info["oidc"]["scope"] == "openid profile email swarmkit"


def test_nothing_is_advertised_when_nothing_is_configured() -> None:
    """An unset client_id must be ABSENT, not an empty string. The client falls back to its
    build-time env var when the key is missing; an empty string would override it with nothing."""
    oidc = _provider().public_info()["oidc"]
    assert "client_id" not in oidc
    assert "scope" not in oidc


def test_issuer_and_audience_still_travel() -> None:
    """Guard: the keys the portal already depended on must not move."""
    oidc = _provider(client_id="swarmkit-portal").public_info()["oidc"]
    assert oidc["issuer"] == ISSUER
    assert oidc["audience"] == "swarmkit"


def test_server_side_validation_details_stay_server_side() -> None:
    """`/auth-info` is UNAUTHENTICATED. It must advertise only what a browser needs to start a login
    — never how the server validates what comes back."""
    oidc = _provider(client_id="swarmkit-portal", scope="openid").public_info()["oidc"]
    assert "jwks_url" not in oidc
    assert "scopes_claim" not in oidc
    assert set(oidc) <= {"issuer", "audience", "client_id", "scope"}


def test_the_registry_factory_passes_them_through() -> None:
    factory = default_registry.get("jwt")
    assert factory is not None
    provider = factory(issuer=ISSUER, audience="swarmkit", client_id="swarmkit-portal")
    assert provider.public_info()["oidc"]["client_id"] == "swarmkit-portal"


def test_the_registry_factory_works_without_them() -> None:
    """Existing workspaces configure neither."""
    factory = default_registry.get("jwt")
    assert factory is not None
    assert "client_id" not in factory(issuer=ISSUER).public_info()["oidc"]


def test_the_workspace_schema_accepts_the_new_keys() -> None:
    from swarmkit_schema import validate  # noqa: PLC0415

    validate(
        "workspace",
        {
            "apiVersion": "swarmkit/v1",
            "kind": "Workspace",
            "metadata": {"id": "w", "name": "W"},
            "server": {
                "auth": {
                    "provider": "jwt",
                    "config": {
                        "issuer": ISSUER,
                        "audience": "swarmkit",
                        "client_id": "swarmkit-portal",
                        "scope": "openid profile email",
                    },
                }
            },
        },
    )
