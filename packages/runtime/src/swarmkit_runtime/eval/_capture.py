"""Capture recorded runs as eval cases — design/details/eval-capture.md (#1105).

A finished run already holds what a regression case needs: its input and output (the job row) and
the skills it called (the audit log's allowed ``skill.executed`` events). :class:`CaptureService`
reads both from the store, so capture behaves the same on one host and on a fleet, and proposes an
eval set without writing anything:

- one case per run: the input, ``not_empty``, the skills the run used, and the recorded output as
  ``reference`` — a known-good answer the judge compares new output against, as data;
- ``source`` on each case, naming the run it came from;
- a ``skipped`` list saying why any run was left out, and a ``flagged`` list for runs taken as
  less than a full case.

Writing is a separate, explicit step through the front door's writer (``ArtifactService``), the
same validated write a portal edit takes. Capturing the same runs twice writes nothing the second
time. Every front door — ``swarmkit eval-capture``, ``POST /evals/capture``, the portal — calls
this one service.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from swarmkit_runtime.eval._errors import EvalSetInvalidError
from swarmkit_runtime.eval._loader import find_eval_set_file, load_eval_set

#: The most runs one capture takes. A capture becomes a reviewed diff of a file in git; a few
#: hundred cases at once is not a diff anyone reviews.
CAPTURE_LIMIT_MAX = 200
#: The schema's cap on `expect.reference`. A longer output is skipped, never truncated: judging
#: against half an answer is worse than no case.
REFERENCE_MAX = 20_000
#: The confidence a captured `reference` must be judged at.
DEFAULT_MIN_CONFIDENCE = 0.7
REDACTED = "[REDACTED]"

_SET_ID = re.compile(r"^[a-z][a-z0-9-]*$")


class CaptureError(ValueError):
    """A capture that cannot be proposed at all, as opposed to one that skips some runs."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CaptureNote:
    """A run left out (``skipped``) or taken as less than a full case (``flagged``)."""

    run_id: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        out = {"run_id": self.run_id, "reason": self.reason}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class CaptureProposal:
    """What a capture would write. Nothing is written until :meth:`CaptureService.write`."""

    set_id: str
    target: str
    #: True when the set does not exist yet and writing creates it
    created: bool
    added: list[str] = field(default_factory=list)
    skipped: list[CaptureNote] = field(default_factory=list)
    flagged: list[CaptureNote] = field(default_factory=list)
    #: the whole file as it would be written
    yaml: str = ""
    written: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "target": self.target,
            "created": self.created,
            "added": list(self.added),
            "skipped": [n.to_dict() for n in self.skipped],
            "flagged": [n.to_dict() for n in self.flagged],
            "yaml": self.yaml,
            "written": self.written,
        }


class _Writer(Protocol):
    """The front door's eval-set writer — ``ArtifactService`` on the CLI and serve alike."""

    def create_eval_set(self, yaml_content: str, topologies: Any) -> dict[str, Any]: ...

    def put_eval_set(self, set_id: str, yaml_content: str, topologies: Any) -> dict[str, Any]: ...


def case_id_for(run_id: str) -> str:
    """``run-`` and the first 8 hex digits of the job id: stable, kebab, unique enough in a set."""
    hexes = re.sub(r"[^0-9a-f]", "", run_id.lower())
    return f"run-{(hexes or 'x')[:8]}"


_AGO = re.compile(r"^(\d+)\s*([mhdw])$")


