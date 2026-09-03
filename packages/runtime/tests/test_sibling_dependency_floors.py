"""Every workspace sibling a package depends on carries a version floor.

An unfloored sibling is not a style problem, it is a shipping bug. ``pip install -U
swarmkit-runtime`` upgrades dependencies *only if needed*, and with a bare ``swarmkit-schema`` it is
never needed — so a runtime that has started reading a new schema field installs cleanly beside a
schema that rejects it. That is what happened at 1.205.0: the runtime supported
``provenance.requires_runtime`` while the published schema 1.33.0 refused a skill declaring it, with
"additional properties are not allowed" naming a field the runtime's own docs describe.

The same argument already floors the ``[ui]`` extra (see the comment on it) — a portal built
against a removed API must fail at install time rather than in a browser. This test is that
reasoning applied to every sibling, so the next one is caught by CI instead of by a user.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PACKAGES = ("runtime", "schema/python", "webui", "control-plane")


def _sibling_requirements(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text())
    project = data.get("project", {})
    reqs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        reqs.extend(extra)
    return [r for r in reqs if re.match(r"^swarmkit[-_]", r.strip(), re.IGNORECASE)]


@pytest.mark.parametrize("package", PACKAGES)
def test_sibling_dependencies_declare_a_floor(package: str) -> None:
    pyproject = REPO / "packages" / package / "pyproject.toml"
    for requirement in _sibling_requirements(pyproject):
        assert ">=" in requirement, (
            f"{package} depends on {requirement!r} with no version floor. "
            "Siblings version independently, so an unfloored one lets a package that needs a new "
            "field resolve against a release that does not have it — at install time, silently."
        )
