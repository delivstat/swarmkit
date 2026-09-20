"""Who started the current run.

A `ContextVar`, for the same reason `_run_scope.py` holds the run id in one: several jobs run
concurrently in a single ``swarmkit serve`` process, and asyncio copies the context when a task is
created — so each run sees its own caller and two concurrent callers cannot clobber each other.
A module global here would not be a style choice; it would be the bug, and it would look like
Alice's run resolving Bob's token under load.

This exists so a credential declared ``identity: per-user`` can resolve to the token belonging to
the authenticated caller of *this* run (``design/details/per-caller-credential-delegation.md``).
Everything else about credentials is a property of configuration; that one is a property of the
request, and there was previously no seam carrying it to the point of resolution.

The value is the identity's ``client_id`` — the same string ``GET /whoami`` reports and the same
string a portal login records as a token's owner. Keeping those two identical on both paths is the
whole contract: if the login stores one spelling and the run looks up another, every user connects
successfully and every run then reports no token.

Absence is meaningful and must stay distinguishable from emptiness. ``None`` means *there is no
authenticated caller* — a CLI run, a cron trigger, an unauthenticated server — and a per-user
credential refuses rather than falling back to a designated owner. That refusal is the feature:
falling back is how a topology that worked in development quietly serves every caller from the
developer's token.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_principal: ContextVar[str | None] = ContextVar("swarmkit_current_principal", default=None)

__all__ = [
    "current_principal",
    "principal_scope",
    "reset_current_principal",
    "set_current_principal",
]


def current_principal() -> str | None:
    """The authenticated caller of the calling task's run, or None when there is not one.

    None is not an error here — most runs have no caller, and every `identity: global` credential
    resolves the same way regardless. It is only a per-user credential that treats it as fatal.
    """
    return _current_principal.get()


def set_current_principal(client_id: str | None) -> Token[str | None]:
    """Enter a principal scope. Pass the returned token to :func:`reset_current_principal`.

    An empty string is normalised to None: an auth provider that yields a blank identity has not
    identified anybody, and treating "" as a principal would let a per-user credential look up the
    owner "" and find nothing, reporting a missing connection where the truth is missing auth.
    """
    return _current_principal.set(client_id or None)


def reset_current_principal(token: Token[str | None]) -> None:
    """Leave a principal scope, restoring whatever was in effect before."""
    _current_principal.reset(token)


class principal_scope:
    """`with principal_scope(client_id):` — set for the block, restored after.

    The paired set/reset functions exist for callers that start and finish a run in different
    places (the server does); this is for the ones that do not, so the reset cannot be forgotten
    on an exception path.
    """

    __slots__ = ("_client_id", "_token")

    def __init__(self, client_id: str | None) -> None:
        self._client_id = client_id
        self._token: Token[str | None] | None = None

    def __enter__(self) -> str | None:
        self._token = set_current_principal(self._client_id)
        return current_principal()

    def __exit__(self, *_exc: object) -> None:
        if self._token is not None:
            reset_current_principal(self._token)
            self._token = None
