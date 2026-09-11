"""Attachments on a run — resolving a caller's file into bytes a model can be sent.

``design/details/images-on-both-executors.md``. A caller that already holds the bytes should not
need an agent to go and find them: a snapshot poller, a webhook with an uploaded photo, a trigger
firing on a new file. Those callers pass ``attachments`` beside the input, and this module turns a
declared path or blob into a validated :class:`Attachment` the compiler can put in the entry node's
first message.

**An attachment is an argument to one invocation, not run state.** This shipped once before (M8,
``SwarmState.image_paths``) as state broadcast to every node, which meant an image reached
text-only supervisors and errored there; it was narrowed to leaf agents and then reverted whole.
So the bytes live on the state the entry node reads and are put into *its* first user message only
— a node that wants an image it was not handed asks for one through a skill, which is what skills
are for.

**The type is sniffed, never declared.** There is deliberately no ``type`` field on the caller's
side: taking one would be accepting a claim about bytes that are about to be forwarded to a third
party, and the flag-shaped version of the same mistake (``--image``, then ``--pdf``, ``--audio``)
bakes the first case into the surface permanently. One ``--attach``, and the bytes say what they
are.

**Re-readable, so no streams and no URLs.** The tool loop re-sends the whole message history each
turn and a retry re-sends everything, so an attachment is read more than once by construction; a
stream would have to be buffered anyway, which hides the cost rather than removing it. A URL source
would have the runtime fetch a caller-supplied address, which is the same exfiltration primitive
this design rejected for prompt-scraped paths — and worth refusing by name, because some providers
do accept URLs and passing one through will look free.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

#: Ceiling per attachment. Providers impose their own (Anthropic ~5 MB an image, OpenAI ~20 MB), and
#: a base64 body inflates by a third on the way out — so refuse here, with the size in the message,
#: rather than letting a provider reject it after the upload.
MAX_BYTES = int(os.environ.get("SWARMKIT_ATTACHMENT_MAX_BYTES", str(20 * 1024 * 1024)))

#: What the compiler can currently put in a message. Everything else resolves fine and fails at the
#: mapping with a message naming the type — see `design/details/images-on-both-executors.md`, which
#: sequences non-image types behind preprocessing (MarkItDown, Whisper) rather than per-provider
#: native blocks.
RENDERABLE_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"},
)

Handling = Literal["preprocess", "native"]


class AttachmentError(ValueError):
    """A caller's attachment could not be accepted.

    Carries a message an operator can act on — the path, the size, the reason — because the
    alternative to a clear refusal here is a provider error three layers away, or worse, a
    silently dropped file and an agent answering confidently about something it never saw.
    """


@dataclass(frozen=True)
class Attachment:
    """One resolved attachment: bytes, and what they turned out to be.

    ``media_type`` is sniffed from ``data`` and never supplied by the caller. ``source`` is the
    workspace-relative path when there was one, and ``None`` for inline bytes — it exists for the
    audit record, so "what did the model see" has an answer that is not the bytes themselves.
    """

    media_type: str
    data: bytes
    name: str
    source: str | None = None
    handling: Handling = "preprocess"

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    def audit_record(self) -> dict[str, object]:
        """What the audit log keeps: everything except the bytes.

        The digest is what makes the reference checkable later — the run record can say which file
        the model was shown and a reader can confirm the file on disk is still that one. Storing
        the bytes themselves would put arbitrary content into a log meant to stay readable, and
        grow it without bound on an appliance attaching one image per event.
        """
        return {
            "name": self.name,
            "media_type": self.media_type,
            "size": self.size,
            "sha256": self.sha256,
            "source": self.source,
            "handling": self.handling,
        }


def _sniff(data: bytes) -> str:  # noqa: PLR0911
    """The media type according to the bytes, not the extension.

    Deliberately narrow: the types below are the ones with unambiguous magic numbers. Anything else
    is ``application/octet-stream``, which is honest — an unknown type refused by name at the
    mapping is a better outcome than a guess that reaches a provider as a corrupt image.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"ID3") or data[:2] == b"\xff\xfb":
        return "audio/mpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    return "application/octet-stream"


