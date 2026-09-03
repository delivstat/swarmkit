"""Resolving an MCP server's credentials — the field that used to reach nothing.

`credentials_ref` was in the schema, parsed into `MCPServerConfig`, and read by no code on either
transport. Worse, `env`'s own description told authors *"Use `credentials_ref` for secrets"* — so a
server configured exactly as documented connected with **no credentials at all**, and found out at
connect time with an auth error that named nothing.

This is the module that makes it true. Two shapes, because the transports differ in what a secret
has to become:

* **http** — a resolved secret becomes `Authorization: Bearer <secret>`, which is what an
  OAuth-protected remote MCP server expects.
* **stdio** — the server is a subprocess, so a secret has to arrive as an environment variable, and
  only the author knows which one. `{credential.<ref>}` in an `env` value says so explicitly. That
  is the same substitution command packs use, for the same reason: the alternative is a naming
  convention the server has never heard of.

**`${VAR}` and `{credential.…}` are not equivalent, and the difference is the point.** `${VAR}`
copies a value the runtime already holds in its own environment; `{credential.…}` resolves through
the workspace `credentials` block, so the secret can come from a file the runtime never reads into
its environment — and cannot then leak into an unrelated subprocess.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CREDENTIAL = re.compile(r"\{credential\.([A-Za-z0-9_.:/-]+)\}")


class CredentialError(Exception):
    """A declared credential could not be resolved. Loud on purpose: a server that starts without
    the secret it declared will fail later, further from the cause."""


def _resolve_ref(ref: str, credentials: Mapping[str, Any] | None) -> str | None:
    from swarmkit_runtime.auth._secrets import resolve_secret_ref  # noqa: PLC0415

    return resolve_secret_ref(ref, dict(credentials or {}))


def substitute(value: str, credentials: Mapping[str, Any] | None) -> str:
    """Expand ``${VAR}`` and ``{credential.<ref>}`` in one config value.

    A `{credential.…}` that cannot be resolved raises rather than becoming an empty string. An
    empty Authorization header is a request that fails for a reason nobody can read; a raised error
    names the ref.
    """
    out = _ENV_VAR.sub(lambda m: os.environ.get(m.group(1), ""), value)

    def _one(match: re.Match[str]) -> str:
        ref = match.group(1)
        # A bare name means an entry in the workspace `credentials` block; a prefixed one
        # (env:, file:, credentials:) is passed through to the resolver as written.
        lookup = ref if ":" in ref else f"credentials:{ref}"
        try:
            secret = _resolve_ref(lookup, credentials)
        except Exception as exc:
            raise CredentialError(f"credential {ref!r} could not be resolved: {exc}") from exc
        if not secret:
            raise CredentialError(
                f"credential {ref!r} resolved to nothing — check the workspace `credentials` entry "
                f"or the environment variable it points at"
            )
        return secret

    return _CREDENTIAL.sub(_one, out)


def resolve_env(
    env: Mapping[str, str] | None, credentials: Mapping[str, Any] | None
) -> dict[str, str] | None:
    """A stdio server's environment, with both substitutions applied."""
    if not env:
        return None
    return {k: substitute(str(v), credentials) for k, v in env.items()}


def resolve_headers(
    *,
    credentials_ref: str,
    headers: Mapping[str, str] | None,
    credentials: Mapping[str, Any] | None,
) -> dict[str, str]:
    """The HTTP headers a remote server should be called with.

    `credentials_ref` becomes a bearer token; explicit `headers` are substituted and applied on
    top. An explicit Authorization wins — a server using a non-bearer scheme must be able to say so
    without the derived header silently overriding it.
    """
    out: dict[str, str] = {}
    if credentials_ref:
        try:
            secret = _resolve_ref(credentials_ref, credentials)
        except CredentialError:
            raise
        except Exception as exc:
            # The resolver raises ValueError for a missing entry. Wrapped so every failure on this
            # path arrives as one type naming the ref — a caller should not have to know which
            # layer failed to report which credential is wrong.
            raise CredentialError(
                f"credentials_ref {credentials_ref!r} could not be resolved: {exc}"
            ) from exc
        if not secret:
            raise CredentialError(
                f"credentials_ref {credentials_ref!r} resolved to nothing — the server would be "
                f"called with no Authorization header at all"
            )
        out["Authorization"] = f"Bearer {secret}"
    for key, value in (headers or {}).items():
        out[key] = substitute(str(value), credentials)
    return out


__all__ = ["CredentialError", "resolve_env", "resolve_headers", "substitute"]
