"""ConversationManager — multi-turn conversations over one-shot topology runs.

Each turn runs the topology with accumulated conversation history as
context. The same service is used by CLI (swarmkit chat), HTTP server
(/conversations endpoints), and the future web UI.

Conversations persist as JSON in .swarmkit/conversations/.
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

    def __init__(self, runtime: WorkspaceRuntime, workspace_root: Path) -> None:
        self._runtime = runtime
        self._workspace_root = workspace_root
        self._conversations_dir = workspace_root / ".swarmkit" / "conversations"
        self._conversations_dir.mkdir(parents=True, exist_ok=True)

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
        self._save(conv)
        return conv

    def resume(self, conversation_id: str) -> Conversation | None:
        """Load an existing conversation by ID (or prefix)."""
        for f in self._conversations_dir.glob("*.json"):
            if f.stem.startswith(conversation_id):
                data = json.loads(f.read_text(encoding="utf-8"))
                return Conversation.from_dict(data)
        return None

    def list_conversations(self, last: int = 10) -> list[dict[str, str]]:
        """List recent conversations, newest first."""
        files = sorted(self._conversations_dir.glob("*.json"), reverse=True)[:last]
        results = []
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            turns = data.get("turns", [])
            last_human = ""
            for t in reversed(turns):
                if t.get("role") == "human":
                    msg = t.get("content", "")
                    last_human = msg[:60] + ("..." if len(msg) > 60 else "")
                    break
            results.append(
                {
                    "id": data.get("id", f.stem),
                    "topology": data.get("topology_name", ""),
                    "turns": str(len(turns)),
                    "updated": data.get("updated_at", "")[:19],
                    "last_message": last_human,
                }
            )
        return results

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
        self._record_turn_job(run_id, conversation, user_message)
        try:
            result = await self._runtime.run(conversation.topology_name, context, thread_id=run_id)
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

    def _record_turn_job(self, run_id: str, conversation: Conversation, message: str) -> None:
        """Open a job row for this turn, linked to the conversation by ``correlation_id``.

        Best-effort in one direction only, as everywhere else: a store that will not open loses the
        RECORD of a turn, never the turn.
        """
        store = self._store()
        if store is None:
            return
        try:
            store.create_job(run_id, conversation.topology_name, message, conversation.id, "chat")
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
        """Persist conversation to disk."""
        path = self._conversations_dir / f"{conversation.id}.json"
        path.write_text(
            json.dumps(conversation.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
