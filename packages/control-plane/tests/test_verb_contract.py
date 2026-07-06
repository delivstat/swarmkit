"""Cross-package contract: the panel's verb→tier map must match the runtime connector's.

The panel (``_verbs.VERB_TIERS``) and the runtime Mode B connector
(``swarmkit_runtime.connect.VERB_ROUTES``) are deliberately separate — the control plane is
standalone and doesn't import the runtime. This test is the seam that keeps them from drifting:
it runs in the workspace (where both packages are installed) and asserts the panel mirrors the
runtime's canonical table exactly. Skipped if the runtime isn't importable (panel-only checkout).
"""

from __future__ import annotations

import pytest

connect = pytest.importorskip("swarmkit_runtime.connect")

from swarmkit_control_plane._deploy import DEPLOYABLE  # noqa: E402
from swarmkit_control_plane._verbs import VERB_TIERS  # noqa: E402


def test_panel_verb_tiers_match_runtime() -> None:
    """Every verb the panel enqueues carries the exact tier the runtime connector enforces."""
    assert connect.verb_tiers() == VERB_TIERS


def test_deploy_plural_matches() -> None:
    """The deployable kind→collection map agrees on both sides (drives the deploy verb's route)."""
    assert DEPLOYABLE == connect.DEPLOY_PLURAL


def test_tier_ranks_agree() -> None:
    """read < run < admin on both sides, so within-tier checks are identical."""
    from swarmkit_control_plane._verbs import tier_rank as panel_rank  # noqa: PLC0415

    for tier in ("read", "run", "admin"):
        assert panel_rank(tier) == connect._tier_rank(tier)
    assert panel_rank("nonsense") == connect._tier_rank("nonsense") == -1
