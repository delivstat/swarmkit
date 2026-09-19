"""serve --profile production: the fail-closed startup preflight (production-profile.md).

Refuses to start unless the deployment is production-safe, reporting every gap at once.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.auth import APIKeyAuthProvider, NoneAuthProvider
from swarmkit_runtime.server._production_profile import (
    assert_production_ready,
    production_violations,
)

_WS_HEAD = "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: t, name: T}\n"


def _ws(tmp_path: Path, *, mcp: str = "") -> Path:
    (tmp_path / "workspace.yaml").write_text(_WS_HEAD + mcp)
    return tmp_path


def _ok_auth() -> APIKeyAuthProvider:
    return APIKeyAuthProvider(keys=[], credentials={})


def test_all_violations_reported_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_OAUTH_KEY", raising=False)
    mcp = (
        "mcp_servers:\n"
        "  - id: files\n    transport: stdio\n    command: x\n"  # not sandboxed
    )
    gaps = production_violations(
        _ws(tmp_path, mcp=mcp),
        auth=NoneAuthProvider(),
        insecure=True,
        cors_origins=["*"],
    )
    joined = "\n".join(gaps)
    assert "auth provider is 'none'" in joined
    assert "--insecure" in joined
    assert "SWARMKIT_OAUTH_KEY" in joined
    assert "wildcard CORS" in joined
    assert "MCP server 'files' is not sandboxed" in joined
    assert len(gaps) == 5


def test_fully_configured_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_OAUTH_KEY", "a-persistent-key")
    mcp = "mcp_servers:\n  - id: files\n    transport: stdio\n    command: x\n    sandboxed: true\n"
    gaps = production_violations(
        _ws(tmp_path, mcp=mcp),
        auth=_ok_auth(),
        insecure=False,
        cors_origins=["https://app.example.com"],
    )
    assert gaps == []


def test_only_missing_oauth_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_OAUTH_KEY", raising=False)
    gaps = production_violations(_ws(tmp_path), auth=_ok_auth(), insecure=False, cors_origins=None)
    assert len(gaps) == 1 and "SWARMKIT_OAUTH_KEY" in gaps[0]


def test_only_anonymous_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_OAUTH_KEY", "k")
    gaps = production_violations(
        _ws(tmp_path), auth=NoneAuthProvider(), insecure=False, cors_origins=None
    )
    assert len(gaps) == 1 and "auth provider is 'none'" in gaps[0]


def test_assert_raises_with_numbered_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_OAUTH_KEY", raising=False)
    with pytest.raises(RuntimeError, match="not fail-closed"):
        assert_production_ready(
            _ws(tmp_path), auth=NoneAuthProvider(), insecure=False, cors_origins=None
        )


def test_assert_is_silent_when_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_OAUTH_KEY", "k")
    assert_production_ready(_ws(tmp_path), auth=_ok_auth(), insecure=False, cors_origins=None)


def test_create_app_refuses_permissive_workspace_under_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    monkeypatch.delenv("SWARMKIT_OAUTH_KEY", raising=False)
    (tmp_path / "topologies").mkdir()
    _ws(tmp_path)  # no auth configured, no oauth key -> not production-ready
    with pytest.raises(RuntimeError, match="not fail-closed"):
        create_app(tmp_path, profile="production")


def test_create_app_standard_profile_unaffected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The permissive default still builds on a loopback workspace with no auth."""
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    (tmp_path / "topologies").mkdir()
    _ws(tmp_path)
    app = create_app(tmp_path)  # profile="standard" default
    assert app is not None
