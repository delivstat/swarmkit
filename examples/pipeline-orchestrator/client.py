"""A minimal HTTP client for `swarmkit serve` — the only SwarmKit-shaped code in this app.

`httpx` and a bearer token. Separated from the orchestrator so the sequencing logic can be driven by
a fake in tests, and so it is obvious how little SwarmKit-specific surface an application needs:
five endpoints.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class ServeClient:
    """`swarmkit serve` over HTTP.

    The five calls a sequencer needs:

    * ``POST /run/{topology}``     — start a stage
    * ``GET  /jobs/{id}``          — watch it
    * ``GET  /review``             — find the gate a parked run is waiting on
    * ``GET  /gates/{id}``         — is that gate resolved, with the policy applied
    * ``POST /jobs/{id}/resume``   — continue after approval

    Plus ``GET /jobs/{id}/diff`` and ``GET /artifacts/{ref}`` for what a run produced.
    """

    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def get(self, path: str, **params: Any) -> Any:
        response = self._http.get(path, params=params or None)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, json: Mapping[str, Any] | None = None) -> Any:
        response = self._http.post(path, json=dict(json or {}))
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ServeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
