"""`auth: none` can name its operator, so a local gate is approvable without an identity provider.

Requested against 1.133.0. There was no usable middle ground between the two auth modes:

    none  -> hardcoded `anonymous`, `*` scopes, authorize() returns True unconditionally -> cannot
             resolve a gate
    jwt   -> `sub` from a token, real scopes -> can, but needs an identity provider

`api_key` is not an option at all: `approvals:resolve` is reserved, so `APIKeyAuthProvider` raises
at construction if granted it.

The odd part is that `none` already trusts the caller absolutely — wildcard scopes, and an
`authorize()` that returns True for everything. Nothing is withheld except a name. And the name is
what the approval engine actually checks: it matches `client_id` against `members:` in
`swarm/roles.yaml`, never scopes. So `anonymous` was refused not for lacking authority but for being
nobody, leaving a mode that permits every action unable to perform the one action that depends on
identity — and pushing local operators onto the break-glass event path, which is the path that was
silently dropping reviewer comments until 1.137.0.

Naming the operator grants no capability the mode does not already give.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from swarmkit_runtime.auth._none import DEFAULT_IDENTITY, NoneAuthProvider
from swarmkit_runtime.auth._registry import default_registry
from swarmkit_runtime.governance._approval import Role, RoleRegistry
from swarmkit_runtime.review._multiparty import membership_error


async def _identity(provider: NoneAuthProvider) -> Any:
    return await provider.authenticate(None)  # type: ignore[arg-type]


# ---- the default is untouched --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_default_is_still_anonymous() -> None:
    """Nothing existing moves: a workspace that says nothing gets exactly what it had."""
    identity = await _identity(NoneAuthProvider())
    assert identity.client_id == DEFAULT_IDENTITY
    assert identity.client_name == "Anonymous"
    assert identity.provider == "none"
    assert identity.scopes == frozenset(["*"])


@pytest.mark.asyncio
async def test_blank_configuration_falls_back_to_the_default() -> None:
    """An empty string in YAML must not produce a nameless identity — that would fail every role
    check with a blank name in the audit, which is worse than `anonymous`."""
    for blank in ("", "   ", None):
        identity = await _identity(NoneAuthProvider(identity=blank))  # type: ignore[arg-type]
        assert identity.client_id == DEFAULT_IDENTITY


# ---- naming the operator -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_identity_can_be_named() -> None:
    identity = await _identity(NoneAuthProvider(identity="srijith", identity_name="Srijith Kartha"))
    assert identity.client_id == "srijith"
    assert identity.client_name == "Srijith Kartha"


@pytest.mark.asyncio
async def test_the_display_name_defaults_to_the_identity() -> None:
    identity = await _identity(NoneAuthProvider(identity="srijith"))
    assert identity.client_name == "srijith"


@pytest.mark.asyncio
async def test_naming_grants_no_extra_capability() -> None:
    """The safety argument for the whole feature: this mode already returns True for everything and
    holds wildcard scopes. A named identity must be identical in authority to `anonymous`."""
    anon = NoneAuthProvider()
    named = NoneAuthProvider(identity="srijith")

    assert (await _identity(anon)).scopes == (await _identity(named)).scopes
    assert await named.authorize(await _identity(named), "anything", "anything") is True
    assert await anon.authorize(await _identity(anon), "anything", "anything") is True


@pytest.mark.asyncio
async def test_the_assertion_is_labelled_as_one() -> None:
    """`provider="none"` is what lets an audit reader tell "srijith approved this, verified by an
    IdP" from "someone on the loopback interface claimed to be srijith". A named identity must not
    look authenticated."""
    assert (await _identity(NoneAuthProvider(identity="srijith"))).provider == "none"


# ---- the point of it all: a gate becomes resolvable ---------------------------------------------


@pytest.mark.asyncio
async def test_a_named_operator_can_resolve_a_gate_that_anonymous_cannot() -> None:
    """The gap, end to end. Same registry, same role, same scope — only the name differs."""
    registry = RoleRegistry(
        roles={
            "approver": Role(
                id="approver",
                members=frozenset({"srijith"}),
                scopes=frozenset({"approvals:resolve"}),
            )
        }
    )

    anon = (await _identity(NoneAuthProvider())).client_id
    named = (await _identity(NoneAuthProvider(identity="srijith"))).client_id

    assert membership_error(registry, role="approver", scope="approvals:resolve", identity=anon), (
        "precondition: anonymous is refused, which is the bug being fixed"
    )
    assert (
        membership_error(registry, role="approver", scope="approvals:resolve", identity=named)
        is None
    )


@pytest.mark.asyncio
async def test_a_named_operator_who_is_not_a_member_is_still_refused() -> None:
    """Naming yourself is not the same as being listed. The registry stays the authority."""
    registry = RoleRegistry(
        roles={
            "approver": Role(
                id="approver",
                members=frozenset({"srijith"}),
                scopes=frozenset({"approvals:resolve"}),
            )
        }
    )
    other = (await _identity(NoneAuthProvider(identity="someone-else"))).client_id
    assert membership_error(registry, role="approver", scope="approvals:resolve", identity=other)


# ---- config plumbing -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_registry_factory_passes_the_identity_through() -> None:
    factory = default_registry.get("none")
    assert factory is not None
    provider = factory(identity="srijith", identity_name="Srijith Kartha")
    identity = await provider.authenticate(None)  # type: ignore[arg-type]
    assert (identity.client_id, identity.client_name) == ("srijith", "Srijith Kartha")


@pytest.mark.asyncio
async def test_the_registry_factory_still_works_with_no_arguments() -> None:
    factory = default_registry.get("none")
    assert factory is not None
    identity = await factory().authenticate(None)  # type: ignore[arg-type]
    assert identity.client_id == DEFAULT_IDENTITY


def test_the_workspace_schema_accepts_the_new_keys() -> None:
    from swarmkit_schema import validate  # noqa: PLC0415

    ws = {
        "apiVersion": "swarmkit/v1",
        "kind": "Workspace",
        "metadata": {"id": "w", "name": "W"},
        "server": {
            "auth": {
                "provider": "none",
                "config": {"identity": "srijith", "identity_name": "Srijith Kartha"},
            }
        },
    }
    validate("workspace", ws)  # raises on failure


# ---- guardrails ----------------------------------------------------------------------------------


def test_a_named_identity_does_not_escape_the_loopback_refusal(tmp_path: Any) -> None:
    """The check that keeps this honest. `provider: none` already refuses a non-loopback bind; a
    named identity must not become a way around it — that would turn a local convenience into an
    open door with somebody's name on it."""
    from swarmkit_runtime.server._app import create_app  # noqa: PLC0415

    with pytest.raises(RuntimeError, match="non-loopback"):
        create_app(tmp_path, auth_provider=NoneAuthProvider(identity="srijith"), host="0.0.0.0")


