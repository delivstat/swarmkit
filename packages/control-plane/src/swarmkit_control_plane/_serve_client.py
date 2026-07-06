"""ServeClient — the panel's typed async HTTP client for talking to an instance's serve API.

Every panel→instance call (verify at enrollment, federated job query, drive an authoring/eval run,
governed deploy) shares the same boilerplate: resolve the token reference, build the bearer header,
join the base URL, open an ``httpx.AsyncClient``, and map a 4xx/5xx into a :class:`ConnectorError`.
This centralises that plumbing so ``_connector`` / ``_deploy`` express only *what* they call.

Standalone by design (design D1): the panel depends on nothing in the runtime, so this lives here
rather than in a shared package. The verb→tier contract it drives is kept in lock-step with the
runtime connector by a cross-package contract test (``tests/test_verb_contract.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx


class ConnectorError(Exception):
    """Raised when the panel cannot reach, authenticate to, or get a good status from serve."""


def resolve_secret_ref(ref: str) -> str | None:
    """Resolve a secret reference to its value — ``env:NAME`` / ``file:/path`` / a literal.

    Returns None for an empty ref or an unreadable file. Only a *reference* is ever stored by the
    panel; the resolved secret is used transiently to authenticate a single call.
    """
    if not ref:
        return None
    if ref.startswith("env:"):
        return os.environ.get(ref[4:])
    if ref.startswith("file:"):
        try:
            return Path(ref[5:]).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return ref


class ServeClient:
    """Async client bound to one instance's serve endpoint + token reference.

    Use as an async context manager::

        async with ServeClient(endpoint, token_ref) as serve:
            body = serve.ok(await serve.get("/capabilities"), "/capabilities")

    ``get``/``post``/``put`` send the bearer header (when a token resolves) and never raise on an
    HTTP status; call ``ok(resp, what)`` to assert a good status + parse JSON, or inspect
    ``resp.status_code`` directly when a handler wants per-code behaviour.
    """

    def __init__(
        self,
        endpoint: str,
        token_ref: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = endpoint.rstrip("/")
        token = resolve_secret_ref(token_ref)
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._timeout = timeout
        # An injected transport (httpx.MockTransport) makes the client testable without a network.
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ServeClient:
        self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:  # pragma: no cover - misuse guard
            raise RuntimeError("ServeClient must be used as an async context manager")
        return self._client

    def url(self, path: str) -> str:
        return f"{self._base}{path}"

    async def get(self, path: str, *, auth: bool = True) -> httpx.Response:
        """GET a serve path. Transport errors become ConnectorError; HTTP status is the caller's."""
        try:
            return await self._http().get(self.url(path), headers=self._headers if auth else {})
        except httpx.HTTPError as exc:
            raise ConnectorError(f"cannot reach {self._base}: {exc}") from exc

    async def post(self, path: str, json: Any) -> httpx.Response:
        try:
            return await self._http().post(self.url(path), json=json, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"cannot reach {self._base}: {exc}") from exc

    async def put(self, path: str, json: Any) -> httpx.Response:
        try:
            return await self._http().put(self.url(path), json=json, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"cannot reach {self._base}: {exc}") from exc

    def ok(self, resp: httpx.Response, what: str) -> Any:
        """Assert a 2xx and return the parsed JSON body; raise ConnectorError otherwise.

        401/403 get a token-specific message; other non-2xx report the code + a body snippet.
        """
        if resp.status_code in (401, 403):
            raise ConnectorError(f"{what} auth failed ({resp.status_code}) — check the token")
        if resp.status_code >= 400:
            raise ConnectorError(f"{what} returned {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:
            return {"text": resp.text}