def _resolve_path(raw: str, workspace_root: Path) -> Path:
    """Resolve a declared path inside the workspace, refusing every way out of it.

    Same rule as ``deliver_context_files``: ``..``, absolute paths and symlinks pointing outside
    are refused rather than resolved. A caller-supplied path is no more trustworthy than a
    model-supplied one — the run's input is frequently a ticket body or a webhook payload — so the
    check does not depend on who asked.
    """
    if os.path.isabs(raw):
        raise AttachmentError(f"attachment path must be workspace-relative, got absolute: {raw!r}")

    root = workspace_root.resolve()
    candidate = (root / raw).resolve()
    if not candidate.is_relative_to(root):
        raise AttachmentError(f"attachment path escapes the workspace: {raw!r}")
    if not candidate.exists():
        raise AttachmentError(f"attachment not found: {raw!r}")
    if not candidate.is_file():
        raise AttachmentError(f"attachment is not a file: {raw!r}")
    return candidate


def resolve_one(spec: Any, workspace_root: Path) -> Attachment:
    """Turn one caller-declared attachment into bytes plus what they are.

    *spec* is a mapping or any object exposing ``path`` / ``data`` / ``name`` / ``handling`` — the
    HTTP model, a CLI-built dict and a Python caller's dataclass all satisfy it without this module
    importing any of them.

    An already-resolved :class:`Attachment` passes through untouched. That is what lets the HTTP
    layer validate at the boundary — so a missing file is a 4xx on the request rather than a job
    that fails a second later — without the file being read and hashed twice.
    """
    if isinstance(spec, Attachment):
        return spec

    get = spec.get if isinstance(spec, dict) else lambda k, d=None: getattr(spec, k, d)

    raw_path = get("path")
    raw_data = get("data")
    name = get("name")
    handling: Handling = get("handling") or "preprocess"

    if handling not in ("preprocess", "native"):
        raise AttachmentError(
            f"attachment handling must be 'preprocess' or 'native', got {handling!r}"
        )

    if get("url") is not None:
        raise AttachmentError(
            "attachment 'url' is not supported: the runtime does not fetch caller-supplied "
            "addresses. Read the file and pass 'path' or 'data'."
        )
    if get("type") is not None or get("media_type") is not None:
        raise AttachmentError(
            "attachment type is determined from the file's content and cannot be declared"
        )

    if bool(raw_path) == bool(raw_data):
        raise AttachmentError("attachment needs exactly one of 'path' or 'data'")

    if raw_path:
        resolved = _resolve_path(str(raw_path), workspace_root)
        size = resolved.stat().st_size
        if size > MAX_BYTES:
            raise AttachmentError(
                f"attachment {raw_path!r} is {size} bytes, over the {MAX_BYTES}-byte limit"
            )
        data = resolved.read_bytes()
        source = str(resolved.relative_to(workspace_root.resolve()))
        name = name or resolved.name
    else:
        try:
            data = base64.b64decode(str(raw_data), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentError(f"attachment 'data' is not valid base64: {exc}") from exc
        if len(data) > MAX_BYTES:
            raise AttachmentError(
                f"attachment is {len(data)} bytes, over the {MAX_BYTES}-byte limit"
            )
        source = None
        name = name or "attachment"

    if not data:
        raise AttachmentError(f"attachment {name!r} is empty")

    return Attachment(
        media_type=_sniff(data),
        data=data,
        name=str(name),
        source=source,
        handling=handling,
    )


def ensure_carriable(attachments: list[Attachment]) -> None:
    """Refuse a type nothing in this build can put in a message, before the run starts.

    Not a capability table about *providers* — that idea was considered and rejected, because model
    support is per-model, goes stale, and a false refusal we own is worse than the model's own
    error. This is narrower: it is what **this runtime** can currently turn into a message block,
    which it knows for certain about itself.

    Checked here rather than only at message-building time because it is knowable at request time.
    Left to the run, a PDF would take a concurrency slot, start MCP servers, write a job row and
    then fail — a failure shape people stop reading. The set widens on its own as preprocessing and
    native passthrough land; it does not need maintaining per provider.
    """
    for att in attachments:
        if att.media_type not in RENDERABLE_MEDIA_TYPES:
            raise AttachmentError(
                f"attachment {att.name!r} is {att.media_type}, which cannot yet be put in a "
                f"message. Only images are carried today "
                f"({', '.join(sorted(RENDERABLE_MEDIA_TYPES))}); "
                f"documents and audio are sequenced behind preprocessing "
                f"(design/details/images-on-both-executors.md)."
            )


def resolve_all(specs: Any, workspace_root: Path) -> list[Attachment]:
    """Resolve every declared attachment, or refuse the run.

    All-or-nothing on purpose: a run that starts with three of four attachments is a run whose
    output cannot be trusted to mean what it appears to, and the missing one is exactly what
    nobody notices.
    """
    if not specs:
        return []
    resolved = [resolve_one(spec, workspace_root) for spec in specs]
    ensure_carriable(resolved)
    return resolved
