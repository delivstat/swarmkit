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
    job_id: str
    status: str
    output: str | None = None
    error: str | None = None


class JobListItem(BaseModel):
    job_id: str
    topology: str
    version: str | None = None
    status: str
    created_at: str
    completed_at: str | None = None
