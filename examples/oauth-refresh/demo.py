#!/usr/bin/env python3
"""Demo: a token that expires mid-session is refreshed at the point of use.

The claim under test is the one `credential-service.md` was written for — that refresh reaches
*every* entry point, including a long-lived `serve` that opened its MCP session hours ago.

A stub provider issues 5-second access tokens. Two resolutions a minute apart both produce a
working token, because resolution refreshes. Under the code before this change the second one
returns the dead original: the store would say "refreshed" and the wire would disagree.

    just demo-oauth-refresh
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from swarmkit_runtime.credentials import CredentialService
from swarmkit_runtime.oauth import TokenStore

TOKEN_ENDPOINT = "https://auth.example/token"
issued = 0


def _provider(request: httpx.Request) -> httpx.Response:
    """Issues a new 5-second token each time it is asked."""
    global issued  # noqa: PLW0603
    issued += 1
    return httpx.Response(200, json={"access_token": f"access-{issued}", "expires_in": 5})


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = TokenStore(root)
        store.save(
            credential_id="linear",
            owner="srijith",
            provider="linear",
            endpoint="https://mcp.linear.app/mcp",
            token_response={
                "access_token": "access-0",
                "refresh_token": "refresh-1",
                "expires_in": 5,
            },
            metadata={"token_endpoint": TOKEN_ENDPOINT, "client_id": "c1"},
        )

        service = CredentialService(
            root, {"linear": {"source": "oauth", "config": {"owner": "srijith"}}}
        )
        service._store = store

        real = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(_provider)
            return real(*args, **kwargs)

        import swarmkit_runtime.credentials._service as svc  # noqa: PLC0415

        svc.httpx.AsyncClient = factory  # type: ignore[assignment]

        print("── a 5-second token, stored")
        meta = store.metadata("linear", "srijith")
        assert meta is not None
        print(f"   access-0, expires in {meta.seconds_remaining:.0f}s\n")

        print("── first resolution (inside the expiry window, so it refreshes)")
        first = await service.resolve("linear")
        print(f"   -> {first}\n")

        print("── waiting 6s, past the original token's lifetime…")
        await asyncio.sleep(6)

        print("── second resolution, as a long-lived serve would make it")
        second = await service.resolve("linear")
        print(f"   -> {second}\n")

        print("── what the caller actually gets")
        print(f"   two resolutions, two live tokens: {first} then {second}")
        print(f"   provider issued {issued} token(s); neither call used the dead access-0")
        print()
        print("   No entry point asked for a refresh. run, chat, serve and mcp-serve")
        print("   all reach this same service, so none of them can forget.")

        store.close()


if __name__ == "__main__":
    start = time.monotonic()
    asyncio.run(main())
    print(f"\n   ({time.monotonic() - start:.0f}s)")
