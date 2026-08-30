"""Composable semantic rerankers for a normalized cross-Paper candidate pool."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from transit_scholar.layer3.retrieval.models import ResearchQuery
    from transit_scholar.layer3.retrieval.workspace_rag import CrossPaperCandidate


class CrossPaperRanker(Protocol):
    """Provider-neutral ranking boundary that returns stable candidate IDs."""

    provider_name: str

    def rerank(
        self,
        query: ResearchQuery,
        candidates: Sequence[CrossPaperCandidate],
        *,
        top_k: int,
    ) -> Sequence[str]: ...


class DedicatedModelReranker(CrossPaperRanker, Protocol):
    """Dedicated semantic model used to reduce the candidate pool."""


class RerankDiagnostics(BaseModel):
    """Execution facts for model reduction and optional fine reranking."""

    model_config = ConfigDict(extra="forbid")

    initial_candidate_count: int = Field(ge=0)
    model_reranker_input_count: int = Field(default=0, ge=0)
    model_reranker_output_count: int = Field(default=0, ge=0)
    configured_model_top_k: int = Field(default=1, ge=1)
    final_output_count: int = Field(ge=0)
    selected_providers: list[str] = Field(default_factory=list)
    degradation_events: list[str] = Field(default_factory=list)
    status: str = "ok"


class ModelThenFineRanker:
    """Run a dedicated model before an injectable semantic fine ranker.

    The model provider only chooses a bounded protected set.  The fine ranker
    receives that set, never the unbounded cross-Paper pool.  On provider
    failure, stable collection order is retained as an explicitly degraded
    fallback; raw per-Paper scores are not used.
    """

    provider_name = "model_then_fine"

    def __init__(
        self,
        model_reranker: DedicatedModelReranker,
        fine_reranker: CrossPaperRanker,
        *,
        model_top_k: int = 50,
    ) -> None:
        if model_top_k < 1:
            raise ValueError("model_top_k must be positive")
        self.model_reranker = model_reranker
        self.fine_reranker = fine_reranker
        self.model_top_k = model_top_k
        self.diagnostics: RerankDiagnostics | None = None

    def rerank(
        self,
        query: ResearchQuery,
        candidates: Sequence[CrossPaperCandidate],
        *,
        top_k: int,
    ) -> Sequence[str]:
        if top_k < 1:
            raise ValueError("top_k must be positive")

        candidate_list = list(candidates)
        model_limit = min(self.model_top_k, len(candidate_list))
        selected_providers = [
            self.model_reranker.provider_name,
            self.fine_reranker.provider_name,
        ]
        events: list[str] = []
        model_ids: list[str]
        try:
            model_ids = self._validated_ids(
                self.model_reranker.rerank(query, candidate_list, top_k=model_limit),
                candidate_list,
                limit=model_limit,
                stage="model reranker",
            )
        except Exception as error:
            model_ids = [candidate.candidate_id for candidate in candidate_list[:model_limit]]
            events.append(f"model_reranker_failed:{type(error).__name__}")

        candidates_by_id = {candidate.candidate_id: candidate for candidate in candidate_list}
        reduced_candidates = [candidates_by_id[candidate_id] for candidate_id in model_ids]
        final_limit = min(top_k, len(reduced_candidates))
        if final_limit == 0:
            final_ids = []
        else:
            try:
                final_ids = self._validated_ids(
                    self.fine_reranker.rerank(query, reduced_candidates, top_k=final_limit),
                    reduced_candidates,
                    limit=final_limit,
                    stage="fine reranker",
                )
            except Exception as error:
                final_ids = model_ids[:final_limit]
                events.append(f"fine_reranker_failed:{type(error).__name__}")

        fine_diagnostics = getattr(self.fine_reranker, "diagnostics", None)
        if isinstance(fine_diagnostics, RerankDiagnostics):
            self.diagnostics = fine_diagnostics.model_copy(
                update={
                    "initial_candidate_count": len(candidate_list),
                    "model_reranker_input_count": len(candidate_list),
                    "model_reranker_output_count": len(reduced_candidates),
                    "configured_model_top_k": self.model_top_k,
                    "selected_providers": selected_providers,
                    "degradation_events": events + fine_diagnostics.degradation_events,
                    "status": "degraded" if events or fine_diagnostics.degradation_events else "ok",
                }
            )
            return final_ids
        self.diagnostics = RerankDiagnostics(
            initial_candidate_count=len(candidate_list),
            model_reranker_input_count=len(candidate_list),
            model_reranker_output_count=len(reduced_candidates),
            configured_model_top_k=self.model_top_k,
            final_output_count=len(final_ids),
            selected_providers=selected_providers,
            degradation_events=events,
            status="degraded" if events else "ok",
        )
        return final_ids

    @staticmethod
    def _validated_ids(
        ranked_ids: Sequence[str],
        candidates: Sequence[CrossPaperCandidate],
        *,
        limit: int,
        stage: str,
    ) -> list[str]:
        candidate_ids = {candidate.candidate_id for candidate in candidates}
        result = list(ranked_ids)
        if len(result) != len(set(result)) or not set(result).issubset(candidate_ids):
            raise ValueError(f"{stage} returned unknown or duplicate candidate IDs")
        return result[:limit]