def test_an_identity_matching_no_role_member_warns(caplog: Any) -> None:
    """A 403 from a role check is indistinguishable from being unauthenticated, so an operator
    debugs their login when the real problem is a name."""
    from swarmkit_runtime.server._app import (  # noqa: PLC0415
        _warn_if_identity_is_not_a_role_member,
    )

    class _Workspace:
        role_registry = RoleRegistry(
            roles={
                "approver": Role(id="approver", members=frozenset({"srijith"}), scopes=frozenset())
            }
        )

    class _Runtime:
        workspace = _Workspace()

    with caplog.at_level(logging.WARNING):
        _warn_if_identity_is_not_a_role_member(NoneAuthProvider(identity="typo"), _Runtime())
    assert "typo" in caplog.text
    assert "members:" in caplog.text


def test_a_listed_identity_does_not_warn(caplog: Any) -> None:
    from swarmkit_runtime.server._app import (  # noqa: PLC0415
        _warn_if_identity_is_not_a_role_member,
    )

    class _Workspace:
        role_registry = RoleRegistry(
            roles={
                "approver": Role(id="approver", members=frozenset({"srijith"}), scopes=frozenset())
            }
        )

    class _Runtime:
        workspace = _Workspace()

    with caplog.at_level(logging.WARNING):
        _warn_if_identity_is_not_a_role_member(NoneAuthProvider(identity="srijith"), _Runtime())
    assert not caplog.text


def test_a_workspace_with_no_roles_does_not_warn(caplog: Any) -> None:
    """No roles declared is a legitimate configuration — there is nothing to be inconsistent with,
    and a warning on every local run would train people to ignore warnings."""
    from swarmkit_runtime.server._app import (  # noqa: PLC0415
        _warn_if_identity_is_not_a_role_member,
    )

    class _Runtime:
        workspace = type("_W", (), {"role_registry": RoleRegistry(roles={})})()

    with caplog.at_level(logging.WARNING):
        _warn_if_identity_is_not_a_role_member(NoneAuthProvider(identity="srijith"), _Runtime())
    assert not caplog.text
