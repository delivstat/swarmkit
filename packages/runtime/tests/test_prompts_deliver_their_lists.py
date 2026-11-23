"""A numbered list in a shipped prompt reaches the model as a list (#986).

A folded `>` scalar joins lines. Written as one step per line, a list is joined into a single
sentence. Written with indented continuations, the step starts are joined while the continuations
keep hard breaks mid-clause. Either way, the file reads as a clean list while the parsed text the
model receives is a different document. That's why this asserts on the **parsed** strings, never
the file. `test-writer.yaml` delivered steps 3 and 4 as one line, and six reference skills and
archetypes delivered their whole checklist as one sentence.

The rule: in any string that numbers its steps (has a `1. ` and a `2. ` item), every numbered
item starts a line.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
#: `N. ` followed by an item, not preceded by a word character or a dot (so `v1.2`, `step 3.`
#: and `e.g. 2. ` do not count as list items).
_ITEM = re.compile(r"(^|[^\n])(?<![\w.])(\d{1,2})\. (?=[A-Z`(])", re.M)
#: A line that is itself a step, so a sentence like "use the line number from step 3. This…"
#: is not mistaken for a welded item.
_STEP_WORD = re.compile(r"\bstep \d{1,2}\. ", re.I)


def _strings(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, (*path, str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, (*path, str(index)))
    elif isinstance(node, str):
        yield "/".join(path), node


def _welded(text: str) -> list[str]:
    numbers = {int(m.group(2)) for m in _ITEM.finditer(text)}
    if not {1, 2} <= numbers:
        return []
    return [
        text[max(0, m.start() - 30) : m.end() + 15]
        for m in _ITEM.finditer(text)
        if m.group(1) not in ("", "\n")
        and not _STEP_WORD.search(text[max(0, m.start() - 8) : m.end()])
    ]


def _shipped_yaml() -> list[Path]:
    return sorted([*(_REPO / "reference").rglob("*.yaml"), *(_REPO / "examples").rglob("*.yaml")])


@pytest.mark.parametrize("path", _shipped_yaml(), ids=lambda p: str(p.relative_to(_REPO)))
def test_every_numbered_list_is_delivered_one_item_per_line(path: Path) -> None:
    try:
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except yaml.YAMLError:
        pytest.skip("not plain YAML (a template or fixture)")
    problems = [
        f"{where}: {snippet!r}"
        for document in documents
        for where, text in _strings(document)
        for snippet in _welded(text)
    ]
    assert not problems, (
        "A numbered list is joined onto one line in the parsed text, so the model receives a "
        "different prompt than the file shows. Use a literal `|` block, or keep every line of a "
        "folded `>` block at its base indentation with a blank line between items:\n  "
        + "\n  ".join(problems)
    )


def test_the_rule_catches_both_shapes_of_the_defect() -> None:
    folded_flat = yaml.safe_load("p: >\n  Check for:\n  1. Naming\n  2. Formatting\n")["p"]
    folded_indented = yaml.safe_load(
        "p: >\n  Do this:\n  1. First step that\n     wraps.\n  2. Second.\n  3. Third.\n"
    )["p"]
    literal = yaml.safe_load("p: |\n  Check for:\n  1. Naming\n  2. Formatting\n")["p"]
    assert _welded(folded_flat)
    assert _welded(folded_indented)
    assert not _welded(literal)
    # "step 3." is prose, not a welded item
    assert not _welded("Steps:\n1. Read the log\n2. Take the line number from\nstep 3. This works.")


def test_the_test_writer_prompt_arrives_as_four_steps() -> None:
    system = yaml.safe_load((_REPO / "reference/archetypes/test-writer.yaml").read_text())[
        "defaults"
    ]["prompt"]["system"]
    steps = [line for line in system.splitlines() if re.match(r"\d\. ", line)]
    assert [s[:2] for s in steps] == ["1.", "2.", "3.", "4."]
    assert steps[2] == "3. Run the test using run-tests."
