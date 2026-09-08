"""Stable API DTOs (kept separate from Product/Core models)."""
from pydantic import BaseModel, ConfigDict, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str = "healthy"


class CapabilityResponse(BaseModel):
    pause_resume: bool
    user_schema_creation: bool
    base_wiki: bool
    agentic_wiki: bool
    semantic_wiki_search: bool
    pdf_upload_max_bytes: int


class PaperSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_id: str
    title: str | None = None
    status: str | None = None


class PaperListResponse(BaseModel):
    items: list[PaperSummary]


class TurnCreateRequest(BaseModel):
    message: str = Field(min_length=1)


class TurnSubmissionResponse(BaseModel):
    turn_id: str
    agent_run_id: str
