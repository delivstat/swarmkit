"""Reversible-lossy compressor: keep the head and tail, elide the middle.

For lossy-tolerant surfaces (logs, large command output, verbose dumps) where the
informative parts are usually at the top (what ran / the schema) and the bottom (the
result / the error). The middle is replaced with a marker pointing at
``context_retrieve``, and the seam stashes the full original under the ref — so this is
lossy *at the point of read* but fully recoverable within the run. Deterministic,
zero-dependency. Learned/LLM backends plug into the same ContextCompressor Protocol.

See design/details/context-compression.md.
"""

from __future__ import annotations

DEFAULT_HEAD = 1500
DEFAULT_TAIL = 1000


class HeadTailCompressor:
    """Keep the first ``head`` and last ``tail`` characters; elide and stash the middle."""

    name = "headtail"
    reversible = True

    def __init__(self, head: int = DEFAULT_HEAD, tail: int = DEFAULT_TAIL) -> None:
        self._head = head
        self._tail = tail

    def compress(self, text: str, ref: str | None = None) -> str:
        # Not worth eliding if the body is barely larger than head+tail+marker.
        if len(text) <= self._head + self._tail:
            return text
        head = text[: self._head]
        tail = text[-self._tail :]
        elided = len(text) - self._head - self._tail
        marker = (
            f"\n\n…[{elided} chars elided — call "
            f'context_retrieve(ref="{ref}", offset=, limit=) for the full '
            f"{len(text)}-char output]…\n\n"
        )
        return head + marker + tail
