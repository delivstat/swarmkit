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

from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.resolver import resolve_workspace

from ._render import render_errors, render_success

# Files / directories enumerated for every pack. Paths are repo-relative.
_PROJECT_FILES = ("README.md", "CLAUDE.md", "llms.txt")
_AUTHORITATIVE_DESIGN = (
    "design/SwarmKit-Design-v0.6.md",
    "design/IMPLEMENTATION-PLAN.md",
)
_NOTES_EXCLUDE = {"README.md", "_template.md"}
# The user-facing reference, generated where it is an inventory (CLI commands, environment
# variables, HTTP routes) and hand-written where it is a contract (every artifact kind, storage,
# events, connections). Without it the pack had no complete list of commands or endpoints — only
# the prose in llms.txt — which is the first thing an LLM asked to drive SwarmKit needs.
_REFERENCE_GLOB = "docs/site/reference/*.md"
#: A design note whose front matter carries one of these is history: it describes something that
#: was later removed or replaced, and it says by what. It is still shipped — the record of why is
#: worth reading — but in its own section, after everything current, under a banner.
_HISTORICAL_STATUSES = {"superseded", "removed", "withdrawn"}

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
    lean: bool = False,
    now: datetime | None = None,
) -> str:
    """Assemble the full knowledge-pack markdown document.

    ``lean`` is the pack to paste. The full pack is ~610k tokens, three quarters of it the 140
    per-feature design notes (the *why*), and fits no context window but the largest. Lean keeps
    what an LLM needs to *use* SwarmKit — overview, the generated reference, the design doc,
    guides, cross-cutting notes, schemas — at ~190k tokens, and drops the design notes, the
    roadmap and the fixtures. Ask the full pack when the question is why something is the way it
    is.

    ``now`` is injectable so tests can pin a timestamp.
    """
    sections = list(
        _corpus_sections(repo_root, include_fixtures=include_fixtures and not lean, lean=lean)
    )
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
        lean=lean,
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


