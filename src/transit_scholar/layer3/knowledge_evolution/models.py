from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

CandidateStatus = Literal["proposed", "accepted", "rejected"]

class KnowledgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    originating_agent_run_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_claim_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = ()
    status: CandidateStatus = "proposed"
    proposed_target_entry_id: str | None = None

class AgenticWikiEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_claim_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = ()
    paper_ids: tuple[str, ...] = ()
    originating_agent_run_id: str = Field(min_length=1)
    status: Literal["active", "stale", "superseded"] = "active"
    superseded_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
