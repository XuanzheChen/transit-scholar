"""L3S3 v3 post-CR freeze-gate regressions (AC-031..AC-036)."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from transit_scholar.layer2.schema_extraction.models import FieldResult
from transit_scholar.layer3.evidence import EvidenceLocator, ResearchEvidence
from transit_scholar.layer3.planner import (
    HybridKnowledgeRetrievalPlanner,
    RetrievalCapabilities,
    RetrievalContext,
)
from transit_scholar.layer3.rerank import (
    LLMFineRerankConfig,
    LLMFineRerankDiagnostics,
    LLMFineReranker,
)
from transit_scholar.layer3.retrieval import (
    RagRetrievalAction,
    ResearchQuery,
    SchemaRetrievalAction,
    WikiRetrievalAction,
    WorkspaceRagResult,
)
from transit_scholar.layer3.tools import KnowledgeToolService, RetrievalResultEnvelope
from transit_scholar.layer3.workspace.errors import WorkspaceChangedError


def _query() -> ResearchQuery:
    return ResearchQuery(
        query_id="query-1",
        session_id="session-1",
        workspace_id="workspace-1",
        query_text="Find useful evidence",
    )


class StaticPlannerProvider:
    def __init__(self, actions):
        self.actions = actions

    def plan(self, prompt):
        return {"query_id": "query-1", "actions": self.actions}


class RevisionGateway:
    workspace_id = "workspace-1"

    def __init__(self):
        self.revision = 5
        self.schema_reads = 0

    def current_state(self):
        return SimpleNamespace(revision=self.revision)

    def list_papers(self):
        return [
            SimpleNamespace(
                paper_id="paper-1", l2s1_ready=True, schema_status="ready"
            )
        ]

    def get_schema_instance(self, paper_id):
        self.schema_reads += 1
        if self.schema_reads == 2:  # capability probe is first; execution is second
            self.revision += 1
        return SimpleNamespace(
            fields={"method": FieldResult(value="holding", status="explicit")}
        )

    def wiki_status(self):
        return SimpleNamespace(status="ready")

    def search_wiki(self, query, *, limit, mode):
        return SimpleNamespace(status="ok", hits=[])


def test_unified_retrieval_discards_results_when_revision_changes_between_actions():
    gateway = RevisionGateway()
    planner = HybridKnowledgeRetrievalPlanner(
        StaticPlannerProvider(
            [
                SchemaRetrievalAction(
                    action_id="schema",
                    source_query="method",
                    field_ids=["method"],
                    paper_ids=["paper-1"],
                ),
                WikiRetrievalAction(action_id="wiki", source_query="holding"),
            ]
        )
    )

    with pytest.raises(WorkspaceChangedError, match="unified retrieval"):
        KnowledgeToolService(gateway, planner=planner).retrieve_knowledge(_query())


def test_workspace_rag_without_semantic_ranker_fails_instead_of_concatenating():
    gateway = RevisionGateway()
    service = KnowledgeToolService(gateway)

    with pytest.raises(RuntimeError, match="semantic WorkspaceRagRetriever"):
        service.search_workspace_rag(
            _query(),
            RagRetrievalAction(action_id="workspace-rag", source_query="evidence"),
        )


class IdentityEvidenceRanker:
    provider_name = "identity-llm"

    def rank_evidence(self, prompt):
        return {
            "ranked_candidate_ids": re.findall(
                r"candidate_id: '([^']+)'", prompt
            )
        }


def _candidates(count):
    return [
        SimpleNamespace(
            candidate_id=f"candidate-{index}",
            paper_id=f"paper-{index % 4}",
            local_rank=index + 1,
            evidence=SimpleNamespace(text=f"evidence {index}"),
        )
        for index in range(count)
    ]


def test_final_listwise_comparison_records_complete_actual_candidate_budget():
    reranker = LLMFineReranker(
        IdentityEvidenceRanker(),
        config=LLMFineRerankConfig(
            entry_candidates=50, group_size=10, max_rounds=3, final_top_k=10
        ),
        final_comparison_capacity=20,
    )

    assert len(reranker.rerank(_query(), _candidates(37), top_k=10)) == 10
    diagnostics = reranker.diagnostics
    assert diagnostics is not None
    assert diagnostics.round_elimination_quotas == [9, 9, 9]
    assert diagnostics.group_sizes[-1] == [19]
    assert diagnostics.per_group_quotas[-1] == [9]
    assert diagnostics.survivor_counts[-1] == 10
    assert sum(diagnostics.round_elimination_quotas) == 27


class UnreadyGateway:
    workspace_id = "workspace-1"

    def __init__(self):
        self.search_calls = 0

    def current_state(self):
        return SimpleNamespace(revision=2)

    def list_papers(self):
        return [
            SimpleNamespace(
                paper_id="paper-1", l2s1_ready=False, schema_status="missing"
            )
        ]

    def wiki_status(self):
        return SimpleNamespace(status="stale")


def test_false_caller_capabilities_cannot_bypass_authoritative_workspace_state():
    query = _query()
    claimed = RetrievalContext(
        query=query,
        capabilities=RetrievalCapabilities(
            available_sources={"schema", "wiki", "rag"},
            available_tools={
                "search_schema",
                "search_wiki",
                "search_rag",
                "search_workspace_rag",
            },
            schema_field_ids={"method"},
            eligible_paper_ids={"paper-1", "invented-paper"},
            l2s1_ready_paper_ids={"paper-1", "invented-paper"},
            schema_ready_paper_ids={"paper-1"},
            wiki_ready=True,
        ),
    )
    planner = HybridKnowledgeRetrievalPlanner(
        StaticPlannerProvider(
            [RagRetrievalAction(action_id="rag", source_query="evidence")]
        )
    )
    result = KnowledgeToolService(
        UnreadyGateway(),
        planner=planner,
        workspace_rag_retriever=SimpleNamespace(),
    ).retrieve_knowledge(query, context=claimed)

    assert result.strategy is None
    assert result.diagnostics[0].status == "failed"
    assert "source_unavailable" in result.diagnostics[0].message


def test_inspect_evidence_revalidates_current_paper_membership():
    evidence = ResearchEvidence(
        evidence_id="evidence-1",
        locator=EvidenceLocator(
            workspace_id="workspace-1", source_kind="paper", paper_id="paper-1"
        ),
        text="Previously visible evidence",
        source_kind="rag",
    )

    gateway = UnreadyGateway()
    gateway.list_papers = lambda: []
    with pytest.raises(ValueError, match="not members of the current workspace"):
        KnowledgeToolService(gateway).inspect_evidence(evidence)


def _fine_diagnostics() -> LLMFineRerankDiagnostics:
    return LLMFineRerankDiagnostics(
        initial_candidate_count=37,
        model_reranker_input_count=37,
        model_reranker_output_count=37,
        configured_model_top_k=50,
        configured_entry_candidates=50,
        actual_llm_entry_count=37,
        effective_llm_entry_count=37,
        configured_final_top_k=10,
        effective_final_top_k=10,
        group_sizes=[[10, 10, 10, 7], [19]],
        round_elimination_quotas=[9, 9, 9],
        per_group_quotas=[[3, 2, 2, 2], [9]],
        survivor_counts=[37, 28, 19, 10],
        selected_providers=["model", "llm"],
        degradation_events=[],
        final_output_count=10,
        final_listwise_comparison_performed=True,
    )


def test_reranker_diagnostics_round_trip_losslessly_at_both_result_boundaries():
    diagnostics = _fine_diagnostics()
    envelope = RetrievalResultEnvelope(query=_query(), rerank_diagnostics=diagnostics)
    workspace_result = WorkspaceRagResult(
        query=_query(),
        workspace_revision=3,
        ranker_provider="hybrid",
        rerank_diagnostics=diagnostics,
    )

    restored_envelope = RetrievalResultEnvelope.model_validate_json(
        envelope.model_dump_json()
    )
    restored_workspace = WorkspaceRagResult.model_validate_json(
        workspace_result.model_dump_json()
    )
    for restored in (
        restored_envelope.rerank_diagnostics,
        restored_workspace.rerank_diagnostics,
    ):
        assert isinstance(restored, LLMFineRerankDiagnostics)
        assert restored.model_dump() == diagnostics.model_dump()
