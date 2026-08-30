"""Hybrid cross-Paper reranking tests (REQ-016..020, REQ-025..026)."""

from __future__ import annotations

import re
from types import SimpleNamespace

from transit_scholar.layer3.rerank import (
    LLMFineRerankConfig,
    LLMFineReranker,
    ModelThenFineRanker,
)
from transit_scholar.layer3.retrieval import ResearchQuery, WorkspaceRagRetriever


class RecordingRanker:
    def __init__(self, provider_name: str, ordered_ids: list[str]) -> None:
        self.provider_name = provider_name
        self.ordered_ids = ordered_ids
        self.calls: list[tuple[list[str], int]] = []

    def rerank(self, query, candidates, *, top_k):
        candidate_ids = [candidate.candidate_id for candidate in candidates]
        self.calls.append((candidate_ids, top_k))
        return [candidate_id for candidate_id in self.ordered_ids if candidate_id in candidate_ids]


class FailingModelRanker:
    provider_name = "unavailable-model"

    def rerank(self, query, candidates, *, top_k):
        raise RuntimeError("provider unavailable")


class EmptyModelRanker:
    provider_name = "empty-model"

    def rerank(self, query, candidates, *, top_k):
        return []


class EvidenceProvider:
    provider_name = "llm-fine"

    def __init__(self, *, malformed: bool = False) -> None:
        self.malformed = malformed

    def rank_evidence(self, prompt: str):
        if self.malformed:
            return {"ranked_candidate_ids": ["unknown"]}
        return {"ranked_candidate_ids": list(reversed(re.findall(r"candidate_id: '([^']+)'", prompt)))}


class IdentityModelRanker:
    provider_name = "dedicated-model"

    def rerank(self, query, candidates, *, top_k):
        return [candidate.candidate_id for candidate in candidates[:top_k]]


class FakeGateway:
    workspace_id = "workspace-1"

    def __init__(self) -> None:
        self.revision = 4

    def current_state(self):
        return SimpleNamespace(revision=self.revision)

    def list_papers(self):
        return [
            SimpleNamespace(paper_id="paper-1", title="One", l2s1_ready=True),
            SimpleNamespace(paper_id="paper-2", title="Two", l2s1_ready=True),
        ]

    def search_evidence(self, paper_id, query, *, top_k):
        return SimpleNamespace(
            status="ok",
            hits=[
                SimpleNamespace(
                    source_refs=[SimpleNamespace(block_id=f"{paper_id}-block", char_start=1, char_end=9)],
                    chunk_id=f"{paper_id}-chunk",
                    pages=[2], text=f"Evidence from {paper_id}", section_path=["Results"],
                    retrieval_method="l2s1-hybrid", rank=1, score=0.9,
                )
            ],
        )


def _query() -> ResearchQuery:
    return ResearchQuery(
        query_id="query-1", session_id="session-1", workspace_id="workspace-1", query_text="Find evidence"
    )


def _candidates(count: int):
    return [
        SimpleNamespace(
            candidate_id=f"candidate-{index}", paper_id=f"paper-{index % 2}", local_rank=index + 1,
            evidence=SimpleNamespace(text=f"Evidence {index}"),
        )
        for index in range(count)
    ]


def test_model_reduces_candidates_before_substitutable_fine_reranker():
    model = RecordingRanker("model-a", ["candidate-3", "candidate-1"])
    fine = RecordingRanker("fine-a", ["candidate-1", "candidate-3"])

    ranked = ModelThenFineRanker(model, fine, model_top_k=2).rerank(_query(), _candidates(4), top_k=2)

    assert ranked == ["candidate-1", "candidate-3"]
    assert fine.calls == [(["candidate-3", "candidate-1"], 2)]


