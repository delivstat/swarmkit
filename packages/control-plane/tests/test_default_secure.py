"""Default-secure: an unauthenticated panel can mint serve tokens + deploy artifacts, so it
refuses to start open on a non-loopback bind unless --insecure-no-auth (review finding: S1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_control_plane import SqliteRegistry, create_app


def _reg(tmp_path: Path) -> SqliteRegistry:
    return SqliteRegistry(tmp_path / "registry.sqlite")


async def _verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
    return {}


def test_open_on_non_loopback_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="unauthenticated"):
        create_app(_reg(tmp_path), verify=_verify, host="0.0.0.0")


def test_open_on_loopback_is_allowed(tmp_path: Path) -> None:
    # dev convenience — loopback stays open
    create_app(_reg(tmp_path), verify=_verify, host="127.0.0.1")


def test_non_loopback_with_auth_is_allowed(tmp_path: Path) -> None:
    create_app(_reg(tmp_path), verify=_verify, host="0.0.0.0", operator_tokens=["op"])


def test_insecure_flag_overrides(tmp_path: Path) -> None:
    create_app(_reg(tmp_path), verify=_verify, host="0.0.0.0", allow_open=True)
