"""Workspace-wide RAG fanout over the existing L3S1/L2S1 boundary."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer3.evidence import (
    EvidenceLocator,
    PaperProvenance,
    QueryProvenance,
    ResearchEvidence,
)
from transit_scholar.layer3.workspace.errors import WorkspaceChangedError
from transit_scholar.layer3.rerank import (
    CrossPaperRanker,
    LLMFineRerankDiagnostics,
    RerankDiagnostics,
)

from .models import ResearchQuery, RetrievalDiagnostic


class CrossPaperCandidate(BaseModel):
    """A normalized candidate whose local retrieval score is non-global."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    evidence: ResearchEvidence
    paper_id: str = Field(min_length=1)
    local_rank: int = Field(ge=1)


class WorkspaceRagResult(BaseModel):
    """Results of one revision-consistent cross-Paper RAG operation."""

    model_config = ConfigDict(extra="forbid")

    query: ResearchQuery
    workspace_revision: int = Field(ge=1)
    evidence_results: list[ResearchEvidence] = Field(default_factory=list)
    diagnostics: list[RetrievalDiagnostic] = Field(default_factory=list)
    searched_paper_ids: list[str] = Field(default_factory=list)
    skipped_paper_ids: list[str] = Field(default_factory=list)
    unavailable_paper_ids: list[str] = Field(default_factory=list)
    failed_paper_ids: list[str] = Field(default_factory=list)
    candidate_count: int = Field(default=0, ge=0)
    ranker_provider: str = Field(min_length=1)
    rerank_diagnostics: LLMFineRerankDiagnostics | RerankDiagnostics | None = None