def _corpus_sections(
    repo_root: Path, *, include_fixtures: bool, lean: bool = False
) -> Iterator[_Section]:
    yield _Section(
        heading="Project overview",
        preamble="Top-level orientation files.",
        files=_existing(repo_root, _PROJECT_FILES),
    )
    yield _Section(
        heading="Reference",
        preamble=(
            "The user-facing reference. `cli.md` and `http-api.md` are generated from the CLI and "
            "the server's OpenAPI document — every command and every route, current by "
            "construction. The rest is the contract for each artifact kind (topology, workspace, "
            "skills, archetypes, funnel, contract, role registry, trigger, executor adapter, model "
            "provider), storage, events, connections and telemetry. Prefer these over a design "
            "note when the two disagree about a name or a flag: the note says why, the reference "
            "says what shipped."
        ),
        files=_discover_glob(repo_root, _REFERENCE_GLOB),
    )
    yield _Section(
        heading="Authoritative design",
        preamble=(
            "The v0.6 design doc is canon for the architecture and its reasons; the plan is the "
            "roadmap as it was written. Where either disagrees with the reference above, the "
            "code shipped differently — the bundled pipeline layer (`kind: StageGraph`, the saga "
            "controller, `swarmkit orchestrator`, `swarmkit pipeline`) the plan calls shipped was "
            "removed in 1.189.0; see `design/details/extracting-the-pipeline.md`."
        ),
        files=_existing(repo_root, _AUTHORITATIVE_DESIGN[:1] if lean else _AUTHORITATIVE_DESIGN),
    )
    if not lean:
        current, historical = _split_notes(_discover_glob(repo_root, "design/details/*.md"))
        yield _Section(
            heading="Per-feature design notes",
            preamble=(
                "One per feature, stating goal, non-goals, API, test plan, demo. Authoritative "
                "for the *why* of individual features; each begins with its status. Notes whose "
                "subject was later removed are not here — they are in the final section, "
                "'Historical design notes', with the note that replaced them."
            ),
            files=current,
        )
        # Deferred to the end, after everything current, so a reader that stops early has only
        # read things that exist.
        _pending_historical.append(historical)
    yield _Section(
        heading="Cross-cutting notes",
        preamble="Discipline / gotcha notes that span packages.",
        files=_discover_glob(repo_root, "docs/notes/*.md"),
    )
    yield _Section(
        heading="How-to guides",
        preamble=(
            "Task-oriented guides — building swarms, getting an image to a model, validating "
            "output, memory bindings, authoring harness adapters, model selection, serve auth. "
            "How to actually do the thing, with runnable commands."
        ),
        files=_discover_glob(repo_root, "docs/site/guides/*.md")
        + _discover_glob(repo_root, "docs/guides/*.md"),
    )
    if not lean:
        # Full pack only: 22 levels is ~50k tokens, and the lean pack's job is to fit a context
        # window with the reference and the design doc in it. The guides above carry the recipe.
        yield _Section(
            heading="Tutorials",
            preamble=(
                "Twenty-two progressive levels, one shipped capability each from level 17 on; "
                "the `just demo-*` target each level names is runnable on the mock provider. Read "
                "these for the ORDER in which features are meant to be adopted, not for the field "
                "list — that is the Reference."
            ),
            files=_discover_glob(repo_root, "docs/site/tutorials/*.md"),
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
    if not lean and _pending_historical:
        historical = _pending_historical.pop()
        yield _Section(
            heading="Historical design notes",
            preamble=(
                "**These describe things SwarmKit no longer has.** Each note's front matter says "
                "`status: superseded` and names the note that replaced it. They are kept because "
                "the reasoning is part of the record — do not answer a 'how do I' question from "
                "them. In particular: the bundled pipeline (`kind: StageGraph`, saga controller, "
                "`swarmkit orchestrator`, `swarmkit pipeline`, `POST /pipelines/*`) was removed "
                "in 1.189.0, and channel skills were replaced by `GET /events` + `events:` sinks "
                "in 1.216.0."
            ),
            files=historical,
        )


#: Historical notes found while walking the current ones, yielded last. Module-level rather than
#: threaded through the generator so `_corpus_sections` stays a flat sequence of yields.
_pending_historical: list[tuple[_File, ...]] = []


def _front_matter(text: str) -> dict[str, str]:
    """The YAML front matter of a note as flat `key: value` strings, or {} when there is none.

    Deliberately not a YAML parse: front matter here is a handful of scalar lines, and pulling a
    parser into a doc bundler for `status:` would be the heavier dependency.
    """
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith(" "):
            out[key.strip()] = value.strip().strip("'\"")
    return out


def note_status(text: str) -> str:
    """A note's declared status, lower-cased, or ''. Reads the front matter first and falls back
    to a `**Status:** …` line, which older notes use."""
    fm = _front_matter(text)
    if fm.get("status"):
        return fm["status"].lower()
    for line in text.splitlines()[:12]:
        stripped = line.strip().strip("*").strip()
        if stripped.lower().startswith("status:"):
            return stripped.split(":", 1)[1].strip().strip("*").strip().lower()
    return ""


def is_historical(text: str) -> bool:
    return note_status(text).split()[0] in _HISTORICAL_STATUSES if note_status(text) else False


def _split_notes(files: tuple[_File, ...]) -> tuple[tuple[_File, ...], tuple[_File, ...]]:
    current: list[_File] = []
    historical: list[_File] = []
    for f in files:
        (historical if is_historical(f.read()) else current).append(f)
    return tuple(current), tuple(historical)


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
    "design/details/topology-schema-v1.md §X') so the user can verify.\n\n"
    "How to weigh the sections, when they disagree: **the Reference section says what "
    "shipped** — `cli.md` and `http-api.md` are generated from the code, and the artifact "
    "references are the current contracts. **Schemas are the source of truth for artifact "
    "shape.** The design doc and the per-feature notes say *why*, and a note's first lines "
    "carry its status; a note in 'Historical design notes' describes something that no longer "
    "exists and is there only for the record. `llms.txt` is the short, current summary and a "
    "good place to start."
)


def _render_header(
    *,
    total_files: int,
    total_bytes: int,
    workspace: Path | None,
    now: datetime,
    lean: bool = False,
) -> str:
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    overlay = f"workspace overlay: `{workspace}`" if workspace else "no workspace overlay"
    kb = total_bytes / 1024
    # ~4 bytes per token is the usual English/markdown ratio; a reader deciding whether the pack
    # fits a context window needs the order of magnitude, not a tokenizer.
    ktok = total_bytes / 4 / 1000
    kind = (
        "lean pack (`--lean`: no per-feature design notes, roadmap or fixtures)"
        if lean
        else "full pack (`--lean` for one that fits a context window)"
    )
    return (
        "# SwarmKit Knowledge Pack\n\n"
        f"> Generated by `swarmkit knowledge-pack` on {ts}. {kind}.\n"
        f"> Contains {total_files} files, ~{kb:.0f} KB, roughly {ktok:.0f}k tokens. {overlay}."
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
