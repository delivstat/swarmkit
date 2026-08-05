"""A gate-driven rework hands a stage its own draft, marked as its own.

The other half of bug 13. `_reinvoke` (1.146.0) fixed the retry a *decision skill* drives, but a
rework driven from the **gate** goes through a different path: the stage is re-run, and its input is
rebuilt by `_prior_input`, which concatenated every `*/output` artifact for the correlation —
including the stage's own previous attempt.

So the agent received its own earlier draft as an unmarked block, indistinguishable from upstream
context:

    '[harness:claude-code] # design spec ...\\n\\n[harness:claude-code] triage says X'

A harness re-run is a fresh process with no memory of writing any of it. That is the same shape that
got a revision refused on safety grounds — and rework is the path a human gate uses, so it is the
more likely one to hit in practice.

The draft should be *present* (the agent has to see what it is revising) and *attributed* (it should
not have to take an authorship claim on faith).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.artifacts import build_artifact_store
from swarmkit_runtime.orchestration._saga import SagaState
from swarmkit_runtime.server._pipeline_stage import _prior_input, _stage_input


@pytest.fixture
def store(tmp_path: Path) -> Any:
    (tmp_path / ".swarmkit").mkdir(parents=True, exist_ok=True)
    return build_artifact_store(
        None,
        workspace_root=tmp_path,
        database_url=f"sqlite:///{tmp_path / '.swarmkit' / 'a.sqlite'}",
    )


TRIAGE = "triage says: the hold flag is set on the RF panel"
DRAFT = "# WMS Design\n\n## PGM hold / RF screens\n\nThe PGM screen confirms a pick."


# ---- the bug -------------------------------------------------------------------------------------


def test_a_stages_own_draft_is_attributed_not_concatenated(store: Any) -> None:
    """The bug: on a re-run the stage's own previous output was mixed into the upstream context
    with nothing to say whose it was."""
    store.put("WMS-5", "triage", TRIAGE)
    store.put("WMS-5", "design", DRAFT)

    text = _prior_input(store, "WMS-5", "design", 1)

    assert "<prior-output" in text, "the stage's own draft must be marked as its own"
    assert 'agent="design"' in text
    assert "PGM hold" in text, "and must still be present — it is what the agent is revising"


def test_upstream_context_stays_outside_the_block(store: Any) -> None:
    """Upstream artifacts are context, not the agent's own work; labelling them as its draft would
    be a different false claim."""
    store.put("WMS-5", "triage", TRIAGE)
    store.put("WMS-5", "design", DRAFT)

    text = _prior_input(store, "WMS-5", "design", 1)
    before_block = text.split("<prior-output")[0]

    assert TRIAGE in before_block
    assert "PGM hold" not in before_block


def test_the_draft_is_not_duplicated(store: Any) -> None:
    """It used to appear once, unmarked. It must now appear once, marked — not twice."""
    store.put("WMS-5", "design", DRAFT)

    text = _prior_input(store, "WMS-5", "design", 1)

    assert text.count("PGM hold") == 1


def test_the_artifact_ref_is_carried(store: Any) -> None:
    """So a correction can be tied to the revision it was written about."""
    store.put("WMS-5", "design", DRAFT)

    assert 'artifact="WMS-5/design/output"' in _prior_input(store, "WMS-5", "design", 1)


# ---- the first run is unchanged ------------------------------------------------------------


def test_a_first_run_has_no_own_draft(store: Any) -> None:
    """A stage that has not produced anything yet must get plain upstream context — an empty
    `prior-output` block would assert a draft that does not exist."""
    store.put("WMS-5", "triage", TRIAGE)

    text = _prior_input(store, "WMS-5", "design", 0)

    assert text == TRIAGE
    assert "<prior-output" not in text


def test_a_stage_with_no_id_behaves_as_before(store: Any) -> None:
    """Callers that do not name the stage keep the old concatenation — the change must not depend
    on every call site being updated at once."""
    store.put("WMS-5", "triage", TRIAGE)
    store.put("WMS-5", "design", DRAFT)

    text = _prior_input(store, "WMS-5")

    assert TRIAGE in text
    assert "PGM hold" in text
    assert "<prior-output" not in text


# ---- through the real seam -----------------------------------------------------------------


def test_stage_input_marks_the_draft_on_a_rework(store: Any) -> None:
    """End to end at the seam the orchestrator calls, since that is what actually runs."""
    saga = SagaState(correlation_id="WMS-5", graph_id="g", input="the ticket")
    saga.passed_stages = ["triage"]
    saga.attempts = {"design": 2}

    class _SagaStore:
        def get(self, _cid: str) -> SagaState:
            return saga

    store.put("WMS-5", "triage", TRIAGE)
    store.put("WMS-5", "design", DRAFT)

    text = _stage_input(_SagaStore(), store, "WMS-5", {"id": "design", "topology": "t"})  # type: ignore[arg-type]

    assert "<prior-output" in text
    assert 'agent="design"' in text
    assert 'round="2"' in text, "the round comes from the saga's attempt count"


def test_the_first_stage_still_gets_the_pipeline_payload(store: Any) -> None:
    """Guard: precedence is unchanged for a run that has passed no stages."""
    saga = SagaState(correlation_id="WMS-5", graph_id="g", input="the ticket payload")

    class _SagaStore:
        def get(self, _cid: str) -> SagaState:
            return saga

    text = _stage_input(_SagaStore(), store, "WMS-5", {"id": "triage", "topology": "t"})  # type: ignore[arg-type]

    assert text == "the ticket payload"
