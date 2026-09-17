"""The parsed ``implementation`` block of an ``agent`` skill, and the load-time target check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from swarmkit_runtime.skills import impl_get

InputPolicy = Literal["agent", "relay", "abort"]

DEFAULT_TIMEOUT_S = 600
DEFAULT_MAX_AGENT_ANSWERS = 2


@dataclass(frozen=True)
class AgentSkillSpec:
    """One ``agent`` implementation, validated: exactly one of ``topology`` / ``card_url``."""

    topology: str | None
    card_url: str | None
    skill_id: str | None
    credentials_ref: str | None
    timeout_s: int
    on_unanswerable: InputPolicy
    max_agent_answers: int
    permission: str
    effects: str

    @property
    def is_local(self) -> bool:
        return self.topology is not None

    @property
    def target(self) -> str:
        """What the audit and the tool result name — the topology id or the card URL."""
        return self.topology or self.card_url or ""


def _plain(value: Any) -> Any:
    """A generated pydantic model hands enum fields back as Enum members; the string is wanted."""
    return getattr(value, "value", value)


def parse_agent_spec(impl: Any) -> AgentSkillSpec:
    """Read an ``agent`` implementation (dict or pydantic model) into a spec.

    Raises ``ValueError`` when both or neither target is named — the schema's ``oneOf`` says the
    same, but the generated pydantic model cannot express it, so this is where it is enforced for
    a skill that reached the runtime by any other path.
    """
    topology = impl_get(impl, "topology", None) or None
    card_url = impl_get(impl, "card_url", None) or None
    if bool(topology) == bool(card_url):
        raise ValueError("an agent skill names exactly one of `topology` or `card_url`")
    policy = str(_plain(impl_get(impl, "on_unanswerable", None)) or "agent")
    if policy not in ("agent", "relay", "abort"):
        raise ValueError(f"on_unanswerable must be agent | relay | abort, not {policy!r}")
    return AgentSkillSpec(
        topology=str(topology) if topology else None,
        card_url=str(card_url) if card_url else None,
        skill_id=(str(impl_get(impl, "skill_id", None)) or None)
        if impl_get(impl, "skill_id", None)
        else None,
        credentials_ref=(str(impl_get(impl, "credentials_ref", None)) or None)
        if impl_get(impl, "credentials_ref", None)
        else None,
        timeout_s=int(impl_get(impl, "timeout_s", None) or DEFAULT_TIMEOUT_S),
        on_unanswerable=policy,  # type: ignore[arg-type]
        max_agent_answers=int(
            impl_get(impl, "max_agent_answers", None)
            if impl_get(impl, "max_agent_answers", None) is not None
            else DEFAULT_MAX_AGENT_ANSWERS
        ),
        permission=str(_plain(impl_get(impl, "permission", None)) or "cautious"),
        effects=str(_plain(impl_get(impl, "effects", None)) or "unknown"),
    )


def find_bad_agent_targets(workspace: Any) -> list[tuple[str, str]]:
    """``(skill_id, description)`` pairs whose ``agent`` target cannot be resolved at load.

    A local target must be a topology in this workspace. Reported here, next to missing command
    packs and MCP servers, so a skill that would fail on first use fails the load instead. A remote
    card is not fetched at load — the network is not a precondition for starting — it is checked
    when the skill is first called and by ``swarmkit validate``.
    """
    bad: list[tuple[str, str]] = []
    for skill_id, skill in workspace.skills.items():
        impl = skill.raw.implementation
        if impl_get(impl, "type") != "agent":
            continue
        try:
            spec = parse_agent_spec(impl)
        except ValueError as exc:
            bad.append((skill_id, f"an agent target ({exc})"))
            continue
        if spec.topology is not None and spec.topology not in workspace.topologies:
            bad.append((skill_id, f"topology '{spec.topology}'"))
    return bad
