"""Stage 1 core ORM models.

Four tables form the minimal skeleton:
    papers            logical paper identity
    paper_files       concrete stored file
    paper_authors     ordered author list per paper
    ingestion_jobs    one record per import attempt

All primary keys are UUID strings generated in Python.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from transit_scholar.db.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class Paper(Base):
    """Logical identity of an academic paper."""

    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    normalized_title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    abstract: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    publication_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    venue: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    doi: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    normalized_doi: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    arxiv_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    files: Mapped[List["PaperFile"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    authors: Mapped[List["PaperAuthor"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="PaperAuthor.author_order"
    )
    ingestion_jobs: Mapped[List["IngestionJob"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan"
    )
    metadata_candidates: Mapped[List["MetadataCandidate"]] = relationship(
        cascade="all, delete-orphan"
    )
    source_relations: Mapped[List["PaperRelation"]] = relationship(
        foreign_keys="PaperRelation.source_paper_id",
        back_populates="source_paper",
        cascade="all, delete-orphan",
    )
    target_relations: Mapped[List["PaperRelation"]] = relationship(
        foreign_keys="PaperRelation.target_paper_id",
        back_populates="target_paper",
        cascade="all, delete-orphan",
    )
    citation_records: Mapped[List["CitationRecord"]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
        order_by="CitationRecord.created_at",
    )

    __table_args__ = (
        Index("ix_papers_normalized_title", "normalized_title"),
        Index("ix_papers_normalized_doi", "normalized_doi"),
        Index("ix_papers_arxiv_id", "arxiv_id"),
        Index("ix_papers_status", "status"),
    )


class PaperFile(Base):
    """A concrete file stored in the local library."""

    __tablename__ = "paper_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    paper_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=True
    )
    original_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    stored_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    relative_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pdf_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    is_encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_scanned_candidate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    paper: Mapped[Optional["Paper"]] = relationship(back_populates="files")
    ingestion_jobs: Mapped[List["IngestionJob"]] = relationship(back_populates="paper_file")
    metadata_candidates: Mapped[List["MetadataCandidate"]] = relationship(
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("sha256", name="uq_paper_files_sha256"),
        UniqueConstraint("relative_path", name="uq_paper_files_relative_path"),
    )


class PaperAuthor(Base):
    """An author belonging to a paper, in order."""

    __tablename__ = "paper_authors"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(String(32), ForeignKey("papers.id"), nullable=False)
    author_order: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    normalized_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    affiliation: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    orcid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    paper: Mapped["Paper"] = relationship(back_populates="authors")

    __table_args__ = (
        Index("ix_paper_authors_normalized_name", "normalized_name"),
    )


class IngestionJob(Base):
    """One import attempt, from upload through dedup to acceptance/failure."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    uploaded_filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("paper_files.id"), nullable=True
    )
    paper_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="created", nullable=False)
    current_stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    paper_file: Mapped[Optional["PaperFile"]] = relationship(back_populates="ingestion_jobs")
    paper: Mapped[Optional["Paper"]] = relationship(back_populates="ingestion_jobs")

    __table_args__ = (
        Index("ix_ingestion_jobs_status", "status"),
    )


class MetadataCandidate(Base):
    """A single candidate value for a paper field, from one source."""

    __tablename__ = "metadata_candidates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=False
    )
    paper_file_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("paper_files.id"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    value_text: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_location: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_metadata_candidates_paper_id", "paper_id"),
        Index("ix_metadata_candidates_paper_file_id", "paper_file_id"),
        Index("ix_metadata_candidates_field_name", "field_name"),
        Index("ix_metadata_candidates_source_type", "source_type"),
        Index("ix_metadata_candidates_is_selected", "is_selected"),
    )


# ---------------------------------------------------------------------------
# Stage 4: paper-level duplicate detection + manual adjudication
# ---------------------------------------------------------------------------


class PaperRelation(Base):
    """A candidate or confirmed relation between two papers.

    Direction is normalised by the service layer: the lexically-smaller
    paper id is always stored as ``source_paper_id`` and the larger as
    ``target_paper_id``. This guarantees at most one row per unordered
    pair + relation_type.
    """

    __tablename__ = "paper_relations"

    # --- relation_type values -----------------------------------------------
    # exact_duplicate / probable_duplicate / possible_version / supplement_of / related
    # --- status values ------------------------------------------------------
    # pending / confirmed / rejected / ignored

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source_paper_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=False
    )
    target_paper_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    source_paper: Mapped["Paper"] = relationship(
        foreign_keys=[source_paper_id], back_populates="source_relations"
    )
    target_paper: Mapped["Paper"] = relationship(
        foreign_keys=[target_paper_id], back_populates="target_relations"
    )

    __table_args__ = (
        CheckConstraint(
            "source_paper_id != target_paper_id",
            name="ck_paper_relations_no_self",
        ),
        UniqueConstraint(
            "source_paper_id",
            "target_paper_id",
            "relation_type",
            name="uq_paper_relations_triple",
        ),
        Index("ix_paper_relations_source_paper_id", "source_paper_id"),
        Index("ix_paper_relations_target_paper_id", "target_paper_id"),
        Index("ix_paper_relations_relation_type", "relation_type"),
        Index("ix_paper_relations_status", "status"),
        Index("ix_paper_relations_confidence", "confidence"),
    )