class WorkspaceRagRetriever:
    """Resolve eligible Papers, reuse L2S1, then semantically rerank globally.

    Per-Paper score values are preserved only as local retrieval provenance.
    They are never used to order candidates across Papers.
    """

    def __init__(
        self,
        gateway: Any,
        ranker: CrossPaperRanker,
        *,
        per_paper_top_k: int = 8,
    ) -> None:
        if per_paper_top_k < 1:
            raise ValueError("per_paper_top_k must be positive")
        self.gateway = gateway
        self.ranker = ranker
        self.per_paper_top_k = per_paper_top_k

    def retrieve(
        self,
        query: ResearchQuery,
        *,
        source_query: str | None = None,
        action_id: str = "workspace-rag",
        top_k: int = 20,
    ) -> WorkspaceRagResult:
        """Retrieve one query across eligible Papers at one Workspace revision."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if query.workspace_id != self.gateway.workspace_id:
            raise ValueError("query belongs to a different workspace")

        state = self.gateway.current_state()
        expected_revision = state.revision
        paper_views = self.gateway.list_papers()
        self._require_revision(expected_revision)

        diagnostics: list[RetrievalDiagnostic] = []
        searched_paper_ids: list[str] = []
        skipped_paper_ids: list[str] = []
        unavailable_paper_ids: list[str] = []
        failed_paper_ids: list[str] = []
        candidates: list[CrossPaperCandidate] = []
        search_text = source_query or query.query_text

        for paper in paper_views:
            paper_id = paper.paper_id
            if not getattr(paper, "l2s1_ready", False):
                skipped_paper_ids.append(paper_id)
                diagnostics.append(
                    RetrievalDiagnostic(
                        action_id=action_id,
                        code="l2s1_unavailable",
                        message="Paper is not eligible for L2S1 retrieval",
                        status="skipped",
                        details={"paper_id": paper_id},
                    )
                )
                continue

            self._require_revision(expected_revision)
            searched_paper_ids.append(paper_id)
            try:
                result = self.gateway.search_evidence(
                    paper_id, search_text, top_k=self.per_paper_top_k
                )
            except WorkspaceChangedError:
                raise
            except Exception as error:
                failed_paper_ids.append(paper_id)
                diagnostics.append(
                    RetrievalDiagnostic(
                        action_id=action_id,
                        code="paper_retrieval_failed",
                        message="Paper retrieval failed",
                        status="failed",
                        details={"paper_id": paper_id, "error": str(error)},
                    )
                )
                self._require_revision(expected_revision)
                continue

            self._require_revision(expected_revision)
            if getattr(result, "status", "ok") != "ok":
                unavailable_paper_ids.append(paper_id)
                diagnostics.append(
                    RetrievalDiagnostic(
                        action_id=action_id,
                        code=getattr(result, "error_code", None) or "l2s1_unavailable",
                        message=getattr(result, "error_message", None)
                        or "Paper retrieval is unavailable",
                        status="degraded",
                        details={"paper_id": paper_id},
                    )
                )
                continue

            candidates.extend(
                self._normalize_hits(query, action_id, paper, result.hits)
            )

        self._require_revision(expected_revision)
        ranked_ids = self._rank(query, candidates, top_k)
        self._require_revision(expected_revision)
        rerank_diagnostics = getattr(self.ranker, "diagnostics", None)
        selected_providers = (
            list(rerank_diagnostics.selected_providers)
            if isinstance(rerank_diagnostics, RerankDiagnostics)
            else [self.ranker.provider_name]
        )
        evidence_by_id = {candidate.candidate_id: candidate.evidence for candidate in candidates}
        evidence_results = [
            evidence_by_id[candidate_id].model_copy(
                update={
                    "final_rank": rank,
                    "rerank_provenance": {
                        **evidence_by_id[candidate_id].rerank_provenance,
                        "provider": self.ranker.provider_name,
                        "candidate_id": candidate_id,
                        "stage": "cross_paper_semantic_rerank",
                        "selected_providers": selected_providers,
                    },
                }
            )
            for rank, candidate_id in enumerate(ranked_ids, start=1)
        ]
        return WorkspaceRagResult(
            query=query,
            workspace_revision=expected_revision,
            evidence_results=evidence_results,
            diagnostics=diagnostics,
            searched_paper_ids=searched_paper_ids,
            skipped_paper_ids=skipped_paper_ids,
            unavailable_paper_ids=unavailable_paper_ids,
            failed_paper_ids=failed_paper_ids,
            candidate_count=len(candidates),
            ranker_provider=self.ranker.provider_name,
            rerank_diagnostics=rerank_diagnostics,
        )

    def _require_revision(self, expected_revision: int) -> None:
        current_revision = self.gateway.current_state().revision
        if current_revision != expected_revision:
            raise WorkspaceChangedError(
                "workspace changed during composite RAG retrieval; results were discarded",
                expected_revision=expected_revision,
                current_revision=current_revision,
            )

    def _normalize_hits(
        self,
        query: ResearchQuery,
        action_id: str,
        paper: Any,
        hits: Sequence[Any],
    ) -> list[CrossPaperCandidate]:
        candidates: list[CrossPaperCandidate] = []
        identity_reader = getattr(self.gateway, "current_source_identity", None)
        source_version = identity_reader(paper.paper_id) if callable(identity_reader) else (
            getattr(paper, "canonical_source_version", None)
            or getattr(paper, "parse_run_id", None)
            or getattr(paper, "source_version", None)
        )
        if source_version is None:
            return candidates
        for hit in hits[: self.per_paper_top_k]:
            source_ref = hit.source_refs[0] if hit.source_refs else None
            block_id = source_ref.block_id if source_ref else hit.chunk_id
            local_rank = hit.rank
            candidate_id = f"{action_id}:{paper.paper_id}:{block_id or 'hit'}:{local_rank}"
            evidence = ResearchEvidence(
                evidence_id=candidate_id,
                locator=EvidenceLocator(
                    workspace_id=query.workspace_id,
                    source_kind="paper",
                    paper_id=paper.paper_id,
                    parse_run_id=source_version,
                    canonical_source_version=source_version,
                    block_id=block_id,
                    pages=hit.pages,
                    span=(
                        {"start": source_ref.char_start, "end": source_ref.char_end}
                        if source_ref
                        else None
                    ),
                ),
                text=hit.text,
                source_kind="rag",
                query_provenance=QueryProvenance(
                    query_id=query.query_id,
                    session_id=query.session_id,
                    query_text=query.query_text,
                ),
                paper_provenance=PaperProvenance(
                    paper_id=paper.paper_id, title=getattr(paper, "title", None),
                    parse_run_id=source_version,
                    canonical_source_version=source_version,
                ),
                section=" / ".join(hit.section_path) or None,
                retrieval_provenance={
                    "action_id": action_id,
                    "method": hit.retrieval_method,
                    "local_rank": local_rank,
                    "local_score": hit.score,
                },
            )
            candidates.append(
                CrossPaperCandidate(
                    candidate_id=candidate_id,
                    evidence=evidence,
                    paper_id=paper.paper_id,
                    local_rank=local_rank,
                )
            )
        return candidates

    def _rank(
        self, query: ResearchQuery, candidates: Sequence[CrossPaperCandidate], top_k: int
    ) -> list[str]:
        if not candidates:
            return []
        candidate_ids = {candidate.candidate_id for candidate in candidates}
        ranked_ids = list(self.ranker.rerank(query, candidates, top_k=min(top_k, len(candidates))))
        if len(ranked_ids) != len(set(ranked_ids)) or not set(ranked_ids).issubset(candidate_ids):
            raise ValueError("cross-Paper ranker returned unknown or duplicate candidate IDs")
        return ranked_ids[:top_k]


__all__ = [
    "CrossPaperCandidate",
    "CrossPaperRanker",
    "WorkspaceRagResult",
    "WorkspaceRagRetriever",
]
