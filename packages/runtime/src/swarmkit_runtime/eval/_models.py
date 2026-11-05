"""Eval-set input models + result value objects.

An eval set is validated against ``eval-set.schema.json`` first (``_loader``), then narrowed to
these models, which give the runner plain types and defaults. They mirror the schema's fields
one for one — ``test_eval_set_schema.py`` keeps the two in step. Results are internal frozen
dataclasses, matching the RunResult / DecisionSkillResult style.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExpectSpec(BaseModel):
    """Expectations for one case. A case passes when every set expectation passes."""

    model_config = ConfigDict(extra="forbid")

    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    regex: str | None = None
    equals: str | None = None
    not_empty: bool = False
    used_skills: list[str] = Field(default_factory=list)  # skill ids that must be invoked
    judge: str | None = None  # a decision-skill id to score the output (LLM rubric)
    rubric: str | None = None  # an inline rubric string judged by the LLM (no skill needed)
    #: a known-good answer the judge model compares the output to; passed as data, not instructions
    reference: str | None = None
    min_confidence: float = 0.5  # judge/rubric/reference must pass with at least this confidence


class CaseSource(BaseModel):
    """Where a captured case came from. Informational: the runner never reads it."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    topology_version: str | None = None
    captured_at: str | None = None


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    input: str
    expect: ExpectSpec = Field(default_factory=ExpectSpec)
    source: CaseSource | None = None


class EvalSetMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    description: str = ""


class EvalSet(BaseModel):
    """An eval-set artifact: a topology target + the cases to score it on."""

    model_config = ConfigDict(extra="forbid")

    apiVersion: str = "swarmkit/v1"
    kind: str = "EvalSet"
    metadata: EvalSetMeta
    target: str  # topology name to run each case against; `name@version` pins one version
    cases: list[EvalCase]
    provenance: dict[str, Any] | None = None


# ---- results (internal value objects) ----


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    output: str = ""
    error: str | None = None
    #: what the case's run cost, from its usage. Judge and rubric calls are not included: a
    #: decision-skill verdict carries no usage today.
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "error": self.error,
            "output": self.output,
            "cost_usd": round(self.cost_usd, 6),
            "checks": [
                {"name": ck.name, "passed": ck.passed, "detail": ck.detail} for ck in self.checks
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalCaseResult:
        """Rebuild a recorded case — the inverse of :meth:`to_dict`, for a resumed run."""
        checks = data.get("checks") or []
        return cls(
            case_id=str(data.get("case_id", "")),
            passed=bool(data.get("passed")),
            checks=[
                CheckResult(str(c.get("name", "")), bool(c.get("passed")), str(c.get("detail", "")))
                for c in checks
                if isinstance(c, dict)
            ],
            output=str(data.get("output") or ""),
            error=data.get("error"),
            cost_usd=float(data.get("cost_usd") or 0.0),
        )


@dataclass(frozen=True)
class EvalReport:
    eval_set_id: str
    target: str
    cases: list[EvalCaseResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.cases)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "eval_set_id": self.eval_set_id,
            "target": self.target,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cost_usd": round(self.cost_usd, 6),
            "cases": [c.to_dict() for c in self.cases],
        }
