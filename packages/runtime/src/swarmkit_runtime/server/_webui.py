"""Mount the static web portal on ``swarmkit serve`` (serve-hosted-webui.md).

The portal (``swarmkit-webui``, the ``[ui]`` extra) is a static SPA. When it is importable, we mount
it at ``/`` as the catch-all — *after* every API route, so the API always wins — with an SPA
fallback so a deep link (e.g. ``/composer/``) serves the app shell and the client router takes over.
Absent, serve runs headless (API only), unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("swarmkit.serve")


def _spa_staticfiles() -> type:
    """Build a StaticFiles subclass that falls back to ``index.html`` on a 404 (SPA client routing).
    Defined lazily so the runtime imports without starlette when serve isn't used."""
    from starlette.exceptions import HTTPException  # noqa: PLC0415
    from starlette.staticfiles import StaticFiles  # noqa: PLC0415

    class _SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope: Any) -> Any:
            try:
                return await super().get_response(path, scope)
            except HTTPException as exc:
                # An unknown non-asset path → serve the app shell so the client router handles it.
                # A missing asset (has a file extension) stays a real 404.
                if exc.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                    return await super().get_response("index.html", scope)
                raise

    return _SPAStaticFiles


def _add_navigation_redirect(app: FastAPI, static: str) -> None:
    """Disambiguate page routes that collide with API endpoints (e.g. ``/jobs`` — a page *and* the
    job-list endpoint). The portal is a static export with ``trailingSlash``, so each page lives at
    ``/<route>/``; the API owns the bare ``/<route>``. A browser *navigation* sends ``Accept:
    text/html``, so for such a request to a bare path that has a built page
    (``<static>/<route>/index.html``) we 307 to the slash form and the SPA serves it. A ``fetch()``
    (``Accept: */*``) is untouched — the data API still wins. Runs before routing, so it beats the
    colliding endpoint."""
    from collections.abc import Awaitable, Callable  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import RedirectResponse, Response  # noqa: PLC0415

    root = Path(static)

    @app.middleware("http")
    async def _spa_navigation_redirect(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in ("GET", "HEAD"):
            path = request.url.path
            if (
                "text/html" in request.headers.get("accept", "")
                and not path.endswith("/")
                and (root / path.lstrip("/") / "index.html").is_file()
            ):
                target = f"{path}/"
                if request.url.query:
                    target = f"{target}?{request.url.query}"
                return RedirectResponse(target, status_code=307)
        return await call_next(request)


def mount_webui(app: FastAPI) -> bool:
    """Mount the static portal at ``/`` if the ``swarmkit-webui`` package is installed and built.
    Returns whether it was mounted. Call **last** in the app factory (after all API routes)."""
    try:
        import swarmkit_webui  # noqa: PLC0415
    except ModuleNotFoundError:
        return False
    static = swarmkit_webui.static_dir()
    if static is None:
        return False
    _add_navigation_redirect(app, str(static))
    app.mount("/", _spa_staticfiles()(directory=str(static), html=True), name="webui")
    logger.info("web portal mounted at / (swarmkit-webui %s)", swarmkit_webui.__version__)
    return True


__all__ = ["mount_webui"]
