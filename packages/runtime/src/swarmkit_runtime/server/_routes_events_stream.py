"""`GET /events` — the durable half of the event seam.

`design/details/extracting-the-channels.md`. An application reconciles from here: the audit log is
already append-only and ordered, so a missed webhook costs a catch-up rather than a lost approval.

Deliberately not a stream. SSE already exists for one job (`/jobs/{id}/stream`); this answers a
different question — *what have I not seen yet, across everything* — and a cursor answers it after
a restart, a redeploy or an outage, which a stream cannot.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request

from swarmkit_runtime.events import CursorError, decode_cursor, encode_cursor

#: A page an application can hold in memory without thinking about it.
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


def _event_to_dict(event: Any, *, cursor: str) -> dict[str, Any]:
    """The wire shape. Carries its own cursor so a consumer can checkpoint per event.

    Checkpointing per event rather than per page matters for an application that crashes
    mid-page: resuming from the page's end would skip what it never processed.
    """
    return {
        "cursor": cursor,
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "timestamp": event.timestamp.isoformat(),
        "run_id": event.run_id,
        "topology_id": event.topology_id,
        "agent_id": event.agent_id,
        "payload": event.payload or {},
        "labels": event.labels or {},
    }


def _register_event_stream_routes(app: FastAPI) -> None:
    @app.get("/events")
    async def list_events(
        request: Request,
        after: Annotated[str, Query(description="Cursor from a previous response.")] = "",
        types: Annotated[str, Query(description="Comma-separated event types.")] = "",
        run_id: Annotated[str, Query()] = "",
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Events in log order from a position.

        Ascending, so a consumer replays forward. `next_cursor` is echoed even on an empty page, so
        an idle application does not have to remember where it was.
        """
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None:
            raise HTTPException(503, "no workspace runtime")
        # SqlAuditProvider IS the store — there is no wrapper to unwrap. An earlier draft
        # looked for a `_store` attribute and 501'd on every workspace.
        store = runtime.audit_provider_for(runtime._workspace_root)
        if not hasattr(store, "since_cursor"):
            raise HTTPException(
                501,
                "this audit backend cannot be read by cursor. `GET /events` needs the sqlite or "
                "postgres store.",
            )

        position = None
        if after:
            try:
                cursor = decode_cursor(after)
            except CursorError as exc:
                raise HTTPException(400, str(exc)) from exc
            position = (cursor.timestamp, cursor.event_id)

        wanted = [t.strip() for t in types.split(",") if t.strip()]
        events = await store.since_cursor(
            after=position, event_types=wanted or None, run_id=run_id or None, limit=limit
        )
        rendered = [
            _event_to_dict(e, cursor=encode_cursor(e.timestamp.isoformat(), str(e.event_id)))
            for e in events
        ]
        return {
            "events": rendered,
            "next_cursor": rendered[-1]["cursor"] if rendered else after,
            "has_more": len(rendered) == limit,
        }
