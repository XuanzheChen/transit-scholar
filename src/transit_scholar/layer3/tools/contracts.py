"""Thin framework-neutral contracts for Layer3 knowledge retrieval tools."""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer3.evidence import ResearchEvidence
from transit_scholar.layer3.retrieval import (
    RerankDiagnostics,
    ResearchQuery,
    RetrievalDiagnostic,
    RetrievalStrategy,
    SchemaResult,
    WikiNavigationResult,
)
from transit_scholar.layer3.rerank import LLMFineRerankDiagnostics


class RetrievalResultEnvelope(BaseModel):
    """Unified result preserving Schema, Wiki, and evidence semantics."""

    model_config = ConfigDict(extra="forbid")

    query: ResearchQuery
    strategy: RetrievalStrategy | None = None
    schema_results: list[SchemaResult] = Field(default_factory=list)
    wiki_results: list[WikiNavigationResult] = Field(default_factory=list)
    evidence_results: list[ResearchEvidence] = Field(default_factory=list)
    diagnostics: list[RetrievalDiagnostic] = Field(default_factory=list)
    workspace_revision: int | None = Field(default=None, ge=1)
    searched_paper_ids: list[str] = Field(default_factory=list)
    skipped_paper_ids: list[str] = Field(default_factory=list)
    unavailable_paper_ids: list[str] = Field(default_factory=list)
    failed_paper_ids: list[str] = Field(default_factory=list)
    rerank_diagnostics: LLMFineRerankDiagnostics | RerankDiagnostics | None = None


class KnowledgeToolDefinition(BaseModel):
    """A tool's portable identity and contracts, independent of any runtime."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_contract: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)


ToolHandler = Callable[..., RetrievalResultEnvelope]


RETRIEVE_KNOWLEDGE = KnowledgeToolDefinition(
    name="retrieve_knowledge",
    description="Plan and execute retrieval for one already-formed ResearchQuery.",
    input_contract="ResearchQuery",
    output_contract="RetrievalResultEnvelope",
)
SEARCH_SCHEMA = KnowledgeToolDefinition(
    name="search_schema",
    description="Execute a direct structured Schema retrieval action.",
    input_contract="SchemaRetrievalAction",
    output_contract="RetrievalResultEnvelope",
)
SEARCH_WIKI = KnowledgeToolDefinition(
    name="search_wiki",
    description="Execute a direct Wiki navigation or discovery action.",
    input_contract="WikiRetrievalAction",
    output_contract="RetrievalResultEnvelope",
)
SEARCH_RAG = KnowledgeToolDefinition(
    name="search_rag",
    description="Execute direct Paper-scoped source-grounded RAG retrieval.",
    input_contract="RagRetrievalAction",
    output_contract="RetrievalResultEnvelope",
)
SEARCH_WORKSPACE_RAG = KnowledgeToolDefinition(
    name="search_workspace_rag",
    description="Execute direct Workspace-wide source-grounded RAG retrieval.",
    input_contract="RagRetrievalAction",
    output_contract="RetrievalResultEnvelope",
)
INSPECT_EVIDENCE = KnowledgeToolDefinition(
    name="inspect_evidence",
    description="Inspect a previously returned ResearchEvidence item.",
    input_contract="ResearchEvidence",
    output_contract="ResearchEvidence",
)


__all__ = [
    "INSPECT_EVIDENCE",
    "KnowledgeToolDefinition",
    "RETRIEVE_KNOWLEDGE",
    "RetrievalResultEnvelope",
    "SEARCH_RAG",
    "SEARCH_SCHEMA",
    "SEARCH_WIKI",
    "SEARCH_WORKSPACE_RAG",
    "ToolHandler",
]
