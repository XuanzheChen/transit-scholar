"""ResearchEvidence contracts built on the stable L3S2 EvidenceLocator."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import EvidenceLocator


class QueryProvenance(BaseModel):
    """Identity of the query which caused evidence to be retrieved."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query_id: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    query_text: str | None = Field(default=None, min_length=1)


class PaperProvenance(BaseModel):
    """Paper identity and optional source metadata for Paper evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    paper_id: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    source_uri: str | None = Field(default=None, min_length=1)
    parse_run_id: str | None = Field(default=None, min_length=1)
    canonical_source_version: str | None = Field(default=None, min_length=1)


class ResearchEvidence(BaseModel):
    """Retrieved evidence, explicitly distinct from a Claim or conclusion."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1)
    locator: EvidenceLocator
    text: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    query_provenance: QueryProvenance | None = None
    paper_provenance: PaperProvenance | None = None
    section: str | None = Field(default=None, min_length=1)
    retrieval_provenance: dict[str, object] = Field(default_factory=dict)
    rerank_provenance: dict[str, object] = Field(default_factory=dict)
    final_rank: int | None = Field(default=None, ge=1)


__all__ = ["PaperProvenance", "QueryProvenance", "ResearchEvidence"]
