"""Explicit DTOs for the conversation HTTP contract."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=512)


class ConversationSummaryResponse(BaseModel):
    conversation_id: str
    workspace_id: str
    title: str | None = None
    created_at: datetime | None = None


class ConversationListResponse(BaseModel):
    items: list[ConversationSummaryResponse]


class AnswerEvidenceCitationResponse(BaseModel):
    """Provenance for admitted evidence supporting an Agent answer.

    This deliberately differs from the paper-library ``CitationResponse``,
    which represents bibliographic records for a paper.
    """

    evidence_id: str
    research_session_id: str
    paper_id: str | None = None
    paper_title: str | None = None
    source_kind: str
    pages: list[int] | None = None
    block_id: str | None = None
    character_start: int | None = None
    character_end: int | None = None
    parse_run_id: str | None = None
    canonical_source_version: str | None = None
    evidence_quote: str | None = None


class TurnResponse(BaseModel):
    turn_id: str
    conversation_id: str
    sequence: int
    user_message: str
    resolved_user_goal: str | None = None
    agent_run_id: str | None = None
    status: str
    assistant_response: dict[str, Any] | None = None
    final_answer: str | None = None
    answer_citations: list[AnswerEvidenceCitationResponse] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class ConversationResponse(ConversationSummaryResponse):
    turns: list[TurnResponse]
