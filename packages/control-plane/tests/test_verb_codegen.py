"""The UI's generated verb contract must match the panel's canonical VERB_TIERS.

`packages/control-plane-ui/lib/generated/verbs.ts` is generated from `_verbs.VERB_TIERS` by
`scripts/codegen_verbs.py` (run via `just codegen-verbs`; CI fails on drift). This is the in-suite
guard so `pytest` also catches a stale committed file — it checks the *content* (every verb+tier
present, no extras) rather than an exact byte match, so it's robust to biome reformatting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from swarmkit_control_plane._verbs import VERB_TIERS

_VERBS_TS = Path(__file__).resolve().parents[3] / "packages/control-plane-ui/lib/generated/verbs.ts"


@pytest.mark.skipif(not _VERBS_TS.exists(), reason="control-plane-ui not in this checkout")
def test_generated_verbs_match_panel_table() -> None:
    text = _VERBS_TS.read_text(encoding="utf-8")

    # Every canonical verb→tier pair appears as an entry.
    for verb, tier in VERB_TIERS.items():
        entry = f'{{ verb: "{verb}", tier: "{tier}" }}'
        assert entry in text, (
            f"generated verbs.ts is stale — missing {entry}. Run `just codegen-verbs`."
        )

    # No extra entries beyond the canonical table (count the entry literals — the `verb: "…"`
    # form, which excludes the `verb: string` field in the KnownVerb interface).
    n_entries = len(re.findall(r'verb:\s*"', text))
    assert n_entries == len(VERB_TIERS), (
        f"generated verbs.ts has {n_entries} entries, expected {len(VERB_TIERS)} "
        "— run `just codegen-verbs` and commit."
    )
