"""Serve-hosted web portal mount (serve-hosted-webui.md, task #27).

`swarmkit serve` mounts the static SPA at `/` as the catch-all — after every API route — with an SPA
fallback, when the swarmkit-webui package is built. Absent, serve stays headless.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import swarmkit_webui
from fastapi import FastAPI
from fastapi.testclient import TestClient
from swarmkit_runtime.server._webui import mount_webui


@pytest.fixture
def portal(tmp_path: Path) -> Path:
    """A minimal built portal: index shell + a real deep-link page + a JS asset."""
    (tmp_path / "index.html").write_text("<html>INDEX-SHELL</html>", encoding="utf-8")
    (tmp_path / "composer").mkdir()
    (tmp_path / "composer" / "index.html").write_text("<html>COMPOSER</html>", encoding="utf-8")
    (tmp_path / "_next").mkdir()
    (tmp_path / "_next" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return tmp_path


def _app_with_portal(monkeypatch: pytest.MonkeyPatch, static: Path | None) -> FastAPI:
    monkeypatch.setattr(swarmkit_webui, "static_dir", lambda: static)
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    mounted = mount_webui(app)
    app.state._webui_mounted = mounted  # type: ignore[attr-defined]
    return app


def test_serves_shell_assets_and_deep_links(
    portal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(_app_with_portal(monkeypatch, portal))

    # root → the shell
    assert "INDEX-SHELL" in client.get("/").text
    # a real deep-link page → its own file (html=True serves the dir index)
    assert "COMPOSER" in client.get("/composer/").text
    # a real asset → served verbatim
    assert client.get("/_next/app.js").text == "console.log(1)"


def test_unknown_path_falls_back_to_the_shell(
    portal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(_app_with_portal(monkeypatch, portal))
    # an unknown non-asset path → the SPA shell (client router then handles/​404s it)
    r = client.get("/some/client/route")
    assert r.status_code == 200
    assert "INDEX-SHELL" in r.text


def test_missing_asset_is_a_real_404(portal: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_app_with_portal(monkeypatch, portal))
    # a path with a file extension that doesn't exist stays a 404 (not the shell)
    assert client.get("/_next/missing.js").status_code == 404


def test_api_routes_win_over_the_static_mount(
    portal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(_app_with_portal(monkeypatch, portal))
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_headless_when_not_built(monkeypatch: pytest.MonkeyPatch) -> None:
    # static_dir None (empty install) ⇒ nothing mounted; the API-only app is unchanged.
    app = _app_with_portal(monkeypatch, None)
    assert app.state._webui_mounted is False
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 404  # no portal, no catch-all
