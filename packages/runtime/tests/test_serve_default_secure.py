"""serve default-secure + CORS hardening (review findings: CLI/server D1, D2). The guard
lives in create_app (not just the CLI), and CORS is never wildcard-with-credentials."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from swarmkit_runtime.server import create_app

WS = Path(".")  # the guard + middleware wiring happen before the workspace is resolved


def _has_cors(app: object) -> bool:
    return any(m.cls is CORSMiddleware for m in app.user_middleware)  # type: ignore[attr-defined]


def test_none_auth_non_loopback_is_refused() -> None:
    with pytest.raises(RuntimeError, match="non-loopback"):
        create_app(WS, host="0.0.0.0")


def test_none_auth_loopback_is_allowed() -> None:
    create_app(WS, host="127.0.0.1")  # no raise


def test_insecure_overrides_the_guard() -> None:
    create_app(WS, host="0.0.0.0", insecure=True)  # no raise


def test_no_cors_middleware_without_configured_origins() -> None:
    # Same-origin only by default — no wildcard, no CORS middleware at all.
    assert not _has_cors(create_app(WS, host="127.0.0.1"))


def test_cors_added_only_for_configured_origins() -> None:
    app = create_app(WS, host="127.0.0.1", cors_origins=["https://fleet.example.com"])
    assert _has_cors(app)
