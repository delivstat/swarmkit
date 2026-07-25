"""``llms-full.txt`` must be freshly generated and its published copy in sync.

``llms-full.txt`` (repo root) is the expanded single-file LLM corpus, produced by
``scripts/build_llms_full.py`` by concatenating ``llms.txt`` + the playbook + the
artifact references + the core design notes. ``docs/site/llms-full.txt`` is the copy
MkDocs publishes at ``https://delivstat.github.io/swarmkit/llms-full.txt``.

Both are generated, not hand-edited. These tests are the guard, same discipline as the
schema codegen drift check: the committed file must equal a fresh generation, and the
two copies must be byte-identical. On failure: ``python scripts/build_llms_full.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GENERATOR = _REPO_ROOT / "scripts" / "build_llms_full.py"
_ROOT_FULL = _REPO_ROOT / "llms-full.txt"
_PUBLISHED_FULL = _REPO_ROOT / "docs" / "site" / "llms-full.txt"


def _load_build() -> object:
    spec = importlib.util.spec_from_file_location("build_llms_full", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_llms_full_is_freshly_generated() -> None:
    module = _load_build()
    expected = module.build()  # type: ignore[attr-defined]
    assert _ROOT_FULL.is_file(), "llms-full.txt is missing — run scripts/build_llms_full.py"
    assert _ROOT_FULL.read_text(encoding="utf-8") == expected, (
        "llms-full.txt has drifted from its source docs. "
        "Regenerate: python scripts/build_llms_full.py"
    )


def test_published_llms_full_matches_root() -> None:
    assert _PUBLISHED_FULL.is_file(), (
        "docs/site/llms-full.txt is missing — MkDocs won't publish llms-full.txt. "
        "Regenerate: python scripts/build_llms_full.py"
    )
    assert _PUBLISHED_FULL.read_bytes() == _ROOT_FULL.read_bytes(), (
        "docs/site/llms-full.txt has drifted from the root llms-full.txt. "
        "Regenerate: python scripts/build_llms_full.py"
    )
