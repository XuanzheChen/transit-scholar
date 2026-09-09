"""Explicit HTTP DTOs for the paper library contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PaperSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: str
    title: str | None = None
    publication_year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    status: str
    primary_file_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PaperListResponse(BaseModel):
    items: list[PaperSummaryResponse]


class PaperFileResponse(BaseModel):
    file_id: str
    original_filename: str | None = None
    mime_type: str | None = None
    file_size_bytes: int | None = None
    is_primary: bool
    page_count: int | None = None


class PaperDetailResponse(PaperSummaryResponse):
    normalized_title: str | None = None
    abstract: str | None = None
    normalized_doi: str | None = None
    authors: list[dict[str, object]] = Field(default_factory=list)
    files: list[PaperFileResponse] = Field(default_factory=list)
    duplicate_relations: list[dict[str, object]] = Field(default_factory=list)
    deleted_at: str | None = None


class PaperImportResponse(BaseModel):
    paper_id: str | None = None
    file_id: str | None = None
    status: str
    import_status: str | None = None
    metadata_status: str | None = None
    duplicate_status: str | None = None
    current_stage: str | None = None
    second_layer_ready: bool
    second_layer_blockers: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

class SecondLayerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: str
    status: str
    second_layer_ready: bool
    second_layer_blockers: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class MetadataUpdateRequest(BaseModel):
    title: str | None = None
    abstract: str | None = None
    publication_year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    authors: list[str] | None = None

    def supplied_fields(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True)


class PaperActionResponse(BaseModel):
    paper_id: str
    status: str
    updated_fields: list[str] = Field(default_factory=list)
    audit_log_id: str | None = None


class DuplicateRelationResponse(BaseModel):
    relation_id: str
    source_paper_id: str
    target_paper_id: str
    relation_type: str
    confidence: float
    status: str
    reasons: list[dict[str, object]] = Field(default_factory=list)


class DuplicateRelationListResponse(BaseModel):
    items: list[DuplicateRelationResponse]


class DuplicateResolutionRequest(BaseModel):
    decision: Literal["same_paper", "different_version", "not_duplicate", "ignore"]


class DuplicateResolutionResponse(BaseModel):
    relation_id: str
    status: str
    decision: str
    audit_log_id: str | None = None


class EnrichmentResponse(BaseModel):
    paper_id: str
    doi: str | None = None
    metadata_enrichment_status: str
    providers: list[dict[str, object]] = Field(default_factory=list)
    resolved: dict[str, str] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


class MetadataCandidateResponse(BaseModel):
    id: str
    paper_id: str | None = None
    paper_file_id: str | None = None
    field_name: str
    value_text: str | None = None
    source_type: str
    source_location: str | None = None
    confidence: float
    is_selected: bool


class CitationResponse(BaseModel):
    id: str
    paper_id: str
    source_format: str
    raw_text: str
    structured_json: str | None = None
    parse_status: str
    parse_warnings: list[str] = Field(default_factory=list)
    is_selected: bool
