"""Component versions are read from metadata, never hardcoded.

Three literals had drifted before this: ``swarmkit_webui.__version__`` still said 0.1.0 at release
0.6.0 (so ``swarmkit serve`` reported the wrong portal version for five releases), the runtime's own
version was never printed at all, and the portal footer showed ``v1.2.58`` — a version of neither
package, left behind when the runtime was at 1.2.x.

``swarmkit serve`` is the runtime hosting a SEPARATELY versioned portal, so one number cannot answer
"what am I running", and an old portal paired with a new runtime is a real, silent failure mode.
"""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

from swarmkit_runtime.server._versions import component_versions, runtime_version, webui_version

REPO = Path(__file__).resolve().parents[3]


def test_runtime_version_matches_installed_metadata() -> None:
    assert runtime_version() == version("swarmkit-runtime")
    assert re.match(r"^\d+\.\d+\.\d+", runtime_version())


def test_webui_version_matches_installed_metadata() -> None:
    assert webui_version() == version("swarmkit-webui")


def test_webui_dunder_version_is_not_a_literal() -> None:
    """The exact defect: a constant in source drifts the moment pyproject is bumped."""
    import swarmkit_webui  # noqa: PLC0415

    assert swarmkit_webui.__version__ == version("swarmkit-webui")
    src = (REPO / "packages/webui/src/swarmkit_webui/__init__.py").read_text()
    # A real version literal, e.g. `__version__ = "0.6.0"`. The `0.0.0+unknown` fallback for an
    # uninstalled source tree is fine — it is a sentinel, not a version anyone reads as current.
    literal = re.search(r'__version__\s*=\s*"(?!0\.0\.0\+unknown)\d+\.\d+', src)
    assert literal is None, f"read it from package metadata, do not hardcode: {literal}"


def test_health_reports_both_components() -> None:
    both = component_versions()
    assert set(both) == {"runtime_version", "webui_version"}
    assert both["runtime_version"] == version("swarmkit-runtime")


def test_the_portal_footer_is_not_hardcoded() -> None:
    """The footer showed a literal for five releases. It must read the server, not a constant."""
    sidebar = (REPO / "packages/ui/components/layout/sidebar.tsx").read_text()
    # A rendered literal, e.g. `<span>v1.2.58</span>`. The comment explaining the old bug names it
    # deliberately, so match what would reach the screen rather than the whole file.
    rendered = re.search(r">\s*v\d+\.\d+\.\d+\s*<", sidebar)
    assert rendered is None, f"the footer must read the server, not a literal: {rendered}"
    assert (
        "runtime_version" in sidebar or "api\n\t\t\t.health()" in sidebar or ".health()" in sidebar
    )
