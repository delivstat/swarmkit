"""An image returned by an MCP tool must reach a harness agent as an image.

Reported against 1.133.0. `_to_content()` read `.text` off every content block and kept only what
had one. `ImageContent` carries `.data` (base64) + `.mimeType` and no `.text`, so every image block
was skipped; a response that was *only* images left `out` empty and fell through to `str(data)`,
delivering a repr:

    Image: screen1.png (.png, 14960 bytes base64)

The agent behaved correctly — it reported the gap rather than inventing screen contents:

    "The view_image tool call succeeded (no error), but what came back in my context was only the
    file metadata ... not an actual rendered image I can visually inspect, so I can't state exactly
    what's on that screen without fabricating details."

This one is worse than the tool-outcome bug fixed in 1.135.0, and would not have been caught by it:
the call genuinely **succeeds**. The bytes are read and then discarded in the last step before the
harness sees them, so there is no failure anywhere to trace.

It is also executor-dependent. A model node has always rendered these correctly — `_skill_executor`
builds a real image block from the same `type == "image"` discriminator. The gateway is the
harness's only route to MCP, so the same skill on the same workspace worked on a model node and
silently degraded on a harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from swarmkit_runtime.mcp._gateway import _to_content

mcp_types = pytest.importorskip("mcp.types")
TextContent = mcp_types.TextContent
ImageContent = mcp_types.ImageContent

# A 1x1 PNG — the smallest thing that is genuinely image bytes rather than a stand-in.
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@dataclass
class _Result:
    content: list[Any] = field(default_factory=list)

    def __str__(self) -> str:
        # Mirrors the repr the agent actually received, so the fallback path is realistic.
        return "Image: screen1.png (.png, 14960 bytes base64)"


@dataclass
class _Resp:
    data: Any


def _image_block() -> Any:
    return ImageContent(type="image", data=PNG_B64, mimeType="image/png")


def _call(*blocks: Any) -> list[Any]:
    return _to_content(_Resp(_Result(list(blocks))), TextContent, ImageContent)


# ---- the reported bug --------------------------------------------------------------------------


def test_an_image_only_response_delivers_the_image() -> None:
    """The bug as the agent met it: a screenshot tool whose whole payload is the picture."""
    out = _call(_image_block())

    assert len(out) == 1
    assert out[0].type == "image", "the image was dropped and replaced by a text repr"
    assert out[0].data == PNG_B64
    assert out[0].mimeType == "image/png"


def test_the_placeholder_repr_is_gone() -> None:
    """Pins the exact symptom: `str(data)` describing the image instead of delivering it."""
    out = _call(_image_block())

    assert not any(
        getattr(b, "type", None) == "text" and "bytes base64" in getattr(b, "text", "") for b in out
    ), "the agent was handed a description of the image rather than the image"


def test_mixed_text_and_image_keeps_both() -> None:
    """MCP's CallToolResult permits mixed content. Text used to survive and images did not, so a
    tool returning a caption plus a picture looked like it had returned only a caption."""
    out = _call(TextContent(type="text", text="screen1.png"), _image_block())

    assert [b.type for b in out] == ["text", "image"]
    assert out[1].data == PNG_B64


def test_order_is_preserved() -> None:
    """Interleaving carries meaning — a caption belongs with the image it labels."""
    out = _call(
        TextContent(type="text", text="before"),
        _image_block(),
        TextContent(type="text", text="after"),
    )
    assert [b.type for b in out] == ["text", "image", "text"]
    assert [b.text for b in out if b.type == "text"] == ["before", "after"]


def test_several_images_all_arrive() -> None:
    """The reported workflow reads a set of RF panel screenshots, not one."""
    out = _call(_image_block(), _image_block(), _image_block())
    assert [b.type for b in out] == ["image"] * 3


# ---- guards ------------------------------------------------------------------------------------


def test_text_only_is_unchanged() -> None:
    """The overwhelmingly common case must be byte-identical to before."""
    out = _call(TextContent(type="text", text="hello"))
    assert len(out) == 1
    assert out[0].type == "text"
    assert out[0].text == "hello"


def test_an_image_without_data_is_not_emitted() -> None:
    """A malformed block must not become an image block with `data=None` — that would fail at the
    harness with a less legible error than simply not being there."""

    class _Broken:
        type = "image"
        data = None
        mimeType = "image/png"

    out = _call(_Broken())
    assert all(getattr(b, "type", None) != "image" for b in out)


def test_the_type_discriminator_is_used_not_attribute_sniffing() -> None:
    """A text block with an empty string has `.text` falsy and may carry other attributes. Sniffing
    for `.data` would misclassify it; the model path uses `type == "image"` and so does this."""

    class _EmptyText:
        type = "text"
        text = ""
        data = "not-an-image"
        mimeType = "image/png"

    out = _call(_EmptyText())
    assert all(getattr(b, "type", None) != "image" for b in out)


def test_no_content_still_falls_back_to_text() -> None:
    """An empty response must not crash; the string fallback is still right when there is nothing
    structured to return."""
    out = _to_content(_Resp(_Result([])), TextContent, ImageContent)
    assert len(out) == 1
    assert out[0].type == "text"


def test_omitting_the_image_type_degrades_to_the_old_behaviour() -> None:
    """`image_content` is optional so the helper stays callable without it. Callers that do not pass
    it get text only — which is why the call site passes it."""
    out = _to_content(_Resp(_Result([_image_block()])), TextContent)
    assert all(getattr(b, "type", None) != "image" for b in out)


# ---- the executor-parity property --------------------------------------------------------------


def test_the_harness_path_agrees_with_the_model_path() -> None:
    """The actual invariant: an executor is an implementation detail, not a capability difference.

    `_skill_executor` (model path) turns a block with `type == "image"` into a real image block
    using `.data` / `.mimeType`. The gateway is the harness's only route to MCP and must reach the
    same conclusion about the same block — that agreement is what the bug broke.
    """
    block = _image_block()

    # What the model path extracts, by the same discriminator it uses.
    model_sees = (
        getattr(block, "type", None) == "image",
        getattr(block, "data", None),
        getattr(block, "mimeType", None),
    )
    out = _call(block)
    harness_sees = (out[0].type == "image", out[0].data, out[0].mimeType)

    assert model_sees == harness_sees
