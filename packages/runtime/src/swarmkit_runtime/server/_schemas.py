"""Request / response bodies for the serve API — the pydantic models FastAPI validates."""

from __future__ import annotations

from pydantic import BaseModel


class RunRequest(BaseModel):
    input: str
    max_steps: int = 10


class CreateConversationRequest(BaseModel):
    topology: str


class SendMessageRequest(BaseModel):
    message: str


class RunResponse(BaseModel):
    output: str
    agent_results: dict[str, str] = {}


class JobResponse(BaseModel):
    """One run, as its detail view needs it.

    This carried five fields — id, status, topology, output, error — so a run's page could show
    WHAT came back and nothing about the run itself: not when it started, not what it was asked,
    not what it cost. Both row shapes (the in-memory `Job` and the persisted `JobRow`) had all of
    it the whole time; the response dropped it. Same shape as the audit API before 1.153.0.
    """

    job_id: str
    status: str
    topology: str = ""  # which topology this run executed (for the run-detail graph overlay)
    output: str | None = None
    error: str | None = None
    #: What the run was asked. The single most useful thing on a detail page after the output —
    #: an answer is not reviewable without the question.
    input: str = ""
    version: str | None = None
    created_at: str = ""
    completed_at: str | None = None
    #: Which front door produced it, and the pipeline run or conversation it belongs to. Null on a
    #: standalone run and on rows written before those columns existed — shown as unknown rather
    #: than guessed.
    source: str | None = None
    correlation_id: str | None = None
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None
    usage_cost_usd: float | None = None


class JobListItem(BaseModel):
    job_id: str
    topology: str
    version: str | None = None
    status: str
    created_at: str
    completed_at: str | None = None
