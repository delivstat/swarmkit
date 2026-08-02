"""`--workspace` confines the docs-reader; it is a boundary, not just a base path.

Reported 2026-08-01 against 1.129.0: an absolute path was returned untouched and `..` was never
collapsed, so any agent granted `read_text` / `read_csv` / `read_svg` / `read_drawio` /
`list_files` could read anything the runtime user could — `.env`, credential files, anything a
project gitignores. Re-rooting the server at a subdirectory did **not** mitigate it, which is what
made it dangerous: the obvious workaround failed silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import swarmkit_runtime.docs_reader._server as server
from swarmkit_runtime.docs_reader._server import PathOutsideWorkspaceError, _resolve_path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "note.txt").write_text("inside")
    (tmp_path / "secret.env").write_text("API_KEY=hunter2")
    monkeypatch.setattr(server, "_workspace_root", root)
    monkeypatch.delenv("SWARMKIT_DOCS_READER_ALLOW_OUTSIDE", raising=False)
    return root


def test_a_relative_path_inside_resolves(workspace: Path) -> None:
    assert _resolve_path("docs/note.txt") == (workspace / "docs" / "note.txt").resolve()


def test_the_root_itself_is_allowed(workspace: Path) -> None:
    """`list_files` on the workspace root must keep working."""
    assert _resolve_path(".") == workspace.resolve()


def test_an_absolute_path_outside_is_refused(workspace: Path, tmp_path: Path) -> None:
    with pytest.raises(PathOutsideWorkspaceError, match="outside the workspace root"):
        _resolve_path(str(tmp_path / "secret.env"))


def test_a_traversal_is_refused(workspace: Path) -> None:
    """`../..` escaped before — re-rooting the server was therefore not a mitigation."""
    with pytest.raises(PathOutsideWorkspaceError):
        _resolve_path("../secret.env")


def test_a_traversal_that_returns_inside_is_allowed(workspace: Path) -> None:
    """Confinement is about where the path LANDS, not whether it contains `..`."""
    assert _resolve_path("docs/../docs/note.txt") == (workspace / "docs" / "note.txt").resolve()


def test_a_symlink_pointing_out_is_refused(workspace: Path, tmp_path: Path) -> None:
    """Resolved BEFORE the check — otherwise a link inside the workspace is a hole through it."""
    link = workspace / "escape.env"
    link.symlink_to(tmp_path / "secret.env")
    with pytest.raises(PathOutsideWorkspaceError):
        _resolve_path("escape.env")


def test_the_classic_targets_are_refused(workspace: Path) -> None:
    for path in ("/etc/passwd", "/etc/shadow", "~/.ssh/id_rsa"):
        with pytest.raises(PathOutsideWorkspaceError):
            _resolve_path(path if path.startswith("/") else str(Path(path).expanduser()))


def test_the_opt_out_is_explicit_and_off_by_default(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unconfined read is a deliberate choice with a name that says what it does."""
    with pytest.raises(PathOutsideWorkspaceError):
        _resolve_path(str(tmp_path / "secret.env"))

    monkeypatch.setenv("SWARMKIT_DOCS_READER_ALLOW_OUTSIDE", "1")
    assert _resolve_path(str(tmp_path / "secret.env")) == (tmp_path / "secret.env").resolve()


def test_every_reading_tool_goes_through_the_confined_resolver() -> None:
    """The fix is one function; this pins that no tool bypasses it — including `view_image`, which
    the report flagged as presumed-vulnerable and untested."""
    src = Path(server.__file__).read_text()
    for tool in ("read_text", "read_csv", "read_svg", "read_drawio", "list_files", "view_image"):
        marker = f"def {tool}("
        assert marker in src, f"{tool} not found — did it get renamed?"
        body = src.split(marker, 1)[1].split("\n@", 1)[0]
        assert "_resolve_path(" in body, f"{tool} does not use the confined resolver"
