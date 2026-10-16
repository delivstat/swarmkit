"""An importable ASGI factory for serving the SAME app under different HTTP servers (Run 7).

`swarmkit serve` builds its app in-process and hands the object to uvicorn. Granian (and uvicorn's
own `--factory` mode) import a target by module path instead, so this rebuilds the app the way
`serve` does — `create_app` with the workspace's auth provider — from environment variables:

    LT_WORKSPACE    workspace path (required)
    LT_ROLE         all | api (default all) — api is `serve --role api`: accept + enqueue only
    LT_QUEUE_DEPTH  `--max-queue-depth` for role api (default 10000)

    uvicorn --factory --app-dir examples/loadtest asgi_app:app --port 8125
    granian --interface asgi --factory --working-dir examples/loadtest asgi_app:app --port 8125

Loopback-only and `insecure=True`, like the rest of the loadtest harness: it measures the server,
not the auth layer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def app() -> Any:
    from swarmkit_runtime.cli._cmd_serve import _build_auth_provider  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    ws = Path(os.environ["LT_WORKSPACE"]).resolve()
    return create_app(
        ws,
        auth_provider=_build_auth_provider(ws),
        host="127.0.0.1",
        insecure=True,
        enqueue_only=os.environ.get("LT_ROLE", "all") == "api",
        queue_max_depth=int(os.environ.get("LT_QUEUE_DEPTH", "10000")),
    )
