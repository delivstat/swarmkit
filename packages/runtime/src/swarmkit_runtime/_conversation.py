"""ConversationManager — multi-turn conversations over one-shot topology runs.

Each turn runs the topology with accumulated conversation history as
context. The same service is used by CLI (swarmkit chat), HTTP server
(/conversations endpoints), and the future web UI.

Conversations persist in the workspace's runtime store — the `conversations` table the storage
service resolves (SQLite or Postgres, `storage.runtime`) — not in files. Conversations saved by
earlier versions as `.swarmkit/conversations/<id>.json` are still readable, and are moved into the
store the first time they are resumed.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swarmkit_runtime._workspace_runtime import RunResult, WorkspaceRuntime
from swarmkit_runtime.persistence import usage_fields

logger = logging.getLogger("swarmkit.conversation")


@dataclass
class ConversationTurn:
    """One human→swarm exchange."""

    role: str  # "human" or "swarm"
    content: str
    timestamp: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Conversation:
    """A multi-turn conversation with a topology."""

    id: str
    workspace_path: str
    topology_name: str
    turns: list[ConversationTurn] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def clear(self) -> None:
        """Clear conversation history for a fresh start."""
        self.turns.clear()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
        turns = [ConversationTurn(**t) for t in data.pop("turns", [])]
        return cls(**data, turns=turns)


def turn_run_id(conversation_id: str, turn_index: int) -> str:
    """The run id for one turn of a conversation: ``<conversation>:<turn>``.

    Per-TURN, not per-conversation, for the same reason a pipeline stage gets its own: the id is
    also the LangGraph checkpoint thread and the trace's ``run_id``, and a trace saves to
    ``{run_id}.json``. Sharing one id across turns would make each turn overwrite the previous
    turn's trace and inherit its graph state — while the conversation already carries history
    itself, as text, which is what a turn is actually given.
    """
    return f"{conversation_id}:{turn_index}"


class ConversationManager:
    """Manages multi-turn conversations over WorkspaceRuntime.

    Each turn runs the topology one-shot with the full conversation
    history prepended to the input. The topology doesn't know it's
    in a conversation — it just sees a longer input.
    """

    def __init__(
        self,
        runtime: WorkspaceRuntime,
        workspace_root: Path,
        *,
        canary: Any = None,
    ) -> None:
        self._runtime = runtime
        self._workspace_root = workspace_root
        #: The server's canary router, when a turn is being served by one. A turn resolves its
        #: topology through `JobService` like every other interface, so a topology under canary is
        #: routed for chat too — it previously ran the base topology always, which meant a chat
        #: turn and a `POST /run` of the same name could execute different versions.
        self._canary = canary
        # Pre-1.227 conversations. Read, never written: the store is where conversations live.
        self._legacy_dir = workspace_root / ".swarmkit" / "conversations"

    async def start_session(self) -> None:
        """Start MCP servers for the conversation session.

        Keeps servers alive across turns instead of restarting per message.
        """
        await self._runtime.start_session()

    async def end_session(self) -> None:
        """Stop MCP servers when the conversation ends."""
        await self._runtime.end_session()

    def create(self, topology_name: str) -> Conversation:
        """Start a new conversation."""
        now = datetime.now(tz=UTC).isoformat()
        conv = Conversation(
            id=str(uuid.uuid4())[:8],
            workspace_path=str(self._workspace_root),
            topology_name=topology_name,
            created_at=now,
            updated_at=now,
        )
        store = self._store()
        if store is not None:
            try:
                store.create_conversation(conv.id, topology_name)
            except Exception:
                logger.warning(
                    "conversation %s will not be resumable: could not create its row", conv.id
                )
        self._save(conv)
        return conv

    def resume(self, conversation_id: str) -> Conversation | None:
        """Load an existing conversation by ID (or prefix)."""
        store = self._runtime.store
        row = store.get_conversation(conversation_id)
        if row is None:
            rows = [
                r for r in store.list_conversations(limit=500) if r.id.startswith(conversation_id)
            ]
            row = rows[0] if len(rows) == 1 else None
        if row is not None:
            return self._from_row(row)
        # A conversation saved as a file by an earlier version: adopt it into the store so the
        # next resume, and `swarmkit conversations`, find it where everything else is.
        for f in self._legacy_dir.glob("*.json") if self._legacy_dir.is_dir() else []:
            if f.stem.startswith(conversation_id):
                conv = Conversation.from_dict(json.loads(f.read_text(encoding="utf-8")))
                store.create_conversation(conv.id, conv.topology_name)
                self._save(conv)
                return conv
        return None

    def list_conversations(self, last: int = 10) -> list[dict[str, str]]:
        """List recent conversations, newest first."""
        results = []
        store = self._store()
        for row in store.list_conversations(limit=last) if store is not None else []:
            turns = row.turns
            last_human = ""
            for t in reversed(turns):
                if t.get("role") == "human":
                    msg = t.get("content", "")
                    last_human = msg[:60] + ("..." if len(msg) > 60 else "")
                    break
            results.append(
                {
                    "id": row.id,
                    "topology": row.topology,
                    "turns": str(len(turns)),
                    "updated": row.updated_at[:19],
                    "last_message": last_human,
                }
            )
        return results

    def _from_row(self, row: Any) -> Conversation:
        return Conversation(
            id=row.id,
            workspace_path=str(row.metadata.get("workspace_path") or self._workspace_root),
            topology_name=row.topology,
            turns=[ConversationTurn(**t) for t in row.turns],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def send(self, conversation: Conversation, user_message: str) -> RunResult:
        """Send a message and get the swarm's response.

        Builds the full conversation context, runs the topology,
        appends both human and swarm turns, saves.
        """
        now = datetime.now(tz=UTC).isoformat()

        conversation.turns.append(
            ConversationTurn(role="human", content=user_message, timestamp=now)
        )

        context = self._build_context(conversation)

        # A chat turn is a topology run like any other, and was the last one recording nothing.
        # `POST /run/{topology}` wrote a job, `swarmkit run` since 1.150.0, a pipeline stage since
        # 1.152.0 — a turn wrote none, so a conversation was invisible in `/jobs` and its cost was
        # attributable to nobody. Worse, the run had no thread id, so its trace and its audit rows
        # landed under a fresh random UUID that no conversation pointed at: the events existed and
        # could not be found from the thing that caused them.
        # Numbered by EXCHANGE, not by list position: turns hold both sides, so positions would
        # run 1, 3, 5 and read as gaps in a record that has none.
        run_id = turn_run_id(
            conversation.id, sum(1 for t in conversation.turns if t.role == "human")
        )
        # Resolve through the service rather than running the name as written: canary routing and
        # the version stamp are decisions about *which* topology runs, and they must not depend on
        # which door the caller came through. The run itself stays here — a turn is awaited inline
        # so Ctrl-C still reaches the caller and a failed row still never costs the answer.
        topology_to_run, version = self._resolve_topology(conversation.topology_name)
        self._record_turn_job(run_id, conversation, user_message, version=version)
        try:
            # a turn is awaited INLINE on purpose. Through JobService the
            # run would execute in a task, where a Ctrl-C escapes into the event loop instead of
            # reaching the caller, and the service writes its row unguarded while chat's rule is
            # that a store which will not write loses the record of a turn, never the turn. The
            # part that must not diverge — WHICH topology runs — is resolved through the service
            # above (per-caller-credential-delegation.md is unrelated; see #977).
            # noqa: service-layer
            result = await self._runtime.run(topology_to_run, context, thread_id=run_id)
        except BaseException as exc:
            self._finish_turn_job(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise
        self._finish_turn_job(
            run_id,
            "completed",
            output=result.output,
            usage=getattr(result, "usage", None),
            diffs=getattr(result, "diffs", {}) or {},
        )

        conversation.turns.append(
            ConversationTurn(
                role="swarm",
                content=result.output,
                timestamp=datetime.now(tz=UTC).isoformat(),
                events=[
                    {
                        "event_type": e.event_type,
                        "agent_id": e.agent_id,
                        "duration_ms": e.payload.get("duration_ms"),
                    }
                    for e in result.events
                    if e.event_type == "agent.completed"
                ],
            )
        )

        conversation.updated_at = datetime.now(tz=UTC).isoformat()
        self._save(conversation)

        return result

    def _store(self) -> Any:
        """The durable store, or None. Reached through the runtime's one storage service."""
        try:
            return self._runtime.store
        except Exception:
            logger.warning("this conversation will not appear in jobs: the store did not open")
            return None

    def _resolve_topology(self, topology_name: str) -> tuple[str, str | None]:
        """The topology this turn should run, and the canary version if it was routed.

        `JobService.resolve_topology` is the one place that answers this, and it is pure — no
        store, no execution — so a turn can ask it without adopting the rest of the run lifecycle.
        Best-effort in the same direction as everything else here: if the service cannot answer,
        the turn runs the name as written rather than failing on a routing question.
        """
        try:
            from swarmkit_runtime.server._jobs import JobStore  # noqa: PLC0415
            from swarmkit_runtime.server._services import JobService  # noqa: PLC0415

            return JobService(JobStore()).resolve_topology(
                self._runtime, self._canary, topology_name
            )
        except Exception:
            logger.debug("canary routing unavailable for %r", topology_name, exc_info=True)
            return topology_name, None

    def _record_turn_job(
        self,
        run_id: str,
        conversation: Conversation,
        message: str,
        *,
        version: str | None = None,
    ) -> None:
        """Open a job row for this turn, linked to the conversation by ``correlation_id``.

        Best-effort in one direction only, as everywhere else: a store that will not open loses the
        RECORD of a turn, never the turn.
        """
        store = self._store()
        if store is None:
            return
        try:
            store.create_job(run_id, conversation.topology_name, message, conversation.id, "chat")
            if version:
                # The same stamp `POST /run` writes, so "which version answered this" is readable
                # from the row rather than inferred from when the turn happened.
                store.update_job(run_id, version=version)
        # A conversation must continue whether or not it can be recorded.
        except Exception:
            logger.warning("turn %s will not appear in jobs: could not create its row", run_id)

    def _finish_turn_job(
        self,
        run_id: str,
        status: str,
        *,
        output: str = "",
        error: str = "",
        usage: Any = None,
        diffs: dict[str, str] | None = None,
    ) -> None:
        """Close the turn's row. A row left at `running` is indistinguishable from a turn still
        being answered."""
        store = self._store()
        if store is None:
            return
        fields: dict[str, Any] = {
            "status": status,
            "completed_at": datetime.now(tz=UTC).isoformat(),
        }
        if output:
            fields["output"] = output
        if error:
            fields["error"] = error
        # Both usage sinks, through the one recorder — see persistence/_usage_recording.py.
        fields.update(usage_fields(usage, run_id, store))
        if diffs is not None:
            fields["diffs"] = diffs
        try:
            store.update_job(run_id, **fields)
        # Same one-directional rule on the way out.
        except Exception:
            logger.warning("could not record the outcome of turn %s", run_id)

    def _build_context(self, conversation: Conversation) -> str:
        """Build the full input for this turn: history + current message."""
        if len(conversation.turns) <= 1:
            return conversation.turns[-1].content

        parts = ["[Conversation history]\n"]
        for turn in conversation.turns[:-1]:
            prefix = "Human" if turn.role == "human" else "Swarm"
            parts.append(f"{prefix}: {turn.content}\n")

        parts.append(f"\n[Current message]\nHuman: {conversation.turns[-1].content}")
        return "\n".join(parts)

    def _save(self, conversation: Conversation) -> None:
        """Persist the conversation's turns to the store. Best-effort, the one-directional rule
        again: a store that will not take the turns loses the ability to RESUME, never the
        answer the user is reading."""
        store = self._store()
        if store is None:
            return
        try:
            store.update_conversation(
                conversation.id,
                [asdict(t) for t in conversation.turns],
                metadata={"workspace_path": conversation.workspace_path},
            )
        except Exception:
            logger.warning("could not save conversation %s; it will not resume", conversation.id)
