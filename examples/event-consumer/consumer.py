"""An application that hears what a swarm did and decides who to tell.

The point of this file is what it does **not** contain: no `swarmkit_runtime` import, no channel
adapter inside the runtime, and no knowledge of how a gate is stored. It receives events, asks a
human on whatever platform it likes, and resolves the gate over HTTP — which is the whole of
`design/details/extracting-the-channels.md`.

The Telegram code here is deliberately the *application's*. It moved out of the runtime, where it
was a supported surface tied to somebody else's API deprecations, and became a hundred lines of an
example — which is where a chat integration belongs.

Two halves, matching the delivery contract:

* **A webhook** receives pushes. Best-effort: if this process is down, the push is lost.
* **A cursor** reconciles. On startup it replays everything missed from `GET /events?after=`, which
  is why losing a push is survivable rather than an outage.

    python examples/event-consumer/consumer.py --serve http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("app")

#: Where this application remembers what it has already seen. A file because that is enough: the
#: cursor is the only durable state an event consumer needs.
CURSOR_FILE = Path(os.environ.get("CONSUMER_CURSOR", ".consumer-cursor"))

#: What this application cares about. `funnel.gate_opened` is the one that needs a human;
#: `funnel.advisory_completed` says a gate was DECLARED but could not be enforced, which is a
#: different problem and must not look like "not gated".
INTERESTING = ["funnel.gate_opened", "funnel.advisory_completed", "run.ended"]


class SwarmKit:
    """`swarmkit serve` over HTTP. The only SwarmKit-shaped code in this application."""

    def __init__(self, base_url: str, token: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def events_since(self, cursor: str, limit: int = 100) -> dict[str, Any]:
        params = {"types": ",".join(INTERESTING), "limit": limit}
        if cursor:
            params["after"] = cursor
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{self._base}/events", params=params, headers=self._headers)
            r.raise_for_status()
            return dict(r.json())

    async def gate(self, gate_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{self._base}/gates/{gate_id}", headers=self._headers)
            r.raise_for_status()
            return dict(r.json())

    async def pending_items(self, gate_id: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{self._base}/review", headers=self._headers)
            r.raise_for_status()
            return [i for i in r.json() if i.get("gate_id") == gate_id]

    async def resolve(self, item_id: str, outcome: str, comment: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                f"{self._base}/review/{item_id}/resolve",
                json={"outcome": outcome, "comment": comment},
                headers=self._headers,
            )
            r.raise_for_status()
            return dict(r.json())


class Telegram:
    """The application's own channel. Swap it for Slack, email, or a ticket — nothing else moves."""

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id

    @property
    def configured(self) -> bool:
        return bool(self._token and self._chat_id)

    async def ask(self, text: str) -> None:
        if not self.configured:
            log.info("    [would send to Telegram] %s", text)
            return
        async with httpx.AsyncClient(timeout=15.0) as c:
            await c.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text},
            )


async def handle(event: dict[str, Any], sk: SwarmKit, chat: Telegram) -> None:
    """What this application does about one event.

    All of the policy lives here rather than in the runtime — which is the point.
    """
    kind = event["event_type"]

    if kind == "funnel.gate_opened":
        gate_id = event["payload"].get("gate_id", "")
        gate = await sk.gate(gate_id)

        # A gate can be announced more than once — a run that resumes re-enters the gated node and
        # re-emits before finding the existing decision. So an application must be idempotent per
        # gate_id, or it asks the same human the same question twice. The gate itself already
        # knows: `resolved` is the check.
        if gate.get("resolved"):
            log.info("  gate %s already resolved — not asking again", gate_id)
            return

        outstanding = ", ".join(gate.get("outstanding") or []) or "someone"
        log.info("  gate %s needs %s", gate_id, outstanding)
        await chat.ask(f"Run {event['run_id']} is waiting on {outstanding}.\nGate: {gate_id}")

    elif kind == "funnel.advisory_completed":
        # NOT the same as "no gate". The funnel declared an approve layer that could not be
        # enforced — usually a role no RoleRegistry defines — so the run continued unreviewed.
        # An application that treats this as "not gated" silently loses its approval step.
        log.warning(
            "  run %s passed an ADVISORY gate — declared but unenforceable, so nobody approved it",
            event["run_id"],
        )
        await chat.ask(
            f"⚠️ Run {event['run_id']} completed through an advisory gate. "
            f"Its approve layer names a role this workspace does not define."
        )

    elif kind == "run.ended":
        log.info("  run %s ended", event["run_id"])


async def reconcile(sk: SwarmKit, chat: Telegram) -> str:
    """Replay everything missed while this application was down.

    This is why best-effort push is honest rather than lossy: a webhook that never arrived is still
    in the log, and startup is when an application finds out.
    """
    cursor = CURSOR_FILE.read_text().strip() if CURSOR_FILE.exists() else ""
    replayed = 0
    while True:
        page = await sk.events_since(cursor)
        for event in page["events"]:
            await handle(event, sk, chat)
            cursor = event["cursor"]
            replayed += 1
        CURSOR_FILE.write_text(cursor)
        if not page["has_more"]:
            break
    log.info("reconciled %d missed event(s)", replayed)
    return cursor


def serve_webhook(
    port: int, sk: SwarmKit, chat: Telegram, state: dict[str, str], loop: asyncio.AbstractEventLoop
) -> ThreadingHTTPServer:
    """The push endpoint, on the standard library.

    `http.server` rather than a framework so this example has exactly one dependency — httpx — and
    the reader can see how little an application actually needs to consume SwarmKit.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            event = json.loads(self.rfile.read(length) or b"{}")
            log.info("push: %s", event.get("event_type"))
            # ACKNOWLEDGE FIRST, then work. The event is already durable in SwarmKit's log,
            # so a failure on this side is this side's problem: answering non-2xx makes the
            # runtime retry and then drop, turning one failed lookup into a lost
            # notification. Not hypothetical — an earlier version of this file raised out of
            # the handler when a gate lookup 404'd, and the push was dropped after three
            # attempts while the event sat safely in the log the whole time.
            self.send_response(204)
            self.end_headers()

            # Checkpoint per event, so a crash mid-page does not skip what was never handled.
            state["cursor"] = event.get("cursor", state["cursor"])
            CURSOR_FILE.write_text(state["cursor"])

            # The work is async; this handler is on the server's thread. Hand it to the loop.
            future = asyncio.run_coroutine_threadsafe(handle(event, sk, chat), loop)
            try:
                future.result(timeout=30)
            except Exception:
                log.exception("  could not act on %s", event.get("event_type"))

        def log_message(self, *_: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", default=os.environ.get("SWARMKIT_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--token", default=os.environ.get("SWARMKIT_TOKEN", ""))
    args = ap.parse_args()

    sk = SwarmKit(args.serve, args.token)
    chat = Telegram(
        os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    )
    if not chat.configured:
        log.info("(no TELEGRAM_BOT_TOKEN — messages are printed instead of sent)")

    state = {"cursor": await reconcile(sk, chat)}

    serve_webhook(args.port, sk, chat, state, asyncio.get_running_loop())
    log.info("listening on http://127.0.0.1:%d/swarmkit/events", args.port)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
