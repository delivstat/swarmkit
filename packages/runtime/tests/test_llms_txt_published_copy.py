"""The docs-site copy of ``llms.txt`` must stay byte-identical to the root.

``llms.txt`` at the repo root is the authoritative, tool-discoverable index.
``docs/site/llms.txt`` is the copy MkDocs publishes so the same file is reachable
at ``https://delivstat.github.io/swarmkit/llms.txt``. The docs CI only rebuilds on
``docs/**`` changes, so the two must be kept in sync by hand — this test is the guard.

Same discipline as the byte-identical bundled schema copies: one source of truth,
a mechanical copy, a drift test. If this fails, re-copy:

    cp llms.txt docs/site/llms.txt
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROOT_LLMS = _REPO_ROOT / "llms.txt"
_PUBLISHED_LLMS = _REPO_ROOT / "docs" / "site" / "llms.txt"


def test_published_llms_txt_matches_root() -> None:
    assert _ROOT_LLMS.is_file(), "root llms.txt is missing"
    assert _PUBLISHED_LLMS.is_file(), (
        "docs/site/llms.txt is missing — MkDocs won't publish llms.txt. "
        "Re-create it: cp llms.txt docs/site/llms.txt"
    )
    assert _PUBLISHED_LLMS.read_bytes() == _ROOT_LLMS.read_bytes(), (
        "docs/site/llms.txt has drifted from the root llms.txt. "
        "Re-sync: cp llms.txt docs/site/llms.txt"
    )
