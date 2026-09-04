"""Workspace-wide RAG fanout tests (REQ-010..013)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from transit_scholar.layer3.retrieval import ResearchQuery, WorkspaceRagRetriever
from transit_scholar.layer3.workspace.errors import WorkspaceChangedError


class SemanticRanker:
    provider_name = "semantic-test"

    def __init__(self) -> None:
        self.calls = []

    def rerank(self, query, candidates, *, top_k):
        self.calls.append((query, list(candidates), top_k))
        return [candidate.candidate_id for candidate in reversed(candidates)][:top_k]


class FakeGateway:
    workspace_id = "workspace-1"

    def __init__(self, *, fail_paper: str | None = None, mutate_after: int | None = None):
        self.revision = 4
        self.fail_paper = fail_paper
        self.mutate_after = mutate_after
        self.searches = []

    def current_state(self):
        return SimpleNamespace(revision=self.revision)

    def current_source_identity(self, paper_id: str) -> str:
        return f"{paper_id}-parse-v1"

    def list_papers(self):
        return [
            SimpleNamespace(paper_id="paper-1", title="One", l2s1_ready=True),
            SimpleNamespace(paper_id="paper-2", title="Two", l2s1_ready=True),
            SimpleNamespace(paper_id="paper-3", title="Three", l2s1_ready=False),
        ]

    def search_evidence(self, paper_id, query, *, top_k):
        self.searches.append((paper_id, query, top_k))
        if paper_id == self.fail_paper:
            raise RuntimeError("L2S1 unavailable")
        if self.mutate_after == len(self.searches):
            self.revision += 1
        return SimpleNamespace(
            status="ok",
            hits=[
                SimpleNamespace(
                    source_refs=[SimpleNamespace(block_id=f"{paper_id}-block", char_start=1, char_end=9)],
                    chunk_id=f"{paper_id}-chunk",
                    pages=[2],
                    text=f"Evidence from {paper_id}",
                    section_path=["Results"],
                    retrieval_method="l2s1-hybrid",
                    rank=1,
                    score=0.99 if paper_id == "paper-1" else 0.01,
                )
            ],
        )


def _query():
    return ResearchQuery(
        query_id="query-1",
        session_id="session-1",
        workspace_id="workspace-1",
        query_text="Find evidence",
    )


def test_workspace_rag_fans_out_through_l2s1_and_semantically_reranks():
    gateway = FakeGateway()
    ranker = SemanticRanker()

    result = WorkspaceRagRetriever(gateway, ranker, per_paper_top_k=2).retrieve(
        _query(), top_k=2
    )

    assert [search[0] for search in gateway.searches] == ["paper-1", "paper-2"]
    assert gateway.searches[0][2] == 2
    assert result.searched_paper_ids == ["paper-1", "paper-2"]
    assert result.skipped_paper_ids == ["paper-3"]
    assert result.candidate_count == 2
    assert result.evidence_results[0].paper_provenance.paper_id == "paper-2"
    evidence = result.evidence_results[0]
    assert evidence.locator.parse_run_id == "paper-2-parse-v1"
    assert evidence.locator.canonical_source_version == "paper-2-parse-v1"
    assert evidence.paper_provenance.parse_run_id == "paper-2-parse-v1"
    assert evidence.paper_provenance.canonical_source_version == "paper-2-parse-v1"
    assert evidence.locator.parse_run_id == evidence.paper_provenance.parse_run_id
    assert evidence.locator.canonical_source_version == evidence.paper_provenance.canonical_source_version
    assert result.evidence_results[0].final_rank == 1
    assert result.evidence_results[0].rerank_provenance["provider"] == "semantic-test"
    assert len(ranker.calls) == 1


def test_workspace_rag_does_not_globally_sort_incomparable_local_scores():
    gateway = FakeGateway()

    result = WorkspaceRagRetriever(gateway, SemanticRanker()).retrieve(_query(), top_k=2)

    assert result.evidence_results[0].paper_provenance.paper_id == "paper-2"
    assert result.evidence_results[0].retrieval_provenance["local_score"] == 0.01


def test_workspace_rag_isolates_one_paper_failure():
    gateway = FakeGateway(fail_paper="paper-1")

    result = WorkspaceRagRetriever(gateway, SemanticRanker()).retrieve(_query())

    assert result.failed_paper_ids == ["paper-1"]
    assert [evidence.paper_provenance.paper_id for evidence in result.evidence_results] == ["paper-2"]
    assert result.diagnostics[0].code == "paper_retrieval_failed"


def test_workspace_revision_change_rejects_mixed_result():
    gateway = FakeGateway(mutate_after=1)

    with pytest.raises(WorkspaceChangedError, match="composite RAG retrieval"):
        WorkspaceRagRetriever(gateway, SemanticRanker()).retrieve(_query())