class AuditLog(Base):
    """Append-only record of manual processing actions and key state changes."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(
        String(64), default="local_user", nullable=False
    )
    old_value_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_actor_type", "actor_type"),
        Index("ix_audit_logs_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# Stage 5: citation structured import + basic rendering
# ---------------------------------------------------------------------------


class CitationRecord(Base):
    """Structured intermediate representation of a citation attached to a paper.

    ``structured_json`` holds CSL-like JSON produced by the parser. Rendering
    only reads this field; it never reads ``Paper`` columns to fill gaps.
    """

    __tablename__ = "citation_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=False
    )
    source_format: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    parse_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="parsed"
    )
    parse_warnings_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    is_selected: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    paper: Mapped["Paper"] = relationship(back_populates="citation_records")
    citation_renders: Mapped[List["CitationRender"]] = relationship(
        back_populates="citation_record",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_citation_records_paper_id", "paper_id"),
        Index("ix_citation_records_is_selected", "is_selected"),
        Index("ix_citation_records_deleted_at", "deleted_at"),
        Index("ix_citation_records_source_format", "source_format"),
    )


class CitationRender(Base):
    """A rendered citation string for a given record + style + locale + version.

    Uniqueness is enforced on ``(citation_record_id, style, locale,
    renderer_version)`` so re-rendering overwrites the cached text.
    """

    __tablename__ = "citation_renders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    citation_record_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("citation_records.id"), nullable=False
    )
    style: Mapped[str] = mapped_column(String(32), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-US")
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False)
    renderer_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="stage5-basic-v1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    citation_record: Mapped["CitationRecord"] = relationship(
        back_populates="citation_renders"
    )

    __table_args__ = (
        UniqueConstraint(
            "citation_record_id",
            "style",
            "locale",
            "renderer_version",
            name="uq_citation_renders_quad",
        ),
        Index("ix_citation_renders_citation_record_id", "citation_record_id"),
    )


# ---------------------------------------------------------------------------
# DOI metadata enrichment: job + per-provider result records
# ---------------------------------------------------------------------------


class DOIEnrichmentJob(Base):
    """One DOI enrichment task per paper.

    ``paper_id`` is UNIQUE so a paper has at most one enrichment job. The
    derived enrichment status for a paper is read from this table; no column is
    added to ``papers``.
    """

    __tablename__ = "doi_enrichment_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=False, unique=True
    )
    doi: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    provider_results: Mapped[List["DOIProviderResult"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_doi_enrichment_jobs_paper_id", "paper_id"),
        Index("ix_doi_enrichment_jobs_status", "status"),
    )


class DOIProviderResult(Base):
    """One provider's request/cache record for a DOI lookup.

    ``request_url`` and ``request_headers_json`` never store API key material:
    keys are stripped before write.
    """

    __tablename__ = "doi_provider_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("doi_enrichment_jobs.id"), nullable=False
    )
    paper_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=False
    )
    doi: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    request_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    request_headers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job: Mapped["DOIEnrichmentJob"] = relationship(back_populates="provider_results")

    __table_args__ = (
        UniqueConstraint("job_id", "provider", name="uq_doi_provider_results_job_provider"),
        Index("ix_doi_provider_results_paper_id", "paper_id"),
        Index("ix_doi_provider_results_provider", "provider"),
        Index("ix_doi_provider_results_status", "status"),
        Index("ix_doi_provider_results_next_retry_at", "next_retry_at"),
    )


# ---------------------------------------------------------------------------
# Layer3 Stage1: Workspace control plane (REQ-001/REQ-002/REQ-003/REQ-006)
# ---------------------------------------------------------------------------

#: Workspace lifecycle states (REQ-001).
WORKSPACE_STATUSES = ("active", "archived", "deleting", "deleted")

#: Workspace schema modes (REQ-003): exactly one of bound / none.
WORKSPACE_SCHEMA_MODES = ("bound", "none")


class Workspace(Base):
    """Persistent Layer3 Workspace knowledge boundary (control plane).

    ``schema_mode`` is bound or none. For bound mode the concrete
    ``schema_id`` / ``schema_version`` / ``schema_hash`` triple is persisted;
    for none mode all three columns MUST be NULL. The database CHECK
    constraint ``ck_workspaces_schema_mode_consistency`` enforces the
    bound-vs-none invariant independently of the service layer.

    ``revision`` is a monotonic version marker advanced by every authoritative
    mutation (membership or lifecycle change). Schema binding is immutable
    within Layer3 Stage1, so it never advances the revision.
    """

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="active", nullable=False
    )
    schema_mode: Mapped[str] = mapped_column(
        String(16), default="none", nullable=False
    )
    schema_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    schema_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    schema_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    memberships: Mapped[List["WorkspacePaperMembership"]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        order_by="WorkspacePaperMembership.paper_id",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived', 'deleting', 'deleted')",
            name="ck_workspaces_status",
        ),
        CheckConstraint(
            "schema_mode IN ('bound', 'none')",
            name="ck_workspaces_schema_mode",
        ),
        CheckConstraint(
            "(schema_mode = 'bound' AND schema_id IS NOT NULL "
            "AND schema_version IS NOT NULL AND schema_hash IS NOT NULL) "
            "OR (schema_mode = 'none' AND schema_id IS NULL "
            "AND schema_version IS NULL AND schema_hash IS NULL)",
            name="ck_workspaces_schema_mode_consistency",
        ),
        CheckConstraint("revision >= 1", name="ck_workspaces_revision_positive"),
        Index("ix_workspaces_status", "status"),
        Index("ix_workspaces_schema_mode", "schema_mode"),
    )


class WorkspacePaperMembership(Base):
    """Workspace-to-Paper membership (REQ-002).

    A single global Paper MAY be visible in many Workspaces; inclusion is
    modelled here rather than as a ``workspace_id`` column on the global
    Paper record. The pair ``(workspace_id, paper_id)`` is unique, so a Paper
    can never be a duplicate member of one Workspace.
    """

    __tablename__ = "workspace_paper_memberships"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), nullable=False
    )
    paper_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("papers.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="memberships")
    paper: Mapped["Paper"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "paper_id", name="uq_workspace_paper_membership_pair"
        ),
        Index("ix_workspace_paper_memberships_workspace_id", "workspace_id"),
        Index("ix_workspace_paper_memberships_paper_id", "paper_id"),
    )


# ---------------------------------------------------------------------------
# Layer3 Stage2: framework-neutral research execution identities
# ---------------------------------------------------------------------------

AGENT_EXECUTION_STATUSES = (
    "created",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
)


class AgentRun(Base):
    """One durable Agent task bound to a Workspace revision."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id"), nullable=False
    )
    user_goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="created", nullable=False)
    workspace_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship()
    research_sessions: Mapped[List["ResearchSession"]] = relationship(
        back_populates="agent_run",
        cascade="all, delete-orphan",
        order_by="ResearchSession.created_at, ResearchSession.id",
    )
    trace_events: Mapped[List["AgentTraceEvent"]] = relationship(
        back_populates="agent_run",
        cascade="all, delete-orphan",
        order_by="AgentTraceEvent.sequence",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "workspace_revision >= 1", name="ck_agent_runs_workspace_revision_positive"
        ),
        Index("ix_agent_runs_workspace_id", "workspace_id"),
        Index("ix_agent_runs_status", "status"),
    )


