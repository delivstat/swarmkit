"""ConversationManager — multi-turn conversations over one-shot topology runs.

Each turn runs the topology with accumulated conversation history as
context. The same service is used by CLI (swarmkit chat), HTTP server
(/conversations endpoints), and the future web UI.

Conversations persist as JSON in .swarmkit/conversations/.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swarmkit_runtime._workspace_runtime import RunResult, WorkspaceRuntime


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
        turns = [ConversationTurn(**t) for t in data.pop("turns", [])]
        return cls(**data, turns=turns)


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

        result = await self._runtime.run(conversation.topology_name, context)

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
