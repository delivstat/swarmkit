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
    min_confidence: float = 0.5  # judge/rubric must pass with at least this confidence


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    input: str
    expect: ExpectSpec = Field(default_factory=ExpectSpec)


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
            "cases": [
                {
                    "case_id": c.case_id,
                    "passed": c.passed,
                    "error": c.error,
                    "output": c.output,
                    "checks": [
                        {"name": ck.name, "passed": ck.passed, "detail": ck.detail}
                        for ck in c.checks
                    ],
                }
                for c in self.cases
            ],
        }
