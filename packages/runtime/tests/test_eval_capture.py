"""Eval capture: recorded runs become eval cases (design/details/eval-capture.md, #1105).

Built on a real store (SQLite always, Postgres with SWARMKIT_TEST_POSTGRES_URL) and a real audit
provider, because what capture claims is about what those hold: the job row gives the input and
output, the audit log's allowed `skill.executed` events give the skills used, and a
`run.attachments` event means the case input can't carry what the model saw.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime.audit import SqlAuditProvider
from swarmkit_runtime.eval import CaptureError, CaptureService, load_eval_set
from swarmkit_runtime.eval._capture import REDACTED, case_id_for, parse_since
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.persistence import SqliteStore, Store
from swarmkit_runtime.persistence._store import make_engine
from swarmkit_runtime.server._services import ArtifactService, InvalidArtifactError

TOPOLOGIES = {"triage": object(), "other": object()}


@pytest.fixture(params=["sqlite", "postgres"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Store]:
    if request.param == "sqlite":
        yield SqliteStore(tmp_path)
        return
    url = os.environ.get("SWARMKIT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set SWARMKIT_TEST_POSTGRES_URL to run the Postgres capture tests")
    yield Store(make_engine(url))


@pytest.fixture
def audit(tmp_path: Path) -> SqlAuditProvider:
    return SqlAuditProvider(make_engine(f"sqlite:///{tmp_path / 'audit.sqlite'}"))


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "evals").mkdir(parents=True)
    return root


def _job(
    store: Store,
    user_input: str,
    output: str | None = "Routed to billing.",
    *,
    topology: str = "triage",
    status: str = "completed",
    labels: dict[str, str] | None = None,
    source: str = "serve",
    version: str | None = None,
) -> str:
    job_id = str(uuid.uuid4())
    store.create_job(job_id, topology, user_input, None, source, labels=labels, version=version)
    store.update_job(job_id, status=status, output=output)
    return job_id


async def _skill(
    audit: SqlAuditProvider, run_id: str, skill_id: str, decision: str = "allow"
) -> None:
    await audit.record(
        AuditEvent(
            event_type="skill.executed",
            agent_id="root",
            timestamp=datetime.now(UTC),
            skill_id=skill_id,
            run_id=run_id,
            policy_decision=decision,  # type: ignore[arg-type]
        )
    )


async def _attachments(audit: SqlAuditProvider, run_id: str) -> None:
    await audit.record(
        AuditEvent(
            event_type="run.attachments",
            agent_id="runtime",
            timestamp=datetime.now(UTC),
            run_id=run_id,
            payload={"count": 1},
        )
    )


def _service(store: Store, audit: SqlAuditProvider, ws: Path) -> CaptureService:
    return CaptureService(store, audit, ws, TOPOLOGIES)


async def _capture(svc: CaptureService, into: str = "captured", **select: Any) -> Any:
    kw = {k: select.pop(k) for k in ("redact", "used_skills") if k in select}
    jobs, missing = svc.select(**select)
    return await svc.propose(
        jobs, into=into, explicit=bool(select.get("run_ids")), missing=missing, **kw
    )


def _cases(proposal: Any) -> list[dict[str, Any]]:
    return list(yaml.safe_load(proposal.yaml)["cases"])


# ---- the case shape ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_case_carries_the_input_the_skills_used_and_the_output_as_reference(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    rid = _job(
        store, "I was charged twice.", "Routed to billing.\nRefund in 5 days.", version="1.2.0"
    )
    await _skill(audit, rid, "classify-ticket")
    await _skill(audit, rid, "lookup-order")
    await _skill(audit, rid, "classify-ticket")  # a repeat call is one skill
    await _skill(audit, rid, "issue-refund", decision="deny")  # refused: not "used"

    proposal = await _capture(_service(store, audit, ws), run_ids=[rid])

    (case,) = _cases(proposal)
    assert case["id"] == case_id_for(rid)
    assert case["input"] == "I was charged twice."
    assert case["expect"] == {
        "not_empty": True,
        "used_skills": ["classify-ticket", "lookup-order"],
        "reference": "Routed to billing.\nRefund in 5 days.",
        "min_confidence": 0.7,
    }
    assert case["source"]["run_id"] == rid
    assert case["source"]["topology_version"] == "1.2.0"
    assert proposal.created and proposal.target == "triage"
    # the proposed file is a valid eval set, with the multi-line reference as a literal block
    assert "reference: |" in proposal.yaml
    (ws / "evals" / "check.yaml").write_text(proposal.yaml.replace("id: captured", "id: check"))
    assert load_eval_set(ws, "check").cases[0].expect.reference


@pytest.mark.asyncio
async def test_no_used_skills_drops_the_requirement(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    rid = _job(store, "hello")
    await _skill(audit, rid, "classify-ticket")
    proposal = await _capture(_service(store, audit, ws), run_ids=[rid], used_skills=False)
    assert "used_skills" not in _cases(proposal)[0]["expect"]


# ---- what is skipped, and why --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_skip_reason(store: Store, audit: SqlAuditProvider, ws: Path) -> None:
    ok = _job(store, "a fine run")
    attached = _job(store, "look at this image")
    await _attachments(audit, attached)
    empty = _job(store, "silent run", "   ")
    long = _job(store, "verbose run", "x" * 20_001)
    proposal = await _capture(
        _service(store, audit, ws), run_ids=[ok, attached, empty, long, "no-such-run"]
    )
    reasons = {n.run_id: n.reason for n in proposal.skipped}
    assert reasons == {
        attached: "attachments",
        empty: "empty-output",
        long: "output-too-long",  # skipped, never truncated
        "no-such-run": "not-found",
    }
    assert proposal.added == [case_id_for(ok)]


@pytest.mark.asyncio
async def test_runs_of_two_topologies_cannot_start_one_new_set(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    a, b = _job(store, "one"), _job(store, "two", topology="other")
    with pytest.raises(CaptureError) as exc:
        await _capture(_service(store, audit, ws), run_ids=[a, b])
    assert exc.value.code == "selection.mixed"
    assert "other" in str(exc.value) and "triage" in str(exc.value)


@pytest.mark.asyncio
async def test_a_run_of_another_topology_is_wrong_target_for_an_existing_set(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    (ws / "evals" / "existing.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: EvalSet\nmetadata: {id: existing}\ntarget: triage\n"
        "cases:\n  - {id: hand-written, input: kept as it is}\n"
    )
    rid = _job(store, "from elsewhere", topology="other")
    proposal = await _capture(_service(store, audit, ws), run_ids=[rid], into="existing")
    assert [(n.run_id, n.reason) for n in proposal.skipped] == [(rid, "wrong-target")]
    assert not proposal.created


@pytest.mark.asyncio
async def test_a_failed_run_is_skipped_under_filters_and_a_smoke_case_when_named(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    failed = _job(store, "this one broke", None, status="failed")
    svc = _service(store, audit, ws)

    filtered = await _capture(svc, topology="triage", status=None)
    assert [(n.run_id, n.reason) for n in filtered.skipped] == [(failed, "not-completed")]

    named = await _capture(svc, run_ids=[failed])
    (case,) = _cases(named)
    assert "expect" not in case  # input only: it re-runs, it is not a baseline
    assert [(n.run_id, n.reason) for n in named.flagged] == [(failed, "smoke-only")]


# ---- duplicates and idempotence ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_runs_with_the_same_input_become_one_case_from_the_newer(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    older = _job(store, "Same  question?", "old answer")
    newer = _job(store, "same question?".replace("s", "S", 1), "new answer")
    proposal = await _capture(_service(store, audit, ws), topology="triage")
    assert proposal.added == [case_id_for(newer)]
    assert _cases(proposal)[0]["expect"]["reference"] == "new answer"
    assert [(n.run_id, n.reason) for n in proposal.skipped] == [(older, "duplicate")]


@pytest.mark.asyncio
async def test_capturing_the_same_runs_twice_writes_nothing_the_second_time(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    rids = [_job(store, f"question {i}") for i in range(3)]
    svc = _service(store, audit, ws)
    first = await _capture(svc, run_ids=rids)
    assert svc.write(first, ArtifactService(ws))["written"] is True
    written = (ws / "evals" / "captured.yaml").read_text()

    second = await _capture(svc, run_ids=rids)
    assert second.added == []
    assert {n.reason for n in second.skipped} == {"duplicate"}
    assert svc.write(second, ArtifactService(ws))["written"] is False
    assert (ws / "evals" / "captured.yaml").read_text() == written


@pytest.mark.asyncio
async def test_appending_keeps_the_existing_cases(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    (ws / "evals" / "existing.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: EvalSet\nmetadata: {id: existing}\ntarget: triage\n"
        "cases:\n  - {id: hand-written, input: kept as it is, expect: {contains: [billing]}}\n"
    )
    rid = _job(store, "a new question")
    svc = _service(store, audit, ws)
    proposal = await _capture(svc, run_ids=[rid], into="existing")
    svc.write(proposal, ArtifactService(ws))
    es = load_eval_set(ws, "existing")
    assert [c.id for c in es.cases] == ["hand-written", case_id_for(rid)]
    assert es.cases[0].expect.contains == ["billing"]


# ---- redaction -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_redact_pattern_never_reaches_the_file(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    rid = _job(store, "Open returns for DC-14 please", "Three returns open at DC-14.")
    gone = _job(store, "DC-99")
    svc = _service(store, audit, ws)
    proposal = await _capture(svc, run_ids=[rid, gone], redact=[r"DC-\d+"])
    assert "DC-14" not in proposal.yaml and REDACTED in proposal.yaml
    assert [(n.run_id, n.reason) for n in proposal.skipped] == [(gone, "redacted-to-empty")]
    svc.write(proposal, ArtifactService(ws))
    assert "DC-14" not in (ws / "evals" / "captured.yaml").read_text()


@pytest.mark.asyncio
async def test_a_bad_redact_pattern_is_an_error(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    rid = _job(store, "x")
    with pytest.raises(CaptureError) as exc:
        await _capture(_service(store, audit, ws), run_ids=[rid], redact=["("])
    assert exc.value.code == "redact.invalid"


# ---- selection -----------------------------------------------------------------------------------


def test_filters_select_by_topology_status_label_source_and_time(store: Store) -> None:
    keep = _job(store, "a", labels={"team": "billing"}, source="cli")
    _job(store, "b", labels={"team": "support"}, source="cli")
    _job(store, "c", labels={"team": "billing"}, source="serve")
    _job(store, "d", topology="other", labels={"team": "billing"}, source="cli")
    _job(store, "e", status="failed", labels={"team": "billing"}, source="cli")
    rows = store.list_jobs_filtered(
        topology="triage", status="completed", labels={"team": "billing"}, source="cli"
    )
    assert [r.id for r in rows] == [keep]
    assert store.list_jobs_filtered(topology="triage", since="2999-01-01T00:00:00+00:00") == []


def test_the_newest_come_first_and_the_limit_is_capped(store: Store) -> None:
    ids = [_job(store, f"q{i}") for i in range(4)]
    rows = store.list_jobs_filtered(topology="triage", limit=2)
    assert [r.id for r in rows] == [ids[3], ids[2]]
    svc = CaptureService(store, None, Path("/nonexistent"), TOPOLOGIES)
    jobs, _ = svc.select(topology="triage", limit=10_000)  # capped at 200, not an error
    assert len(jobs) == 4


def test_a_selection_needs_runs_or_a_topology(store: Store) -> None:
    with pytest.raises(CaptureError) as exc:
        CaptureService(store, None, Path("."), TOPOLOGIES).select()
    assert exc.value.code == "selection.empty"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-01", "2026-11-01T00:00:00+00:00"),
        ("2026-11-01T10:00:00Z", "2026-11-01T10:00:00+00:00"),
        (None, None),
    ],
)
def test_since_accepts_dates(value: str | None, expected: str | None) -> None:
    assert parse_since(value) == expected


def test_since_accepts_durations() -> None:
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    assert parse_since("7d", now=now) == "2026-11-16T12:00:00+00:00"
    assert parse_since("24h", now=now) == "2026-11-22T12:00:00+00:00"
    with pytest.raises(CaptureError):
        parse_since("last week", now=now)


# ---- writing -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_write_is_the_validated_eval_set_write(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    """Capture adds no second writer: a target the workspace lacks is refused by the same check a
    portal edit hits, and nothing is left behind."""
    rid = _job(store, "q")
    svc = CaptureService(store, audit, ws, {"triage": object()})
    proposal = await _capture(svc, run_ids=[rid])
    narrowed = CaptureService(store, audit, ws, {"something-else": object()})
    with pytest.raises(InvalidArtifactError):
        narrowed.write(proposal, ArtifactService(ws))
    assert not (ws / "evals" / "captured.yaml").exists()


@pytest.mark.asyncio
async def test_a_bad_set_id_is_refused(store: Store, audit: SqlAuditProvider, ws: Path) -> None:
    rid = _job(store, "q")
    with pytest.raises(CaptureError) as exc:
        await _capture(_service(store, audit, ws), run_ids=[rid], into="Not_Kebab")
    assert exc.value.code == "set.id"


# ---- round trip: captured cases catch a regression -----------------------------------------------


class _Topology:
    """Answers each input from a table, and judges a reference as matching when it is equal."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers

    async def run(self, topology_name: str, user_input: str) -> object:
        from types import SimpleNamespace  # noqa: PLC0415

        return SimpleNamespace(output=self.answers[user_input], events=[])

    async def judge(self, **_: Any) -> Any:
        raise AssertionError("captured cases use reference, not a judge skill")

    async def judge_rubric(self, **_: Any) -> Any:
        raise AssertionError("captured cases use reference, not a rubric")

    async def judge_reference(
        self, *, reference: str, content: str, case_input: str = "", trigger: str = "eval"
    ) -> Any:
        from swarmkit_runtime.governance import DecisionSkillResult  # noqa: PLC0415

        same = reference.strip() == content.strip()
        return DecisionSkillResult(
            skill_id="reference",
            verdict="pass" if same else "fail",
            confidence=0.95,
            reasoning="same answer" if same else "different answer",
        )


@pytest.mark.asyncio
async def test_captured_cases_pass_unchanged_and_catch_a_changed_answer(
    store: Store, audit: SqlAuditProvider, ws: Path
) -> None:
    from swarmkit_runtime.eval import run_eval_set  # noqa: PLC0415

    answers = {
        "charged twice": "Billing refunds it.",
        "nothing loads": "Escalated as P1.",
        "reset password": "Sent a reset link.",
    }
    ids = [_job(store, q, a) for q, a in answers.items()]
    svc = _service(store, audit, ws)
    svc.write(await _capture(svc, run_ids=ids), ArtifactService(ws))
    captured = load_eval_set(ws, "captured")

    unchanged = await run_eval_set(_Topology(answers), captured)
    assert (unchanged.passed, unchanged.total) == (3, 3)

    changed = await run_eval_set(
        _Topology({**answers, "nothing loads": "Closed as a duplicate."}), captured
    )
    assert (changed.passed, changed.total) == (2, 3)
    (failed,) = [c for c in changed.cases if not c.passed]
    assert failed.case_id == case_id_for(ids[1])