def parse_since(value: str | None, *, now: datetime.datetime | None = None) -> str | None:
    """``7d`` / ``24h`` / ``30m`` / ``2w`` before now, or an ISO date or date-time, as the ISO
    string job rows are compared against. ``None`` passes through."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    m = _AGO.match(text)
    if m:
        unit = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[m.group(2)]
        base = now or datetime.datetime.now(datetime.UTC)
        return (base - datetime.timedelta(**{unit: int(m.group(1))})).isoformat()
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureError(
            "since.invalid", f"{text!r} is not a duration (7d, 24h) or an ISO date"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.isoformat()


def _norm(text: str) -> str:
    return " ".join(text.split())


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _LiteralDumper(yaml.SafeDumper):
    """Multi-line strings as `|` blocks, so a captured reference reads in a diff as it was said."""


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_LiteralDumper.add_representer(str, _str)


def _dump(raw: Mapping[str, Any]) -> str:
    text: str = yaml.dump(
        dict(raw), Dumper=_LiteralDumper, sort_keys=False, allow_unicode=True, width=100
    )
    return text


def _compile(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p))
        except re.error as exc:
            raise CaptureError(
                "redact.invalid", f"redact pattern {p!r} is not valid: {exc}"
            ) from exc
    return out


def _redact(text: str, patterns: Sequence[re.Pattern[str]]) -> str:
    for p in patterns:
        text = p.sub(REDACTED, text)
    return text


class CaptureService:
    """Select finished runs and propose them as cases of an eval set; write on request."""

    def __init__(
        self,
        store: Any,  # the runtime store: get_job + list_jobs_filtered
        audit: Any,
        workspace_root: Path,
        topologies: Mapping[str, Any],
        *,
        default_redact: Sequence[str] = (),
    ) -> None:
        self._store = store
        self._audit = audit
        self._ws = workspace_root
        self._topologies = topologies
        #: the workspace's `evals.capture.redact`: applied on every capture, before a request's own
        self._default_redact = list(default_redact)

    # ---- selection -----------------------------------------------------------------------------

    def select(
        self,
        *,
        run_ids: Sequence[str] = (),
        topology: str | None = None,
        since: str | None = None,
        until: str | None = None,
        status: str | None = "completed",
        labels: Mapping[str, str] | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Any], list[CaptureNote]]:
        """The runs to capture, newest first, and any named run that does not exist.

        Explicit ``run_ids`` are taken as given — a failed one too, which :meth:`propose` then takes
        as a smoke case. Otherwise ``topology`` plus filters, defaulting to completed runs and
        capped at :data:`CAPTURE_LIMIT_MAX`.
        """
        if run_ids:
            jobs, missing = [], []
            for rid in dict.fromkeys(run_ids):
                job = self._store.get_job(rid)
                if job is None:
                    missing.append(CaptureNote(rid, "not-found"))
                else:
                    jobs.append(job)
            jobs.sort(key=lambda j: j.created_at or "", reverse=True)
            return jobs, missing
        if not topology:
            raise CaptureError(
                "selection.empty", "name runs (run_ids) or a topology to select from"
            )
        rows = self._store.list_jobs_filtered(
            topology=topology,
            status=status,
            since=since,
            until=until,
            labels=dict(labels or {}) or None,
            source=source,
            limit=max(1, min(int(limit), CAPTURE_LIMIT_MAX)),
        )
        return list(rows), []

    # ---- the proposal ----------------------------------------------------------------------------

    async def propose(  # noqa: PLR0912, PLR0915 — one branch per skip reason, in the design's order
        self,
        jobs: Sequence[Any],
        *,
        into: str,
        explicit: bool = False,
        redact: Sequence[str] = (),
        used_skills: bool = True,
        missing: Sequence[CaptureNote] = (),
    ) -> CaptureProposal:
        """Build the cases ``jobs`` would add to set ``into``. Writes nothing."""
        if not _SET_ID.match(into):
            raise CaptureError(
                "set.id", f"{into!r} is not a set id: lowercase-kebab, starting with a letter"
            )
        # The workspace's patterns always apply; a request's are added, never instead (§4).
        patterns = _compile([*self._default_redact, *redact])
        existing_file = find_eval_set_file(self._ws, into)
        if existing_file is not None:
            try:
                load_eval_set(self._ws, str(existing_file))
            except EvalSetInvalidError as exc:
                raise CaptureError(
                    "set.invalid",
                    f"eval set {into!r} does not validate, so nothing can be added to it: {exc}",
                ) from exc
            raw: dict[str, Any] = yaml.safe_load(existing_file.read_text(encoding="utf-8")) or {}
            target = str(raw.get("target", ""))
            created = False
        else:
            topologies = sorted({str(j.topology) for j in jobs})
            if len(topologies) > 1:
                raise CaptureError(
                    "selection.mixed",
                    f"a new set has one target, and these runs are of {len(topologies)} "
                    f"topologies: {', '.join(topologies)}. Capture each into its own set.",
                )
            if not topologies:
                raise CaptureError("selection.none", "no runs to capture")
            target = topologies[0]
            if target not in self._topologies:
                raise CaptureError(
                    "target.unknown",
                    f"the runs are of topology {target!r}, which this workspace no longer has",
                )
            raw = {
                "apiVersion": "swarmkit/v1",
                "kind": "EvalSet",
                "metadata": {
                    "id": into,
                    "description": f"Cases captured from recorded runs of {target}.",
                },
                "target": target,
                "cases": [],
            }
            created = True

        cases: list[dict[str, Any]] = list(raw.get("cases") or [])
        seen_inputs = {_norm(str(c.get("input", ""))) for c in cases if isinstance(c, dict)}
        seen_runs = {
            str((c.get("source") or {}).get("run_id"))
            for c in cases
            if isinstance(c, dict) and isinstance(c.get("source"), dict)
        }
        seen_ids = {str(c.get("id")) for c in cases if isinstance(c, dict)}
        target_name = target.split("@", 1)[0]

        proposal = CaptureProposal(set_id=into, target=target, created=created)
        proposal.skipped.extend(missing)
        captured_at = _now()
        for job in jobs:
            run_id = str(job.id)
            completed = job.status == "completed"
            if not completed and not explicit:
                proposal.skipped.append(CaptureNote(run_id, "not-completed", str(job.status)))
                continue
            if str(job.topology) != target_name:
                proposal.skipped.append(CaptureNote(run_id, "wrong-target", str(job.topology)))
                continue
            if await self._had_attachments(run_id):
                proposal.skipped.append(
                    CaptureNote(run_id, "attachments", "the case input cannot carry them")
                )
                continue
            case_input = _redact(str(job.input or ""), patterns)
            if not case_input.strip() or set(case_input.split()) <= {REDACTED}:
                proposal.skipped.append(CaptureNote(run_id, "redacted-to-empty"))
                continue
            reference = ""
            if completed:
                reference = _redact(str(job.output or ""), patterns).strip()
                if not reference:
                    proposal.skipped.append(CaptureNote(run_id, "empty-output"))
                    continue
                if len(reference) > REFERENCE_MAX:
                    proposal.skipped.append(
                        CaptureNote(run_id, "output-too-long", f"{len(reference)} characters")
                    )
                    continue
            cid = case_id_for(run_id)
            if _norm(case_input) in seen_inputs or run_id in seen_runs or cid in seen_ids:
                proposal.skipped.append(CaptureNote(run_id, "duplicate"))
                continue

            case: dict[str, Any] = {"id": cid, "input": case_input}
            if completed:
                expect: dict[str, Any] = {"not_empty": True}
                if used_skills:
                    skills = await self._used_skills(run_id)
                    if skills:
                        expect["used_skills"] = skills
                expect["reference"] = reference
                expect["min_confidence"] = DEFAULT_MIN_CONFIDENCE
                case["expect"] = expect
            else:
                # Named explicitly and failed: it cannot be a baseline ("keep doing this"), so it
                # only re-runs the input — a smoke case, and said so.
                proposal.flagged.append(
                    CaptureNote(run_id, "smoke-only", f"the run {job.status}; no reference")
                )
            src: dict[str, Any] = {"run_id": run_id}
            if getattr(job, "version", None):
                src["topology_version"] = str(job.version)
            src["captured_at"] = captured_at
            case["source"] = src

            cases.append(case)
            seen_inputs.add(_norm(case_input))
            seen_runs.add(run_id)
            seen_ids.add(cid)
            proposal.added.append(cid)

        raw["cases"] = cases
        proposal.yaml = _dump(raw)
        return proposal

    def write(self, proposal: CaptureProposal, writer: _Writer) -> dict[str, Any]:
        """Write the proposal through ``writer`` — the validated eval-set write every front door
        shares. A proposal that adds nothing writes nothing."""
        if not proposal.added:
            return {"written": False, "id": proposal.set_id, "reason": "no new cases"}
        if proposal.created:
            result = writer.create_eval_set(proposal.yaml, self._topologies)
        else:
            result = writer.put_eval_set(proposal.set_id, proposal.yaml, self._topologies)
        proposal.written = True
        return {**result, "written": True}

    # ---- what the audit log says about a run -------------------------------------------------

    async def _events(self, run_id: str, event_type: str, limit: int) -> list[Any]:
        if self._audit is None:
            return []
        return [
            e async for e in self._audit.query(run_id=run_id, event_type=event_type, limit=limit)
        ]

    async def _had_attachments(self, run_id: str) -> bool:
        return bool(await self._events(run_id, "run.attachments", 1))

    async def _used_skills(self, run_id: str) -> list[str]:
        """Each distinct skill the run called and was allowed, in first-call order.

        A denied call is not something the run used. Decision skills a governance binding runs are
        recorded as ``decision.evaluated``, not ``skill.executed``, so they are never here — they
        come from the workspace, not from what the topology chose to do.
        """
        events = await self._events(run_id, "skill.executed", 2000)
        out: list[str] = []
        for e in reversed(events):  # the audit query is newest first
            decision = str(getattr(e, "policy_decision", "") or "")
            sid = getattr(e, "skill_id", None)
            if sid and decision != "deny" and sid not in out:
                out.append(str(sid))
        return out
