"""Attachments on a run — resolution, refusals, and the scoping rule that got this reverted once.

``design/details/images-on-both-executors.md``. The assertions here are grouped by what they
protect rather than by function, because the interesting failures are not "the code raised the
wrong exception" — they are a file silently not arriving, an image reaching a text-only supervisor,
or a caller's claim about content being believed.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from swarmkit_runtime.attachments import (
    MAX_BYTES,
    Attachment,
    AttachmentError,
    resolve_all,
    resolve_one,
)

# A one-pixel PNG. Real bytes rather than a fixture file, so the sniffing tests assert against
# something that genuinely is a PNG rather than something named like one.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "gate.png").write_bytes(PNG)
    (tmp_path / "report.pdf").write_bytes(PDF)
    return tmp_path


# --- the type is read from the bytes, never from the caller -----------------------------------


def test_type_comes_from_content_not_extension(workspace: Path) -> None:
    """A PNG named .pdf is a PNG.

    The whole reason there is no caller-supplied type field: the bytes are about to be forwarded to
    a third-party model, and a name is not evidence.
    """
    (workspace / "misnamed.pdf").write_bytes(PNG)
    assert resolve_one({"path": "misnamed.pdf"}, workspace).media_type == "image/png"


def test_unknown_bytes_are_not_guessed(workspace: Path) -> None:
    """An unrecognised file is octet-stream, not a hopeful guess at an image.

    It will be refused by name at the message mapping, which is a better outcome than arriving at a
    provider as a corrupt image block.
    """
    (workspace / "mystery.bin").write_bytes(b"\x01\x02\x03\x04" * 8)
    assert resolve_one({"path": "mystery.bin"}, workspace).media_type == "application/octet-stream"


@pytest.mark.parametrize("field", ["type", "media_type"])
def test_declaring_a_type_is_refused(workspace: Path, field: str) -> None:
    with pytest.raises(AttachmentError, match="determined from the file's content"):
        resolve_one({"path": "snapshots/gate.png", field: "image/png"}, workspace)


# --- containment: a caller's path is no more trusted than a model's ----------------------------


@pytest.mark.parametrize(
    "bad",
    ["../outside.png", "snapshots/../../outside.png", "/etc/passwd"],
)
def test_paths_out_of_the_workspace_are_refused(workspace: Path, bad: str) -> None:
    (workspace.parent / "outside.png").write_bytes(PNG)
    with pytest.raises(AttachmentError, match=r"escapes the workspace|workspace-relative"):
        resolve_one({"path": bad}, workspace)


def test_symlink_out_of_the_workspace_is_refused(workspace: Path) -> None:
    """Resolved, then checked — a symlink is the version of this that looks relative."""
    outside = workspace.parent / "secret.png"
    outside.write_bytes(PNG)
    (workspace / "link.png").symlink_to(outside)
    with pytest.raises(AttachmentError, match="escapes the workspace"):
        resolve_one({"path": "link.png"}, workspace)


def test_missing_file_names_the_path(workspace: Path) -> None:
    with pytest.raises(AttachmentError, match=r"nope\.png"):
        resolve_one({"path": "nope.png"}, workspace)


def test_a_directory_is_not_an_attachment(workspace: Path) -> None:
    with pytest.raises(AttachmentError, match="not a file"):
        resolve_one({"path": "snapshots"}, workspace)


# --- source shape: exactly one, and not the two that cannot be re-read -------------------------


def test_needs_exactly_one_source(workspace: Path) -> None:
    with pytest.raises(AttachmentError, match="exactly one"):
        resolve_one({}, workspace)
    with pytest.raises(AttachmentError, match="exactly one"):
        resolve_one({"path": "snapshots/gate.png", "data": base64.b64encode(PNG)}, workspace)


def test_url_is_refused_with_the_reason(workspace: Path) -> None:
    """Not a schema error — a reason.

    Some providers do accept URLs, so the next person to want this will assume it was an oversight
    unless the refusal says why.
    """
    with pytest.raises(AttachmentError, match="does not fetch caller-supplied"):
        resolve_one({"url": "https://example.com/x.png"}, workspace)


def test_inline_data_round_trips(workspace: Path) -> None:
    att = resolve_one({"data": base64.b64encode(PNG).decode(), "name": "inline.png"}, workspace)
    assert att.media_type == "image/png"
    assert att.data == PNG
    assert att.source is None
    assert att.name == "inline.png"


def test_bad_base64_is_refused(workspace: Path) -> None:
    with pytest.raises(AttachmentError, match="valid base64"):
        resolve_one({"data": "not base64 at all!!"}, workspace)


def test_empty_file_is_refused(workspace: Path) -> None:
    (workspace / "empty.png").write_bytes(b"")
    with pytest.raises(AttachmentError, match="empty"):
        resolve_one({"path": "empty.png"}, workspace)


def test_oversized_is_refused_with_its_size(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swarmkit_runtime.attachments.MAX_BYTES", 16)
    (workspace / "big.png").write_bytes(PNG)
    with pytest.raises(AttachmentError, match=r"\d+ bytes, over the 16-byte limit"):
        resolve_one({"path": "big.png"}, workspace)


def test_default_ceiling_is_sane() -> None:
    """A regression guard on the env-var parse, not on the number itself."""
    assert MAX_BYTES > 1024 * 1024


# --- handling is an intent, and it is validated ------------------------------------------------


def test_handling_defaults_to_preprocess(workspace: Path) -> None:
    assert resolve_one({"path": "snapshots/gate.png"}, workspace).handling == "preprocess"


def test_unknown_handling_is_refused(workspace: Path) -> None:
    with pytest.raises(AttachmentError, match="preprocess"):
        resolve_one({"path": "snapshots/gate.png", "handling": "magic"}, workspace)


# --- all-or-nothing, and idempotence at the boundary -------------------------------------------


def test_one_bad_attachment_fails_the_whole_set(workspace: Path) -> None:
    """A run that starts with three of four attachments is a run whose output cannot be trusted,
    and the missing one is exactly what nobody notices."""
    with pytest.raises(AttachmentError):
        resolve_all([{"path": "snapshots/gate.png"}, {"path": "gone.png"}], workspace)


def test_resolved_attachments_pass_through_unchanged(workspace: Path) -> None:
    """What lets the HTTP layer validate at the boundary without the file being read twice."""
    once = resolve_one({"path": "snapshots/gate.png"}, workspace)
    twice = resolve_all([once], workspace)
    assert twice == [once]
    assert twice[0] is once


def test_no_attachments_is_an_empty_list(workspace: Path) -> None:
    assert resolve_all(None, workspace) == []
    assert resolve_all([], workspace) == []


# --- what the audit record keeps, and what it must not -----------------------------------------


def test_audit_record_carries_metadata_and_never_bytes(workspace: Path) -> None:
    att = resolve_one({"path": "snapshots/gate.png"}, workspace)
    record = att.audit_record()

    assert record["name"] == "gate.png"
    assert record["media_type"] == "image/png"
    assert record["size"] == len(PNG)
    assert record["source"] == "snapshots/gate.png"
    assert record["sha256"] == att.sha256

    # The bytes, in any encoding, must not be reachable from the record. An audit log is meant to
    # stay readable and bounded; content in it is both a size problem and a disclosure one.
    serialised = repr(record)
    assert att.b64 not in serialised
    assert str(PNG) not in serialised


def test_digest_identifies_the_file(workspace: Path) -> None:
    """The digest is what makes the audit reference checkable once the source may have changed."""
    a = resolve_one({"path": "snapshots/gate.png"}, workspace)
    (workspace / "copy.png").write_bytes(PNG)
    b = resolve_one({"path": "copy.png"}, workspace)
    assert a.sha256 == b.sha256

    (workspace / "other.jpg").write_bytes(JPEG)
    assert resolve_one({"path": "other.jpg"}, workspace).sha256 != a.sha256


def test_name_falls_back_to_the_filename(workspace: Path) -> None:
    assert resolve_one({"path": "snapshots/gate.png"}, workspace).name == "gate.png"
    assert resolve_one({"data": base64.b64encode(PNG).decode()}, workspace).name == "attachment"


def test_attachment_is_frozen() -> None:
    """Seeded once and read by the prompt builder — nothing should be able to rewrite it mid-run."""
    att = Attachment(media_type="image/png", data=PNG, name="x.png")
    with pytest.raises(AttributeError):
        att.name = "y.png"  # type: ignore[misc]


# --- unrenderable types are refused before a run starts, not during one -------------------------


def test_non_image_is_refused_at_resolve_time(workspace: Path) -> None:
    """Knowable at request time, so it fails there.

    Left to the run, a PDF would take a concurrency slot, start MCP servers, write a job row and
    then fail — a failure shape people stop reading. This is not a capability table about
    providers (that was considered and rejected); it is what this runtime can put in a message,
    which it knows for certain about itself.
    """
    with pytest.raises(AttachmentError, match=r"cannot yet be put in a message"):
        resolve_all([{"path": "report.pdf"}], workspace)


def test_the_refusal_names_the_file_and_what_it_is(workspace: Path) -> None:
    with pytest.raises(AttachmentError) as exc:
        resolve_all([{"path": "report.pdf"}], workspace)
    assert "report.pdf" in str(exc.value)
    assert "application/pdf" in str(exc.value)


def test_images_of_every_carried_type_pass(workspace: Path) -> None:
    (workspace / "a.gif").write_bytes(b"GIF89a" + b"\x00" * 16)
    (workspace / "b.jpg").write_bytes(JPEG)
    (workspace / "c.webp").write_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 16)
    got = resolve_all(
        [{"path": p} for p in ("snapshots/gate.png", "a.gif", "b.jpg", "c.webp")], workspace
    )
    assert [a.media_type for a in got] == ["image/png", "image/gif", "image/jpeg", "image/webp"]
