"""Request / response bodies for the serve API — the pydantic models FastAPI validates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AttachmentSpec(BaseModel):
    """A file the caller is putting in front of the run.

    ``design/details/images-on-both-executors.md``. Exactly one of ``path`` (workspace-relative) or
    ``data`` (base64). There is deliberately **no type field**: the media type is read from the
    bytes, because accepting a caller's claim about content that is about to be forwarded to a
    third-party model is not a claim worth honouring. ``extra="forbid"`` makes that a 422 naming
    the field rather than a silently ignored key — including ``url``, which is refused on purpose:
    having the runtime fetch a caller-supplied address is the same exfiltration primitive the
    design rejected for prompt-scraped paths.
    """

    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    data: str | None = None
    #: Display/filename only. Some providers want one on the wire; it is metadata, not a type claim.
    #: Derived from ``path`` when absent.
    name: str | None = None
    #: ``preprocess`` (default) converts to something every provider takes; ``native`` passes the
    #: bytes through to providers that have a shape for them. Only images are carried today, and
    #: an image is never preprocessed, so this is inert until document/audio support lands.
    handling: Literal["preprocess", "native"] = "preprocess"


class RunRequest(BaseModel):
    input: str
    max_steps: int = 10
    #: Files put in front of the entry agent. Resolved and validated before the run starts, so a
    #: bad path is a 4xx on this call rather than a failed job. They reach the root agent's first
    #: message and no downstream node — an agent that wants a file it was not handed asks for one
    #: through a skill.
    attachments: list[AttachmentSpec] = Field(default_factory=list)
    #: Group this run with others — a ticket, a requirement, a pipeline run. The CLI has had this
    #: since 1.176.0 and the HTTP surface did not, so a run started over the API could not be
    #: correlated at all: `jobs.correlation_id` was NULL and the whole chain
    #: `jobs -> audit_events.run_id -> artifacts` had nothing to hang off. That is the surface an
    #: application sequencing its own runs actually uses.
    correlation_id: str | None = None
    #: Arbitrary grouping, opaque to the runtime — it carries the caller's model, not ours.
    labels: dict[str, str] = Field(default_factory=dict)
    #: The job this run supersedes. A rejected artifact is redone by running AGAIN, which writes a
    #: new job; `correlation_id` cannot express that, because it already means "same ticket" and
    #: holds different units of work as well as retries. The column and the read path shipped in
    #: 1.189.0 with no way for a caller to set it, which made the chain unwritable over HTTP.
    parent_job_id: str | None = None


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
    #: Total characters of unified diff this run produced, or None when no harness diff was
    #: carried out of it. The CONTENT is at `GET /jobs/{id}/diff`, so an ordinary job fetch does
    #: not carry megabytes — but the length is here, because "changed nothing" and "the changes
    #: were dropped" used to be the same answer and that is what made the loss dangerous.
    diff_length: int | None = None
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
