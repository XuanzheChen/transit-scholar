"""Provider-neutral, multi-round LLM evidence fine reranking."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import Field

from transit_scholar.layer3.prompts import build_evidence_rerank_prompt

from .model import RerankDiagnostics
from .scheduler import LLMFineRerankConfig, allocate_group_quotas, build_elimination_schedule, regroup_candidates

if TYPE_CHECKING:
    from transit_scholar.layer3.retrieval.models import ResearchQuery
    from transit_scholar.layer3.retrieval.workspace_rag import CrossPaperCandidate


class EvidenceRankingProvider(Protocol):
    """Provider boundary for a single evidence-only listwise comparison."""

    provider_name: str

    def rank_evidence(self, prompt: str) -> Sequence[str] | Mapping[str, Any] | str: ...


class LLMFineRerankDiagnostics(RerankDiagnostics):
    """Auditable facts from LLM grouping, elimination, and degradation."""

    configured_entry_candidates: int = Field(ge=1)
    actual_llm_entry_count: int = Field(ge=0)
    effective_llm_entry_count: int = Field(ge=0)
    configured_final_top_k: int = Field(ge=1)
    effective_final_top_k: int = Field(ge=0)
    group_sizes: list[list[int]] = Field(default_factory=list)
    round_elimination_quotas: list[int] = Field(default_factory=list)
    per_group_quotas: list[list[int]] = Field(default_factory=list)
    survivor_counts: list[int] = Field(default_factory=list)
    selected_providers: list[str] = Field(default_factory=list)
    degradation_events: list[str] = Field(default_factory=list)
    final_output_count: int = Field(ge=0)
    final_listwise_comparison_performed: bool = False
    status: str = "ok"


class LLMFineReranker:
    """Eliminate evidence through reproducible groups and LLM listwise ordering."""

    def __init__(
        self,
        provider: EvidenceRankingProvider,
        *,
        config: LLMFineRerankConfig | None = None,
        final_comparison_capacity: int | None = None,
    ) -> None:
        if final_comparison_capacity is not None and final_comparison_capacity < 1:
            raise ValueError("final_comparison_capacity must be positive")
        self.provider = provider
        self.config = config or LLMFineRerankConfig()
        self.final_comparison_capacity = final_comparison_capacity or self.config.group_size
        self.provider_name = provider.provider_name
        self.diagnostics: LLMFineRerankDiagnostics | None = None

    def rerank(self, query: "ResearchQuery", candidates: Sequence["CrossPaperCandidate"], *, top_k: int) -> Sequence[str]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        candidate_list = list(candidates)
        active_config = replace(self.config, final_top_k=min(top_k, self.config.final_top_k))
        schedule = build_elimination_schedule(len(candidate_list), active_config)
        survivors = candidate_list[: schedule.effective_entry_count]
        group_sizes: list[list[int]] = []
        round_quotas: list[int] = []
        per_group_quotas: list[list[int]] = []
        survivor_counts = [len(survivors)]
        events: list[str] = []
        final_listwise = False

        for round_ in schedule.rounds:
            if len(survivors) <= self.final_comparison_capacity:
                survivors = self._rank_group(query, survivors, events, "final")
                final_listwise = True
                break
            groups = regroup_candidates(survivors, group_size=self.config.group_size, seed=self.config.seed + round_.round_number)
            sizes = [len(group) for group in groups]
            quotas = allocate_group_quotas(sizes, round_.elimination_quota)
            next_survivors: list[CrossPaperCandidate] = []
            for group, quota in zip(groups, quotas):
                ranked_group = self._rank_group(query, list(group), events, f"round_{round_.round_number}")
                next_survivors.extend(ranked_group[: len(ranked_group) - quota])
            survivors = next_survivors
            group_sizes.append(sizes)
            round_quotas.append(round_.elimination_quota)
            per_group_quotas.append(list(quotas))
            survivor_counts.append(len(survivors))

        if not final_listwise and len(survivors) <= self.final_comparison_capacity and len(survivors) > 1:
            survivors = self._rank_group(query, survivors, events, "final")
            final_listwise = True
        result = [candidate.candidate_id for candidate in survivors[: schedule.effective_final_top_k]]
        self.diagnostics = LLMFineRerankDiagnostics(
            initial_candidate_count=len(candidate_list), configured_entry_candidates=self.config.entry_candidates,
            actual_llm_entry_count=len(candidate_list), effective_llm_entry_count=schedule.effective_entry_count,
            configured_final_top_k=self.config.final_top_k, effective_final_top_k=schedule.effective_final_top_k,
            group_sizes=group_sizes, round_elimination_quotas=round_quotas, per_group_quotas=per_group_quotas,
            survivor_counts=survivor_counts, selected_providers=[self.provider_name], degradation_events=events,
            final_output_count=len(result), final_listwise_comparison_performed=final_listwise,
            status="degraded" if events else "ok",
        )
        return result

    def _rank_group(self, query: "ResearchQuery", candidates: Sequence["CrossPaperCandidate"], events: list[str], stage: str) -> list["CrossPaperCandidate"]:
        try:
            ranked_ids = self._parse_ranked_ids(self.provider.rank_evidence(build_evidence_rerank_prompt(query, candidates)))
            expected_ids = [candidate.candidate_id for candidate in candidates]
            if len(ranked_ids) != len(expected_ids) or set(ranked_ids) != set(expected_ids):
                raise ValueError("provider returned missing, unknown, or duplicate candidate IDs")
            by_id = {candidate.candidate_id: candidate for candidate in candidates}
            return [by_id[candidate_id] for candidate_id in ranked_ids]
        except Exception as error:
            events.append(f"llm_fine_reranker_{stage}_failed:{type(error).__name__}")
            return list(candidates)

    @staticmethod
    def _parse_ranked_ids(response: Sequence[str] | Mapping[str, Any] | str) -> list[str]:
        if isinstance(response, str):
            response = json.loads(response)
        if isinstance(response, Mapping):
            response = response.get("ranked_candidate_ids")
        if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
            raise ValueError("provider response must contain ranked_candidate_ids")
        if not all(isinstance(candidate_id, str) for candidate_id in response):
            raise ValueError("ranked candidate IDs must be strings")
        result = list(response)
        if len(result) != len(set(result)):
            raise ValueError("provider returned duplicate candidate IDs")
        return result


__all__ = ["EvidenceRankingProvider", "LLMFineRerankDiagnostics", "LLMFineReranker"]
