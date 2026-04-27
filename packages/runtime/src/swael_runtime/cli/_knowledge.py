"""Knowledge-pack generator for ``swarmkit knowledge-pack``.

Produces a single paste-ready markdown document bundling the SwarmKit
corpus (and optionally a target workspace) for LLM-assisted help flows.
See ``design/details/knowledge-pack-cli.md``.
"""

from __future__ import annotations

import contextlib
import io
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from swael_runtime.errors import ResolutionErrors
from swael_runtime.resolver import resolve_workspace

from ._render import render_errors, render_success

# Files / directories enumerated for every pack. Paths are repo-relative.
_PROJECT_FILES = ("README.md", "CLAUDE.md", "llms.txt")
_AUTHORITATIVE_DESIGN = (
    "design/SwarmKit-Design-v0.6.md",
    "design/IMPLEMENTATION-PLAN.md",
)
_NOTES_EXCLUDE = {"README.md", "_template.md"}

# Workspace-overlay subdirectories scanned in this order.
_WORKSPACE_SUBDIRS = ("topologies", "archetypes", "skills", "triggers", "schedules")


@dataclass(frozen=True)
class _File:
    """A single file included in the pack."""

    repo_path: str  # repo-relative, forward slashes
    abs_path: Path

    def read(self) -> str:
        return self.abs_path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class _Section:
    """A grouped set of files with a heading and a short preamble."""

    heading: str
    preamble: str
    files: tuple[_File, ...]


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` until a directory contains ``CLAUDE.md`` + ``design/``.

    Returns ``None`` if no such ancestor exists. The two markers together
    are distinctive enough that false positives are unrealistic.
    """
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "CLAUDE.md").is_file() and (candidate / "design").is_dir():
            return candidate
    return None


def build_pack(
    repo_root: Path,
    *,
    workspace: Path | None = None,
    include_fixtures: bool = True,
    now: datetime | None = None,
) -> str:
    """Assemble the full knowledge-pack markdown document.

    ``now`` is injectable so tests can pin a timestamp.
    """
    sections = list(_corpus_sections(repo_root, include_fixtures=include_fixtures))
    workspace_section = _workspace_section(workspace, repo_root) if workspace else None

    total_files = sum(len(s.files) for s in sections)
    total_bytes = sum(len(f.read()) for s in sections for f in s.files)
    if workspace_section is not None:
        total_files += len(workspace_section.files)
        total_bytes += sum(len(f.read()) for f in workspace_section.files)

    header = _render_header(
        total_files=total_files,
        total_bytes=total_bytes,
        workspace=workspace,
        now=now or datetime.now(tz=UTC),
    )

    parts = [header, _ABOUT_PARAGRAPH]
    for section in sections:
        parts.append(_render_section(section))
    if workspace_section is not None and workspace is not None:
        parts.append(_render_section(workspace_section))
        parts.append(_render_validation(workspace))

    return "\n\n".join(parts).rstrip() + "\n"


# ---- section discovery ------------------------------------------------


def _corpus_sections(repo_root: Path, *, include_fixtures: bool) -> Iterator[_Section]:
    yield _Section(
        heading="Project overview",
        preamble="Top-level orientation files.",
        files=_existing(repo_root, _PROJECT_FILES),
    )
    yield _Section(
        heading="Authoritative design",
        preamble="The v0.6 design doc is canon; the plan tracks progress against it.",
        files=_existing(repo_root, _AUTHORITATIVE_DESIGN),
    )
    yield _Section(
        heading="Per-feature design notes",
        preamble=(
            "One per feature, stating goal, non-goals, API, test plan, demo. "
            "Authoritative contracts for individual features."
        ),
        files=_discover_glob(repo_root, "design/details/*.md"),
    )
    yield _Section(
        heading="Cross-cutting notes",
        preamble="Discipline / gotcha notes that span packages.",
        files=_discover_glob(repo_root, "docs/notes/*.md"),
    )
    yield _Section(
        heading="Per-package invariants",
        preamble="Package-specific CLAUDE.md files — stricter than the root one.",
        files=_discover_glob(repo_root, "packages/*/CLAUDE.md"),
    )
    yield _Section(
        heading="Canonical schemas",
        preamble="JSON Schema 2020-12. The shape of every artifact in a workspace.",
        files=_discover_glob(repo_root, "packages/schema/schemas/*.json"),
    )
    if include_fixtures:
        yield _Section(
            heading="Schema fixtures",
            preamble=(
                "Concrete valid + invalid examples of every artifact. Invalid "
                "fixtures double as 'what the errors look like' examples."
            ),
            files=_discover_glob(repo_root, "packages/schema/tests/fixtures/**/*.yaml"),
        )


def _existing(repo_root: Path, repo_paths: Sequence[str]) -> tuple[_File, ...]:
    files: list[_File] = []
    for rel in repo_paths:
        abs_path = repo_root / rel
        if abs_path.is_file():
            files.append(_File(repo_path=rel, abs_path=abs_path))
    return tuple(files)


def _discover_glob(repo_root: Path, pattern: str) -> tuple[_File, ...]:
    matches = sorted(repo_root.glob(pattern))
    files: list[_File] = []
    for abs_path in matches:
        if not abs_path.is_file():
            continue
        if abs_path.name in _NOTES_EXCLUDE:
            continue
        rel = abs_path.relative_to(repo_root).as_posix()
        files.append(_File(repo_path=rel, abs_path=abs_path))
    return tuple(files)


def _workspace_section(workspace: Path, repo_root: Path) -> _Section:
    files: list[_File] = []
    ws_yaml = workspace / "workspace.yaml"
    if ws_yaml.is_file():
        files.append(_File(repo_path=_display_path(ws_yaml, repo_root), abs_path=ws_yaml))
    for subdir_name in _WORKSPACE_SUBDIRS:
        subdir = workspace / subdir_name
        if not subdir.is_dir():
            continue
        for child in sorted(subdir.rglob("*.yaml")):
            files.append(_File(repo_path=_display_path(child, repo_root), abs_path=child))
    return _Section(
        heading="Current workspace",
        preamble=(
            "The workspace the user is asking about. Read this alongside "
            "the schemas and design notes above."
        ),
        files=tuple(files),
    )


def _display_path(path: Path, repo_root: Path) -> str:
    # Prefer a repo-relative display path; fall back to absolute if the
    # workspace lives outside the checkout.
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


# ---- rendering --------------------------------------------------------


_ABOUT_PARAGRAPH = (
    "## About this pack\n\n"
    "You are an LLM reading the complete SwarmKit reference material. The user "
    "has pasted this pack to get help with a SwarmKit question. When answering, "
    "cite which file you're drawing from (e.g. 'per "
    "design/details/topology-schema-v1.md §X') so the user can verify. The "
    "design doc is canon; per-feature notes under design/details/ refine it. "
    "Schemas are the source of truth for artifact shape."
)


def _render_header(
    *,
    total_files: int,
    total_bytes: int,
    workspace: Path | None,
    now: datetime,
) -> str:
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    overlay = f"workspace overlay: `{workspace}`" if workspace else "no workspace overlay"
    kb = total_bytes / 1024
    return (
        "# SwarmKit Knowledge Pack\n\n"
        f"> Generated by `swarmkit knowledge-pack` on {ts}.\n"
        f"> Contains {total_files} files, ~{kb:.1f} KB. {overlay}."
    )


def _render_section(section: _Section) -> str:
    if not section.files:
        return f"---\n\n## {section.heading}\n\n{section.preamble}\n\n_(empty)_"
    parts = [f"---\n\n## {section.heading}\n\n{section.preamble}"]
    for f in section.files:
        parts.append(_render_file(f))
    return "\n\n".join(parts)


def _render_file(f: _File) -> str:
    body = f.read()
    lang = _lang_for(f.repo_path)
    if lang is None:
        # Markdown and plain text included inline — LLM readers handle
        # nested headings fine, and re-fencing markdown inside markdown
        # creates escaping noise.
        return f"### `{f.repo_path}`\n\n{body.rstrip()}"
    return f"### `{f.repo_path}`\n\n```{lang}\n{body.rstrip()}\n```"


def _lang_for(repo_path: str) -> str | None:
    suffix = os.path.splitext(repo_path)[1].lower()
    if suffix in (".md", ".markdown", ".txt", ""):
        return None
    if suffix in (".yaml", ".yml"):
        return "yaml"
    if suffix == ".json":
        return "json"
    return ""  # unknown — plain fence without highlighting


def _render_validation(workspace: Path) -> str:
    """Capture `swarmkit validate` output verbatim for the pack."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            resolved = resolve_workspace(workspace)
        except ResolutionErrors as exc:
            body = render_errors(list(exc.errors), workspace_root=workspace, color=False)
            status = "errors"
        else:
            body = render_success(resolved, tree=True, color=False)
            status = "ok"
    return (
        f"---\n\n## Validation output (`{status}`)\n\n"
        f"What `swarmkit validate {workspace} --tree --no-color` prints today.\n\n"
        "```\n" + body.rstrip() + "\n```"
    )