def test_actual_model_output_and_empty_output_govern_fine_reranking():
    fine = RecordingRanker("fine", ["candidate-1", "candidate-0"])
    ranker = ModelThenFineRanker(RecordingRanker("model", ["candidate-1", "candidate-0"]), fine, model_top_k=3)

    assert ranker.rerank(_query(), _candidates(3), top_k=2) == ["candidate-1", "candidate-0"]
    assert ranker.diagnostics.model_reranker_output_count == 2
    empty = ModelThenFineRanker(EmptyModelRanker(), fine, model_top_k=3)
    assert empty.rerank(_query(), _candidates(3), top_k=2) == []
    assert fine.calls == [(["candidate-1", "candidate-0"], 2)]
    assert empty.diagnostics.final_output_count == 0


def test_fine_entry_and_target_use_actual_counts_below_equal_and_above_ceiling():
    config = LLMFineRerankConfig(entry_candidates=50, group_size=10, max_rounds=3, final_top_k=10)
    for count, expected_entry, expected_top_k in ((8, 8, 8), (50, 50, 10), (60, 50, 10)):
        reranker = LLMFineReranker(EvidenceProvider(), config=config)
        assert len(reranker.rerank(_query(), _candidates(count), top_k=10)) == expected_top_k
        assert reranker.diagnostics.effective_llm_entry_count == expected_entry
        assert reranker.diagnostics.effective_final_top_k == expected_top_k


def test_model_output_count_governs_fine_rerank_elimination_math():
    model = RecordingRanker("model", [f"candidate-{index}" for index in range(37)])
    fine = LLMFineReranker(
        EvidenceProvider(),
        config=LLMFineRerankConfig(entry_candidates=50, group_size=10, max_rounds=3, final_top_k=10),
    )
    ranker = ModelThenFineRanker(model, fine, model_top_k=50)

    assert len(ranker.rerank(_query(), _candidates(60), top_k=10)) == 10
    assert ranker.diagnostics.model_reranker_output_count == 37
    assert ranker.diagnostics.actual_llm_entry_count == 37
    assert ranker.diagnostics.effective_llm_entry_count == 37
    assert ranker.diagnostics.effective_final_top_k == 10
    assert sum(ranker.diagnostics.round_elimination_quotas) == 27


def test_model_and_llm_provider_failures_are_explicitly_degraded():
    model_fallback = ModelThenFineRanker(FailingModelRanker(), RecordingRanker("fine", ["candidate-1", "candidate-0"]), model_top_k=2)
    assert model_fallback.rerank(_query(), _candidates(3), top_k=2) == ["candidate-1", "candidate-0"]
    assert model_fallback.diagnostics.status == "degraded"
    assert model_fallback.diagnostics.degradation_events == ["model_reranker_failed:RuntimeError"]

    llm_fallback = LLMFineReranker(EvidenceProvider(malformed=True), config=LLMFineRerankConfig(entry_candidates=2, group_size=2, max_rounds=1, final_top_k=1))
    llm_fallback.rerank(_query(), _candidates(2), top_k=1)
    assert llm_fallback.diagnostics.status == "degraded"
    assert llm_fallback.diagnostics.degradation_events == ["llm_fine_reranker_final_failed:ValueError"]


def test_workspace_results_preserve_hybrid_provenance_and_diagnostics():
    ranker = ModelThenFineRanker(IdentityModelRanker(), LLMFineReranker(EvidenceProvider(), config=LLMFineRerankConfig(entry_candidates=2, group_size=2, final_top_k=2)), model_top_k=2)

    result = WorkspaceRagRetriever(FakeGateway(), ranker).retrieve(_query(), top_k=2)

    assert result.rerank_diagnostics is not None
    assert result.rerank_diagnostics.selected_providers == ["dedicated-model", "llm-fine"]
    evidence = result.evidence_results[0]
    assert evidence.query_provenance.query_id == "query-1"
    assert evidence.paper_provenance.paper_id in {"paper-1", "paper-2"}
    assert evidence.rerank_provenance["selected_providers"] == ["dedicated-model", "llm-fine"]
    assert evidence.final_rank == 1
