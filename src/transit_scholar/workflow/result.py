"""Return structures for the workflow package.

ImportPipelineResult, PaperSummary, PaperDetail and SecondLayerInputResult
are the frozen dataclass shapes for Stage 6's aggregation layer. No business
logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# --- pipeline status constants -------------------------------------------------
PIPELINE_COMPLETED = "completed"
PIPELINE_DUPLICATE = "duplicate"
PIPELINE_PARTIAL = "partial"
PIPELINE_FAILED = "failed"
PIPELINE_AWAITING_USER_REVIEW = "awaiting_user_review"


@dataclass
class ImportPipelineResult:
    """Outcome of a single run_import_pipeline() call."""

    status: str                              # completed / duplicate / partial / failed / awaiting_user_review
    job_id: str | None
    paper_id: str | None
    file_id: str | None
    is_exact_duplicate: bool
    import_status: str | None                # accepted / rejected / failed
    metadata_status: str | None              # extracted / partial / failed
    duplicate_status: str | None             # completed / failed
    relations_created: int
    relations_existing: int
    relation_ids: list[str]
    current_stage: str | None
    error_code: str | None
    error_message: str | None
    warnings: list[str]
    second_layer_ready: bool
    second_layer_blockers: list[str]
    metadata_quality_flags: list[str] = field(default_factory=list)
    metadata_enrichment_status: str | None = None
    enrichment_provider_results: list[dict[str, object]] | None = None


@dataclass
class PaperSummary:
    """A single row returned by list_papers()."""

    paper_id: str
    title: str | None
    publication_year: int | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    status: str
    primary_file_id: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass
class PaperDetail:
    """Detailed view returned by get_paper()."""

    paper_id: str
    title: str | None
    normalized_title: str | None
    abstract: str | None
    publication_year: int | None
    venue: str | None
    doi: str | None
    normalized_doi: str | None
    arxiv_id: str | None
    status: str
    authors: list[dict[str, object]]
    files: list[dict[str, object]]
    duplicate_relations: list[dict[str, object]]
    created_at: datetime | None
    updated_at: datetime | None
    deleted_at: datetime | None


@dataclass
class SecondLayerInputResult:
    """Outcome of a single get_second_layer_input() call."""

    status: str                              # ready / blocked / failed
    paper_id: str
    primary_file_id: str | None
    source_pdf_path: str | None
    relative_path: str | None
    title: str | None
    authors: list[str]
    year: int | None
    doi: str | None
    arxiv_id: str | None
    page_count: int | None
    identity_status: str | None
    duplicate_status: str | None
    blockers: list[str]
    error_code: str | None
    error_message: str | None
    metadata_quality_flags: list[str] = field(default_factory=list)
