#!/usr/bin/env python
"""Regenerate docs/site/releases/changelog.md from the annotated git tags.

    uv run python scripts/build_changelog.py

The changelog has always *claimed* to be "generated from the annotated git tags" and never was —
it was hand-maintained, so it drifted 33 versions behind before anyone noticed. A claim about
where a document comes from should be executable, otherwise it is just a hope.

Every release already writes a tag subject in the shape
``SwarmKit v1.131.0 - <what changed>``, which is the summary a reader wants. This reads those,
groups by month, and rewrites the generated block. The hand-written preamble and the per-series
notes below it are preserved.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "docs/site/releases/changelog.md"

#: Everything above this marker is hand-written and kept as-is.
START = "<!-- BEGIN GENERATED: tags -->"
END = "<!-- END GENERATED: tags -->"

_VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _tags() -> list[tuple[str, str, str]]:
    """(tag, YYYY-MM-DD, subject) for every v1.* tag, newest first."""
    out = subprocess.run(
        [
            "git",
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname:short)|%(creatordate:short)|%(contents:subject)",
            "refs/tags/v1.*",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=CHANGELOG.parents[3],
    ).stdout

    rows = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        tag, date, subject = parts
        if not _VERSION.match(tag):
            continue
        rows.append((tag, date, subject.strip()))
    return rows


def _summary(tag: str, subject: str) -> str:
    """The part of the tag subject after 'SwarmKit vX.Y.Z —', or the whole thing."""
    if tag in _OVERRIDES:
        return _OVERRIDES[tag]
    for dash in ("—", "-"):
        marker = f"{tag} {dash} "
        if marker in subject:
            return subject.split(marker, 1)[1].strip()
    # A tag written without the conventional prefix still deserves a line.
    return subject.removeprefix(f"SwarmKit {tag}").strip(" —-") or subject


#: Tags whose subject is just the bare version, so there is nothing to summarise from. These
#: sentences come from the hand-written changelog that preceded this script — losing them to
#: regeneration would be the generator making the document worse than what it replaced.
_OVERRIDES = {
    "v1.2.49": "maintenance release",
    "v1.0.31": "ASCII banner + suppress noisy MCP logs",
    "v1.0.13": "maintenance release",
    "v1.0.0": "launch prep — skills catalogue, docs site, Docker, PyPI, CHANGELOG",
}

_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _month_heading(date: str) -> str:
    year, month, _ = date.split("-")
    return f"{_MONTHS[int(month) - 1]} {year}"


def build() -> str:
    lines: list[str] = []
    current_month = ""
    for tag, date, subject in _tags():
        heading = _month_heading(date)
        if heading != current_month:
            lines.append(f"\n## {heading}\n")
            current_month = heading
        lines.append(f"- **{tag}** ({date}) — {_summary(tag, subject)}")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    text = CHANGELOG.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print(f"error: {CHANGELOG} has no generated block ({START} / {END})", file=sys.stderr)
        return 1
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    CHANGELOG.write_text(f"{head}{START}\n\n{build()}\n{END}{tail}", encoding="utf-8")
    print(f"Wrote {CHANGELOG} ({len(_tags())} tags).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
