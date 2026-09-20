"""A per-user connection's session is never handed to another caller.

The resolver returning the right token is not enough. `MCPClientManager` consults its session cache
*before* resolving anything, so a cache keyed by server id alone would hand Alice's open session —
carrying Alice's bearer — to Bob's run, and Bob would act as Alice. The resolver cannot see it
happen; it already did its job for both of them.

That makes this a keying bug rather than a wiring one, which is why these tests sit beside the
client rather than in an end-to-end tier: the property is about what the cache may be keyed by, and
it is cheapest to state exactly there. See design/details/per-caller-credential-delegation.md.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from swarmkit_runtime._principal import principal_scope
from swarmkit_runtime.mcp._client import MCPClientManager


class _Credentials:
    """The slice of CredentialService the session cache actually asks for."""

    def __init__(self, per_user: set[str]) -> None:
        self._per_user = per_user

    def is_per_user(self, name: str) -> bool:
        return name in self._per_user


def _manager(per_user: set[str]) -> MCPClientManager:
    manager = MCPClientManager(credential_service=_Credentials(per_user))
    configs: dict[str, Any] = {
        "calendar": SimpleNamespace(server_id="calendar", credentials_ref="google-calendar"),
        "github": SimpleNamespace(server_id="github", credentials_ref="github"),
        "local": SimpleNamespace(server_id="local", credentials_ref=""),
    }
    manager._configs = configs
    return manager


def test_a_per_user_server_keys_a_session_per_caller() -> None:
    manager = _manager({"google-calendar"})

    with principal_scope("alice"):
        alice = manager._session_key("calendar")
    with principal_scope("bob"):
        bob = manager._session_key("calendar")

    assert alice != bob, "two callers must not share one per-user session"
    assert "alice" in alice and "bob" in bob


def test_a_global_server_keeps_one_session_for_everyone() -> None:
    """Today's behaviour, and the reason `global` stays cheap: one warm session per server."""
    manager = _manager({"google-calendar"})

    with principal_scope("alice"):
        alice = manager._session_key("github")
    with principal_scope("bob"):
        bob = manager._session_key("github")

    assert alice == bob == "github"


def test_a_server_with_no_credential_is_never_per_user() -> None:
    manager = _manager({"google-calendar"})
    with principal_scope("alice"):
        assert manager._session_key("local") == "local"


def test_an_unanswerable_credential_service_is_treated_as_shared() -> None:
    """The conservative direction is *shared*, not *per-user* — but only for the key.

    Guessing per-user would merely cost an extra session. Guessing shared costs nothing here
    either, because a credential that is really per-user still refuses to resolve without a
    principal: the resolver, not the cache, is what enforces the security property. The cache only
    has to avoid *collapsing* two owners onto one session, and it cannot do that for a connection
    the service says nothing about.
    """

    class _Broken:
        def is_per_user(self, name: str) -> bool:
            raise RuntimeError("no workspace loaded")

    manager = MCPClientManager(credential_service=_Broken())
    broken_configs: dict[str, Any] = {"x": SimpleNamespace(server_id="x", credentials_ref="c")}
    manager._configs = broken_configs
    with principal_scope("alice"):
        assert manager._session_key("x") == "x"


def test_no_principal_still_keys_distinctly_from_a_named_caller() -> None:
    """An unauthenticated run must not land on a key an authenticated caller could also produce.

    It will fail to resolve a moment later — per-user with no caller is refused — but it must not
    pick up somebody's warm session on the way there.
    """
    manager = _manager({"google-calendar"})

    anonymous = manager._session_key("calendar")
    with principal_scope("alice"):
        alice = manager._session_key("calendar")

    assert anonymous != alice