class ResearchSession(Base):
    """One independently executable research question within an AgentRun."""

    __tablename__ = "research_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    agent_run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agent_runs.id"), nullable=False
    )
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="created", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    agent_run: Mapped["AgentRun"] = relationship(back_populates="research_sessions")
    research_state: Mapped["ResearchState | None"] = relationship(
        back_populates="research_session", cascade="all, delete-orphan", uselist=False
    )
    trace_events: Mapped[List["AgentTraceEvent"]] = relationship(
        back_populates="research_session"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="ck_research_sessions_status",
        ),
        Index("ix_research_sessions_agent_run_id", "agent_run_id"),
        Index("ix_research_sessions_status", "status"),
    )


class ResearchState(Base):
    """Durable, framework-neutral working state for one ResearchSession."""

    __tablename__ = "research_states"

    research_session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("research_sessions.id"), primary_key=True
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    research_session: Mapped["ResearchSession"] = relationship(
        back_populates="research_state"
    )


class AgentTraceEvent(Base):
    """An append-only execution event, ordered within one AgentRun."""

    __tablename__ = "agent_trace_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    agent_run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agent_runs.id"), nullable=False
    )
    research_session_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("research_sessions.id"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    agent_run: Mapped["AgentRun"] = relationship(back_populates="trace_events")
    research_session: Mapped["ResearchSession | None"] = relationship(
        back_populates="trace_events"
    )

    __table_args__ = (
        CheckConstraint("sequence >= 1", name="ck_agent_trace_events_sequence_positive"),
        UniqueConstraint(
            "agent_run_id", "sequence", name="uq_agent_trace_events_run_sequence"
        ),
        Index("ix_agent_trace_events_agent_run_sequence", "agent_run_id", "sequence"),
        Index("ix_agent_trace_events_research_session_id", "research_session_id"),
        Index("ix_agent_trace_events_event_type", "event_type"),
    )
