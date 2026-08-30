"""Dedicated model reranker tests (REQ-016, REQ-017, REQ-025, REQ-026)."""

from __future__ import annotations

from types import SimpleNamespace

from transit_scholar.layer3.rerank import ModelThenFineRanker
from transit_scholar.layer3.retrieval import ResearchQuery


class RecordingRanker:
    def __init__(self, provider_name: str, ordered_ids: list[str]) -> None:
        self.provider_name = provider_name
        self.ordered_ids = ordered_ids
        self.calls: list[tuple[list[str], int]] = []

    def rerank(self, query, candidates, *, top_k):
        candidate_ids = [candidate.candidate_id for candidate in candidates]
        self.calls.append((candidate_ids, top_k))
        return [candidate_id for candidate_id in self.ordered_ids if candidate_id in candidate_ids]


class FailingRanker:
    provider_name = "unavailable-model"

    def rerank(self, query, candidates, *, top_k):
        raise RuntimeError("provider unavailable")


def _query() -> ResearchQuery:
    return ResearchQuery(
        query_id="query-1",
        session_id="session-1",
        workspace_id="workspace-1",
        query_text="Find evidence",
    )


def _candidates(count: int):
    return [SimpleNamespace(candidate_id=f"candidate-{index}") for index in range(count)]


def test_dedicated_model_provider_is_substitutable_before_fine_reranking():
    candidates = _candidates(4)
    first_model = RecordingRanker("model-a", ["candidate-3", "candidate-1"])
    second_model = RecordingRanker("model-b", ["candidate-2", "candidate-0"])
    first_fine = RecordingRanker("fine", ["candidate-1", "candidate-3"])
    second_fine = RecordingRanker("fine", ["candidate-0", "candidate-2"])

    first = ModelThenFineRanker(first_model, first_fine, model_top_k=2)
    second = ModelThenFineRanker(second_model, second_fine, model_top_k=2)

    assert first.rerank(_query(), candidates, top_k=2) == ["candidate-1", "candidate-3"]
    assert second.rerank(_query(), candidates, top_k=2) == ["candidate-0", "candidate-2"]
    assert first_fine.calls == [(["candidate-3", "candidate-1"], 2)]
    assert second_fine.calls == [(["candidate-2", "candidate-0"], 2)]


def test_model_reranker_reduces_pool_before_fine_reranking_with_diagnostics():
    model = RecordingRanker("dedicated-model", ["candidate-4", "candidate-3", "candidate-2"])
    fine = RecordingRanker("llm-fine", ["candidate-2", "candidate-3", "candidate-4"])
    ranker = ModelThenFineRanker(model, fine, model_top_k=3)

    ranked_ids = ranker.rerank(_query(), _candidates(5), top_k=2)

    assert ranked_ids == ["candidate-2", "candidate-3"]
    assert fine.calls == [(["candidate-4", "candidate-3", "candidate-2"], 2)]
    assert ranker.diagnostics is not None
    assert ranker.diagnostics.model_dump() == {
        "initial_candidate_count": 5,
        "model_reranker_input_count": 5,
        "model_reranker_output_count": 3,
        "configured_model_top_k": 3,
        "final_output_count": 2,
        "selected_providers": ["dedicated-model", "llm-fine"],
        "degradation_events": [],
        "status": "ok",
    }


def test_model_provider_failure_uses_explicitly_degraded_deterministic_fallback():
    fine = RecordingRanker("llm-fine", ["candidate-1", "candidate-0"])
    ranker = ModelThenFineRanker(FailingRanker(), fine, model_top_k=2)

    ranked_ids = ranker.rerank(_query(), _candidates(3), top_k=2)

    assert ranked_ids == ["candidate-1", "candidate-0"]
    assert fine.calls == [(["candidate-0", "candidate-1"], 2)]
    assert ranker.diagnostics is not None
    assert ranker.diagnostics.status == "degraded"
    assert ranker.diagnostics.degradation_events == ["model_reranker_failed:RuntimeError"]
