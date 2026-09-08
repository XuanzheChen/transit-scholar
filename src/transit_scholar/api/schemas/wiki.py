"""Explicit structured DTOs for Workspace Wiki resources."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class BaseWikiStatusResponse(BaseModel):
    workspace_id: str
    status: Literal["unsupported", "missing", "ready", "stale", "error"]
    manifest_status: str | None = None
    fingerprint: str | None = None
    recorded_fingerprint: str | None = None
    build_revision: int | None = None
    built_at: datetime | None = None
    error_code: str | None = None


class BaseWikiCapabilityResponse(BaseModel):
    build_supported: bool
    read_supported: bool
    reason: str | None = None


class WikiOverviewResponse(BaseModel):
    workspace_id: str
    base_wiki: BaseWikiStatusResponse
    base_wiki_capability: BaseWikiCapabilityResponse
    agentic_wiki_entry_count: int


class WikiBuildResponse(BaseModel):
    workspace_id: str
    status: BaseWikiStatusResponse
    fingerprint: str
    build_revision: int


class WikiPageResponse(BaseModel):
    page_id: str
    workspace_id: str
    paper_id: str
    title: str
    summary: str
    schema_id: str
    schema_version: str
    build_status: str
    created_at: datetime
    updated_at: datetime
    build_revision: int


class WikiPageListResponse(BaseModel):
    items: list[WikiPageResponse]


class WikiEntityResponse(BaseModel):
    entity_id: str
    workspace_id: str
    canonical_name: str
    aliases: list[str]
    description: str
    kind: str | None = None
    created_at: datetime
    updated_at: datetime


class WikiEntityListResponse(BaseModel):
    items: list[WikiEntityResponse]


class AgenticWikiEntryResponse(BaseModel):
    entry_id: str
    workspace_id: str
    title: str
    content: str
    source_claim_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    paper_ids: tuple[str, ...]
    originating_agent_run_id: str
    status: Literal["active", "stale", "superseded"]
    superseded_by: str | None = None
    created_at: datetime
    updated_at: datetime


class AgenticWikiEntryListResponse(BaseModel):
    items: list[AgenticWikiEntryResponse]


class WikiSearchHitResponse(BaseModel):
    type: Literal["page", "entity", "entry"]
    object_id: str
    title: str
    score: float
    snippet: str
    retrieval_mode: Literal["lexical", "semantic"]
    source_kind: Literal["base_wiki", "agentic_wiki"]
    lifecycle_status: Literal["active", "stale"] | None = None
    source_score: float | None = None
    local_rank: int | None = None
    fusion_score: float | None = None


class WikiSearchResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    hits: list[WikiSearchHitResponse] = Field(default_factory=list)
    error_code: str | None = None
    source_status: dict[str, str] = Field(default_factory=dict)
    source_errors: dict[str, str | None] = Field(default_factory=dict)
