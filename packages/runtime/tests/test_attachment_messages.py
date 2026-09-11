"""Where an attachment lands in the message list — and, more importantly, where it does not.

``design/details/images-on-both-executors.md``. This shipped once (M8) as ``SwarmState.image_paths``
broadcast to every node, which handed images to text-only supervisors and errored there; narrowing
it to leaf agents was a patch on the wrong axis and the whole thing was reverted (``43ed71e3``).

So the load-bearing assertion in this file is not that an attachment arrives. It is that it arrives
**once, at the entry agent, and nowhere else** — and that a type nothing can carry raises instead of
vanishing, because an agent answering confidently about a file it never received is the failure the
whole design exists to prevent.
"""

from __future__ import annotations

import base64
from typing import Any, cast

import pytest
from swarmkit_runtime.attachments import Attachment, AttachmentError
from swarmkit_runtime.langgraph_compiler._prompts import _build_prompt_messages
from swarmkit_runtime.langgraph_compiler._state import SwarmState
from swarmkit_runtime.resolver._resolved import ResolvedAgent

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _image() -> Attachment:
    return Attachment(media_type="image/png", data=PNG, name="gate.png", source="gate.png")


def _pdf() -> Attachment:
    return Attachment(media_type="application/pdf", data=b"%PDF-1.7\n", name="r.pdf")


def _agent(agent_id: str, role: str, children: tuple[ResolvedAgent, ...] = ()) -> ResolvedAgent:
    return ResolvedAgent(
        id=agent_id,
        role=cast(Any, role),
        model={"name": "mock"},
        prompt={"system": "system prompt"},
        skills=(),
        iam=None,
        children=children,
    )


def _state(**over: Any) -> SwarmState:
    base: dict[str, Any] = {
        "input": "what is in this picture?",
        "messages": [],
        "agent_results": {},
        "delegation_counts": {},
        "task_plan": {},
        "current_agent": "",
        "output": "",
        "node_errors": {},
        "diffs": {},
        "attachments": [],
    }
    base.update(over)
    return cast(SwarmState, base)


def _image_blocks(messages: list[Any]) -> list[Any]:
    out: list[Any] = []
    for m in messages:
        if isinstance(m.content, str):
            continue
        out.extend(b for b in m.content if b.type == "image")
    return out


# --- it arrives, on the entry agent ------------------------------------------------------------


def test_attachment_reaches_the_entry_agent() -> None:
    messages = _build_prompt_messages(_agent("root", "root"), _state(attachments=[_image()]))
    blocks = _image_blocks(messages)

    assert len(blocks) == 1
    assert blocks[0].image_media_type == "image/png"
    assert blocks[0].image_data == base64.b64encode(PNG).decode("ascii")


def test_the_prompt_text_survives_beside_the_image() -> None:
    """An image with the question dropped is a subtler failure than no image at all."""
    messages = _build_prompt_messages(_agent("root", "root"), _state(attachments=[_image()]))
    first_user = next(m for m in messages if m.role == "user")

    blocks = cast("list[Any]", first_user.content)
    text = "".join(b.text or "" for b in blocks if b.type == "text")
    assert "what is in this picture?" in text


def test_several_attachments_all_arrive_in_order() -> None:
    a = Attachment(media_type="image/png", data=PNG, name="a.png")
    b = Attachment(media_type="image/jpeg", data=b"\xff\xd8\xff\xe0ju", name="b.jpg")
    messages = _build_prompt_messages(_agent("root", "root"), _state(attachments=[a, b]))

    assert [x.image_media_type for x in _image_blocks(messages)] == ["image/png", "image/jpeg"]


# --- and nowhere else. this is the one that got the first implementation reverted ---------------


@pytest.mark.parametrize("role", ["leader", "worker"])
def test_downstream_agents_never_see_the_attachment(role: str) -> None:
    """The reverted M8 design's exact failure: a supervisor or worker handed an image it did not
    ask for, on a model that may not take one at all."""
    messages = _build_prompt_messages(_agent("child", role), _state(attachments=[_image()]))

    assert _image_blocks(messages) == []
    assert all(isinstance(m.content, str) for m in messages)


def test_a_root_with_children_still_gets_only_one_copy() -> None:
    """A root that delegates is still the entry node — one copy, not one per child."""
    root = _agent("root", "root", children=(_agent("w1", "worker"), _agent("w2", "worker")))
    messages = _build_prompt_messages(root, _state(attachments=[_image()]))

    assert len(_image_blocks(messages)) == 1


def test_no_attachments_leaves_messages_as_plain_text() -> None:
    """The whole feature must be invisible when unused — every existing run takes this path."""
    plain = _build_prompt_messages(_agent("root", "root"), _state())
    assert all(isinstance(m.content, str) for m in plain)


def test_state_without_the_key_at_all_is_fine() -> None:
    """A checkpoint written before this feature existed has no ``attachments`` key."""
    state = _state()
    del state["attachments"]
    messages = _build_prompt_messages(_agent("root", "root"), state)
    assert all(isinstance(m.content, str) for m in messages)


# --- never dropped ------------------------------------------------------------------------------


def test_a_type_that_cannot_be_carried_raises_rather_than_vanishing() -> None:
    """The founding rule. A PDF has no message block today, so it must stop the run — not be
    filtered out, leaving an agent to answer confidently about a document it never received."""
    with pytest.raises(AttachmentError) as exc:
        _build_prompt_messages(_agent("root", "root"), _state(attachments=[_pdf()]))

    assert "application/pdf" in str(exc.value)
    assert "r.pdf" in str(exc.value)


def test_one_bad_type_among_good_ones_still_raises() -> None:
    """Partial delivery is the dangerous outcome: the run looks fine and the missing file is the
    one nobody checks for."""
    with pytest.raises(AttachmentError):
        _build_prompt_messages(
            _agent("root", "root"), _state(attachments=[_image(), _pdf(), _image()])
        )
