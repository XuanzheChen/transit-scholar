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


from .run_control import RunStateResponse, TimelineEventResponse, TimelineResponse
from .conversations import (
    AnswerEvidenceCitationResponse, ConversationCreateRequest, ConversationListResponse, ConversationResponse,
    ConversationSummaryResponse, TurnResponse,
)
from .schema_catalog import SchemaDraftRequest, SchemaResponse, SchemaValidationResponse
from .papers import (
    CitationResponse, DuplicateRelationListResponse, DuplicateRelationResponse,
    DuplicateResolutionRequest, DuplicateResolutionResponse, EnrichmentResponse,
    MetadataCandidateResponse, MetadataUpdateRequest, PaperActionResponse,
    PaperDetailResponse, PaperFileResponse, PaperImportResponse, PaperListResponse as PaperLibraryListResponse,
    PaperSummaryResponse,
)
from .workspaces import (
    PaperSchemaStateResponse, SchemaMaterializationResponse, WorkspaceCreateRequest,
    WorkspaceListResponse, WorkspacePaperListResponse, WorkspacePaperRequest,
    WorkspacePaperResponse, WorkspaceResponse, WorkspaceSchemaResponse,
)
from .wiki import (
    AgenticWikiEntryListResponse, AgenticWikiEntryResponse,
    BaseWikiCapabilityResponse, BaseWikiStatusResponse, WikiBuildResponse,
    WikiEntityListResponse, WikiEntityResponse, WikiOverviewResponse,
    WikiPageListResponse, WikiPageResponse, WikiSearchHitResponse,
    WikiSearchResponse,
)

__all__ = [
    "ErrorBody", "ErrorEnvelope", "HealthResponse", "CapabilityResponse",
    "PaperSummary", "PaperListResponse", "TurnCreateRequest",
    "TurnSubmissionResponse", "RunStateResponse", "TimelineEventResponse", "TimelineResponse",
    "AnswerEvidenceCitationResponse", "ConversationCreateRequest", "ConversationListResponse", "ConversationResponse",
    "ConversationSummaryResponse", "TurnResponse",
    "CitationResponse", "DuplicateRelationListResponse", "DuplicateRelationResponse",
    "DuplicateResolutionRequest", "DuplicateResolutionResponse", "EnrichmentResponse",
    "MetadataCandidateResponse", "MetadataUpdateRequest", "PaperActionResponse",
    "PaperDetailResponse", "PaperFileResponse", "PaperImportResponse", "PaperSummaryResponse",
    "PaperLibraryListResponse",
    "SchemaDraftRequest", "SchemaResponse", "SchemaValidationResponse",
    "WorkspaceCreateRequest", "WorkspaceResponse", "WorkspaceListResponse",
    "WorkspacePaperRequest", "WorkspacePaperResponse", "WorkspacePaperListResponse",
    "WorkspaceSchemaResponse", "PaperSchemaStateResponse", "SchemaMaterializationResponse",
    "BaseWikiStatusResponse", "BaseWikiCapabilityResponse", "WikiOverviewResponse",
    "WikiBuildResponse", "WikiPageResponse", "WikiPageListResponse",
    "WikiEntityResponse", "WikiEntityListResponse", "AgenticWikiEntryResponse",
    "AgenticWikiEntryListResponse", "WikiSearchHitResponse", "WikiSearchResponse",
]
