"""Framework-neutral query ledger read models."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer3.evidence import EvidenceLocator

if TYPE_CHECKING:  # pragma: no cover
    from transit_scholar.db.models import ClaimEvidenceLink as ClaimEvidenceLinkRow
    from transit_scholar.db.models import ClaimRecord as ClaimRecordRow
    from transit_scholar.db.models import EvidenceRecord as EvidenceRecordRow
    from transit_scholar.db.models import ResearchQueryRecord as ResearchQueryRow


QueryStatus = Literal["active", "completed", "abandoned"]
ClaimStatus = Literal["proposed", "supported", "conflicting", "rejected"]
ClaimEvidenceRelation = Literal["supports", "contradicts"]


class ResearchQueryRecord(BaseModel):
    """Immutable public representation of a persisted research query."""

    query_id: str = Field(min_length=1)
    research_session_id: str = Field(min_length=1)
    query_text: str = Field(min_length=1)
    status: QueryStatus
    parent_query_id: str | None = Field(default=None, min_length=1)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: "ResearchQueryRow") -> "ResearchQueryRecord":
        return cls(
            query_id=row.id,
            research_session_id=row.research_session_id,
            query_text=row.query_text,
            status=row.status,
            parent_query_id=row.parent_query_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class EvidenceRecord(BaseModel):
    """Immutable public representation of admitted evidence."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(min_length=1)
    research_session_id: str = Field(min_length=1)
    source_query_id: str = Field(min_length=1)
    locator: EvidenceLocator
    text_snapshot: str = Field(min_length=1)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def from_row(cls, row: "EvidenceRecordRow") -> "EvidenceRecord":
        return cls(
            evidence_id=row.id,
            research_session_id=row.research_session_id,
            source_query_id=row.source_query_id,
            locator=EvidenceLocator.model_validate(json.loads(row.locator_json)),
            text_snapshot=row.text_snapshot,
            source_metadata=json.loads(row.source_metadata_json),
            retrieval_provenance=json.loads(row.retrieval_provenance_json),
            created_at=row.created_at,
        )


class ClaimRecord(BaseModel):
    """Public representation of a caller-created persisted claim."""

    claim_id: str = Field(min_length=1)
    research_session_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    status: ClaimStatus
    rationale: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: "ClaimRecordRow") -> "ClaimRecord":
        return cls(
            claim_id=row.id,
            research_session_id=row.research_session_id,
            statement=row.statement,
            status=row.status,
            rationale=row.rationale,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ClaimEvidenceLink(BaseModel):
    """Public representation of a persisted Claim-Evidence relationship."""

    claim_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    relation: ClaimEvidenceRelation
    created_at: datetime

    @classmethod
    def from_row(cls, row: "ClaimEvidenceLinkRow") -> "ClaimEvidenceLink":
        return cls(
            claim_id=row.claim_id,
            evidence_id=row.evidence_id,
            relation=row.relation,
            created_at=row.created_at,
        )


__all__ = [
    "ClaimEvidenceLink",
    "ClaimEvidenceRelation",
    "ClaimRecord",
    "ClaimStatus",
    "EvidenceRecord",
    "QueryStatus",
    "ResearchQueryRecord",
]
