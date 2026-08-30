"""LLM evidence fine-reranker tests (REQ-018, REQ-019, REQ-023..026)."""

from __future__ import annotations

import re
from types import SimpleNamespace

from transit_scholar.layer3.prompts import build_evidence_rerank_prompt
from transit_scholar.layer3.rerank import (
    LLMFineRerankConfig,
    LLMFineRerankDiagnostics,
    LLMFineReranker,
    ModelThenFineRanker,
    RerankDiagnostics,
)
from transit_scholar.layer3.retrieval import ResearchQuery


class FakeEvidenceRanker:
    provider_name = "fake-llm"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def rank_evidence(self, prompt: str):
        self.prompts.append(prompt)
        return {"ranked_candidate_ids": list(reversed(re.findall(r"candidate_id: '([^']+)'", prompt)))}


class MalformedEvidenceRanker(FakeEvidenceRanker):
    provider_name = "malformed-llm"

    def rank_evidence(self, prompt: str):
        self.prompts.append(prompt)
        return {"ranked_candidate_ids": ["not-a-candidate"]}


class IdentityModelRanker:
    provider_name = "identity-model"

    def rerank(self, query, candidates, *, top_k):
        return [candidate.candidate_id for candidate in candidates[:top_k]]


def _query() -> ResearchQuery:
    return ResearchQuery(
        query_id="query-1", session_id="session-1", workspace_id="workspace-1", query_text="Find control evidence"
    )


def _candidates(count: int):
    return [
        SimpleNamespace(
            candidate_id=f"candidate-{index}", paper_id=f"paper-{index % 3}", local_rank=index + 1,
            evidence=SimpleNamespace(text=f"evidence text {index}"),
        )
        for index in range(count)
    ]


def test_multiround_reranking_regroups_survivors_and_runs_final_listwise_comparison():
    provider = FakeEvidenceRanker()
    reranker = LLMFineReranker(
        provider,
        config=LLMFineRerankConfig(entry_candidates=6, group_size=3, max_rounds=2, final_top_k=2, seed=9),
        final_comparison_capacity=4,
    )

    result = reranker.rerank(_query(), _candidates(8), top_k=2)

    assert len(result) == 2
    assert reranker.diagnostics is not None
    assert reranker.diagnostics.actual_llm_entry_count == 8
    assert reranker.diagnostics.effective_llm_entry_count == 6
    assert reranker.diagnostics.effective_final_top_k == 2
    assert reranker.diagnostics.group_sizes == [[3, 3]]
    assert reranker.diagnostics.round_elimination_quotas == [2]
    assert reranker.diagnostics.per_group_quotas == [[1, 1]]
    assert reranker.diagnostics.survivor_counts == [6, 4]
    assert reranker.diagnostics.final_listwise_comparison_performed is True
    assert len(provider.prompts) == 3


def test_malformed_llm_output_uses_explicitly_degraded_stable_fallback():
    reranker = LLMFineReranker(
        MalformedEvidenceRanker(),
        config=LLMFineRerankConfig(entry_candidates=4, group_size=4, max_rounds=1, final_top_k=2),
    )

    assert reranker.rerank(_query(), _candidates(4), top_k=2) == ["candidate-0", "candidate-1"]
    assert reranker.diagnostics is not None
    assert reranker.diagnostics.status == "degraded"
    assert reranker.diagnostics.degradation_events == ["llm_fine_reranker_final_failed:ValueError"]


def test_evidence_ranking_prompt_does_not_request_an_answer_or_claims():
    prompt = build_evidence_rerank_prompt(_query(), _candidates(2))

    assert "do not answer the research question" in prompt
    assert "do not generate or request Claims" in prompt
    assert "directness, evidentiary value, and specificity" in prompt
    assert "candidate-0" in prompt


def test_llm_diagnostics_are_compatible_with_base_rerank_diagnostics():
    provider = FakeEvidenceRanker()
    reranker = LLMFineReranker(provider)

    reranker.rerank(_query(), _candidates(2), top_k=1)

    assert isinstance(reranker.diagnostics, LLMFineRerankDiagnostics)
    assert isinstance(reranker.diagnostics, RerankDiagnostics)


def test_model_wrapper_preserves_compatible_llm_diagnostics():
    ranker = ModelThenFineRanker(
        IdentityModelRanker(),
        LLMFineReranker(FakeEvidenceRanker()),
        model_top_k=3,
    )

    ranker.rerank(_query(), _candidates(4), top_k=2)

    assert isinstance(ranker.diagnostics, LLMFineRerankDiagnostics)
    assert ranker.diagnostics.model_reranker_input_count == 4
    assert ranker.diagnostics.model_reranker_output_count == 3
    assert ranker.diagnostics.configured_model_top_k == 3
