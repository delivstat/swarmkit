#!/usr/bin/env python
"""Check the documentation against the repository it describes.

    uv run python scripts/check_docs.py [--all]

Docs drift silently. Nothing fails, nothing warns, and the first person to notice is a user who
followed an instruction that stopped being true — which is how a `CLAUDE.md` came to say "this repo
is currently scaffolding only" while 1.200.0 shipped, and how the published portal kept a section
for an API removed three weeks earlier.

This checks the classes of claim that can be verified mechanically:

* **paths** — every file a doc points at exists
* **commands** — every `just <target>`, `swarmkit <cmd>` and `scripts/<file>.py` cited is real
* **versions** — version numbers stated in prose match the packages
* **counts** — "N archetypes", "N skills", "N schemas", "N providers"
* **removed features** — current-state docs must not describe things that were deleted

It deliberately does NOT check design notes (`design/details/`, `docs/site/design-notes/`) or dated
posts (`docs/*-post.md`, `docs/blog-*.md`) for staleness. A design note is a record of a decision at
a time and a launch post is dated by definition; expecting either to track the code would make them
useless as history. Only current-state docs are held to the current state.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Docs that must describe the code as it is TODAY. Design notes are excluded on purpose.
CURRENT_STATE = [
    "README.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "RELEASING.md",
    "llms.txt",
    "docs/notes/*.md",
    "docs/site/getting-started/*.md",
    "docs/site/guides/*.md",
    "docs/site/index.md",
    "docs/site/architecture/*.md",
    "docs/site/reference/*.md",
    "docs/site/sdlc-example/*.md",
    "docs/README.md",
    "docs/migration-guide.md",
    "design/IMPLEMENTATION-PLAN.md",
    "packages/*/CLAUDE.md",
    "reference/**/README.md",
    "examples/*/README.md",
]

#: Features removed from the runtime. A current-state doc naming one is describing a thing that is
#: gone — the exact failure the published portal shipped.
REMOVED = {
    "swarmkit orchestrator": "the bundled orchestrator was removed in 1.189.0",
    "swarmkit pipeline": "the pipeline CLI was removed in 1.189.0",
    "kind: StageGraph": "the StageGraph schema was removed in 1.189.0",
    "POST /pipelines": "the pipeline HTTP API was removed in 1.189.0",
    "/pipelines/sagas": "the saga API was removed in 1.189.0",
    "swarmkit eject": "eject was dropped before 1.0",
}

#: Docs whose SUBJECT is something that was removed or fixed. They must name the thing they are
#: about, so the removed-feature and dead-path checks would fire on every line. Excluding them is
#: not a loophole — a record of a deletion that may not mention the deleted thing is useless.
HISTORICAL = {
    "design/IMPLEMENTATION-PLAN.md",
    "docs/notes/pipeline-deprecation.md",
    "docs/notes/reported-bugs.md",
    "docs/notes/mcp-effects-migration.md",
    "docs/notes/release-version-discipline.md",
    "docs/notes/harness-parity-gaps.md",
}

#: Phrases whose presence is itself the bug: a doc asserting the project has not started.
CONTRADICTED = {
    "scaffolding only": "the repo ships four published packages",
    "has not started": "phases 1-5 are complete",
}


@dataclass
class Issue:
    file: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"  {self.file}\n      [{self.kind}] {self.detail}"


def _docs() -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in CURRENT_STATE:
        for p in REPO.glob(pattern):
            if p.is_file() and "node_modules" not in str(p):
                seen[p] = None
    return list(seen)


def _rel(p: Path) -> str:
    """Repo-relative where possible, the bare name otherwise.

    Tolerant so each check can be exercised on a temp file in a unit test — a checker whose parts
    can only run against the real repo is one whose tuning cannot be tested.
    """
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return p.name


def _outside_code(text: str) -> str:
    """The document with fenced code blocks removed.

    A path inside a fence is an illustration, not a reference — `getting-an-image-to-a-model.md`
    shows a deliberately broken image path as the *subject* of the guide. Checking it would mean
    the doc can only pass by not demonstrating the thing it exists to warn about.
    """
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


# --- the checks ---------------------------------------------------------------------------------


def check_paths(path: Path, text: str) -> list[Issue]:
    """Every markdown LINK a doc offers must resolve.

    Only links. A backticked filename is usually prose — "a declarative `adapter.yaml`" names a
    kind of file, not a path — and treating those as references produced 46 findings of which
    almost none were real, which is how a checker gets ignored.
    """
    if _rel(path) in HISTORICAL:
        return []
    out = []
    for raw in set(re.findall(r"\]\(([^)#\s]+)\)", _outside_code(text))):
        if raw.startswith(("http", "mailto:", "#", "<", "{")):
            continue
        candidates = [path.parent / raw, REPO / raw.lstrip("./"), REPO / raw]
        if not any(c.exists() for c in candidates):
            out.append(Issue(_rel(path), "path", f"link target {raw} does not exist"))
    return out


def check_just_targets(path: Path, text: str) -> list[Issue]:
    justfile = (REPO / "justfile").read_text()
    if _rel(path) in HISTORICAL:
        return []
    targets = set(re.findall(r"^([a-z][a-z0-9-]*):", justfile, re.M))
    out = []
    # Only in a code context. "just want to check" is English, and treating it as a target was the
    # single noisiest false positive in the first run.
    cited = set(re.findall(r"`just ([a-z][a-z0-9-]*)[^`]*`", text))
    cited |= set(re.findall(r"^(?:\$ )?just ([a-z][a-z0-9-]*)$", text, re.M))
    for t in cited:
        # `just demo-<feature>` is a placeholder, not a target.
        if t.endswith("-"):
            continue
        if t not in targets:
            out.append(Issue(_rel(path), "just", f"`just {t}` is not a target"))
    return out


def check_scripts(path: Path, text: str) -> list[Issue]:
    out = []
    for s in set(re.findall(r"scripts/([a-z_0-9]+\.py)", text)):
        if not (REPO / "scripts" / s).exists():
            out.append(Issue(_rel(path), "script", f"scripts/{s} does not exist"))
    return out


def _package_versions() -> dict[str, str]:
    out = {}
    for name, rel in {
        "swarmkit-runtime": "packages/runtime/pyproject.toml",
        "swarmkit-schema": "packages/schema/python/pyproject.toml",
        "swarmkit-webui": "packages/webui/pyproject.toml",
        "swarmkit-control-plane": "packages/control-plane/pyproject.toml",
    }.items():
        out[name] = tomllib.loads((REPO / rel).read_text())["project"]["version"]
    return out


def check_versions(path: Path, text: str) -> list[Issue]:
    """A doc stating "runtime is at vX" must state the current one."""
    current = _package_versions()["swarmkit-runtime"]
    out = []
    for stated in set(re.findall(r"[Rr]untime is at v?(\d+\.\d+\.\d+)", text)):
        if stated != current:
            out.append(Issue(_rel(path), "version", f"says runtime {stated}, actual {current}"))
    for stated in set(re.findall(r"\*\*Status:\*\* runtime v(\d+\.\d+\.\d+)", text)):
        if stated != current:
            out.append(
                Issue(_rel(path), "version", f"status says runtime {stated}, actual {current}")
            )
    return out


def _counts() -> dict[str, int]:
    return {
        "archetypes": len(list((REPO / "reference" / "archetypes").glob("*.yaml"))),
        "skills": len(list((REPO / "reference" / "skills").glob("*.yaml"))),
        "topologies": len(list((REPO / "reference" / "topologies").glob("*.yaml"))),
        "schemas": len(list((REPO / "packages/schema/schemas").glob("*.schema.json"))),
    }


def check_counts(path: Path, text: str) -> list[Issue]:
    """Counts of the REFERENCE library, and only those.

    Scoped to lines that are plainly about `reference/` — an example workspace saying "6
    topologies" is counting its own, and flagging that would be wrong.
    """
    if _rel(path) in HISTORICAL:
        return []
    actual = _counts()
    out = []
    for line in text.splitlines():
        low = line.lower()
        about_reference = "reference" in low or "ships" in low or "bundled" in low
        for noun in ("archetypes", "skills", "topologies"):
            for n in re.findall(rf"(\d+)\+? {noun}\b", line):
                if not about_reference:
                    continue
                if int(n) != actual[noun]:
                    out.append(
                        Issue(
                            _rel(path),
                            "count",
                            f"says {n} {noun}, actual {actual[noun]}: {line.strip()[:70]}",
                        )
                    )
        for n in re.findall(r"(\d+) canonical (?:artifact )?schemas", line):
            if int(n) != actual["schemas"]:
                out.append(
                    Issue(_rel(path), "count", f"says {n} schemas, actual {actual['schemas']}")
                )
    return out


#: Words that mark a mention as historical rather than a live instruction.
_PAST_TENSE = (
    # gone
    "remov",
    "deprecat",
    "dropped",
    "was ",
    "were ",
    "used to",
    "no longer",
    "gone",
    "before ",
    "went with",
    # never existed — a hypothetical used to argue about design
    "hypothetical",
    "inventing",
    "would be",
    # not yet. A doc that labels a section "(planned)" is being honest, not stale.
    "planned",
    "not yet",
    "future",
    "proposed",
)


def _is_historical_context(lines: list[str], i: int) -> bool:
    """Whether the mention at line `i` sits in prose that says the thing is not a live instruction.

    Two sources of context, because markdown carries meaning at two scales:

    * **A two-line window either side**, because prose wraps — "…was removed in 1.189.0, along
      with\n`kind: StageGraph`…" puts the verb and its object on different lines, and a per-line
      check flags the correction itself as the defect.
    * **The nearest preceding heading**, because a section titled "CLI access (planned)" governs
      every line under it, including a fenced example several lines down. Without this, a doc is
      penalised for honestly labelling something as not yet built.
    """
    window = " ".join(lines[max(0, i - 2) : i + 2]).lower()
    if any(w in window for w in _PAST_TENSE):
        return True
    for j in range(i, -1, -1):
        if lines[j].lstrip().startswith("#"):
            return any(w in lines[j].lower() for w in _PAST_TENSE)
    return False


def check_removed(path: Path, text: str) -> list[Issue]:
    """A current-state doc naming a removed feature, except where it says it was removed."""
    if _rel(path) in HISTORICAL:
        return []
    lines = text.splitlines()
    out = []
    for phrase, why in REMOVED.items():
        for i, line in enumerate(lines):
            if phrase in line and not _is_historical_context(lines, i):
                out.append(Issue(_rel(path), "removed", f"{phrase!r} — {why}"))
    return out


def check_contradicted(path: Path, text: str) -> list[Issue]:
    out = []
    for phrase, why in CONTRADICTED.items():
        if phrase in text.lower():
            out.append(Issue(_rel(path), "contradicted", f"{phrase!r} — {why}"))
    return out


def check_cli(path: Path, text: str) -> list[Issue]:
    """Every `swarmkit <command>` cited must be a real command."""
    if _rel(path) in HISTORICAL:
        return []
    known = _swarmkit_commands()
    if not known:
        return []
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if _is_historical_context(lines, i):
            continue
        for cmd in set(re.findall(r"swarmkit ([a-z][a-z0-9-]*)", line)):
            if cmd not in known and cmd not in {"runtime", "schema", "webui", "control-plane"}:
                out.append(Issue(_rel(path), "cli", f"`swarmkit {cmd}` is not a command"))
    return out


@lru_cache(maxsize=1)
def _swarmkit_commands() -> frozenset[str]:
    """The CLI's own command list, asked of the CLI rather than hard-coded.

    Cached because it costs a subprocess and every document asks. Returns empty when the CLI is not
    installed, which makes the check skip rather than fail — a docs check that needs a working
    editable install to run at all would be skipped in exactly the environments that need it.
    """
    proc = subprocess.run(
        ["uv", "run", "swarmkit", "--help"], cwd=REPO, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return frozenset()
    body = proc.stdout
    start = body.find("Commands")
    return (
        frozenset(re.findall(r"│\s+([a-z][a-z0-9-]*)\s", body[start:]))
        if start >= 0
        else frozenset()
    )


def check_justfile_commands() -> list[Issue]:
    """Every `swarmkit <cmd>` a just target runs must be a real command.

    The justfile is executable documentation: a target nobody has run since a command was removed
    fails only when someone follows the README that cites it. `just demo-gates` invoked
    `swarmkit gates --pipeline`, removed with the bundled pipeline in 1.189.0.
    """
    known = _swarmkit_commands()
    if not known:
        return []
    out = []
    target = "?"
    for line in (REPO / "justfile").read_text().splitlines():
        m = re.match(r"^([a-z][a-z0-9-]*):", line)
        if m:
            target = m.group(1)
        if line.lstrip().startswith("#"):
            continue  # a comment naming `.swarmkit audit store` is prose, not an invocation
        for cmd in re.findall(r"swarmkit ([a-z][a-z0-9-]*)", line):
            if cmd not in known:
                out.append(
                    Issue(
                        "justfile",
                        "just-cmd",
                        f"`just {target}` runs `swarmkit {cmd}`, not a command",
                    )
                )
    return out


CHECKS = (
    check_paths,
    check_just_targets,
    check_scripts,
    check_versions,
    check_counts,
    check_removed,
    check_contradicted,
    check_cli,
)


def main() -> int:
    docs = _docs()
    issues: list[Issue] = []
    for doc in sorted(docs):
        text = doc.read_text(encoding="utf-8", errors="replace")
        for check in CHECKS:
            issues.extend(check(doc, text))
    issues.extend(check_justfile_commands())

    print(f"checked {len(docs)} current-state documents\n")
    if not issues:
        print("OK — no documented claim contradicts the repository.")
        return 0

    by_kind: dict[str, list[Issue]] = {}
    for i in issues:
        by_kind.setdefault(i.kind, []).append(i)
    for kind, group in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        print(f"── {kind} ({len(group)})")
        for i in group:
            print(i)
        print()
    print(f"FAIL — {len(issues)} issue(s) across {len({i.file for i in issues})} file(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
