"""End-to-end Query-level retrieval integration for Layer3 Stage3."""

from __future__ import annotations

import importlib
import inspect
import re
from types import SimpleNamespace

from transit_scholar.layer3.planner import (
    HybridKnowledgeRetrievalPlanner,
    RetrievalCapabilities,
    RetrievalContext,
)
from transit_scholar.layer3.rerank import (
    LLMFineRerankConfig,
    LLMFineReranker,
    ModelThenFineRanker,
)
from transit_scholar.layer3.retrieval import (
    RagRetrievalAction,
    ResearchQuery,
    SchemaRetrievalAction,
    WikiRetrievalAction,
    WorkspaceRagRetriever,
)
from transit_scholar.layer3.tools import KnowledgeToolService


class RecordingPlanProvider:
    def __init__(self, actions: list[object]) -> None:
        self.actions = actions
        self.prompts: list[str] = []

    def plan(self, prompt: str) -> dict[str, object]:
        self.prompts.append(prompt)
        return {"query_id": "query-1", "actions": self.actions}


class ReducingModelRanker:
    provider_name = "dedicated-model"

    def __init__(self) -> None:
        self.input_counts: list[int] = []

    def rerank(self, query, candidates, *, top_k):
        self.input_counts.append(len(candidates))
        return [candidate.candidate_id for candidate in candidates[:top_k]]


class ReverseEvidenceRanker:
    provider_name = "llm-fine"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def rank_evidence(self, prompt: str) -> dict[str, list[str]]:
        self.prompts.append(prompt)
        candidate_ids = re.findall(r"candidate_id: '([^']+)'", prompt)
        return {"ranked_candidate_ids": list(reversed(candidate_ids))}


class EndToEndGateway:
    workspace_id = "workspace-1"

    def __init__(self) -> None:
        self.revision = 9
        self.schema_calls: list[str] = []
        self.wiki_calls: list[str] = []
        self.evidence_calls: list[tuple[str, str, int]] = []

    def current_state(self):
        return SimpleNamespace(revision=self.revision)

    def list_papers(self):
        return [
            SimpleNamespace(paper_id="paper-1", title="Paper One", l2s1_ready=True),
            SimpleNamespace(paper_id="paper-2", title="Paper Two", l2s1_ready=True),
        ]

    def get_schema_instance(self, paper_id: str):
        self.schema_calls.append(paper_id)
        field = SimpleNamespace(
            value="adaptive holding",
            status="explicit",
            confidence=0.91,
            notes=None,
            evidence=[],
        )
        return SimpleNamespace(fields={"method": field})

    def search_wiki(self, query: str, *, limit: int, mode: str):
        self.wiki_calls.append(query)
        return SimpleNamespace(
            status="ok",
            hits=[
                SimpleNamespace(
                    object_id="wiki-paper-2",
                    title="Paper Two",
                    type="page",
                    snippet="Navigation to the second paper",
                    retrieval_mode=mode,
                    score=0.7,
                )
            ],
        )

    def resolve_wiki_hit_paper_ids(self, hit) -> list[str]:
        return ["paper-2"] if hit.object_id == "wiki-paper-2" else []

    def search_evidence(self, paper_id: str, query: str, *, top_k: int):
        self.evidence_calls.append((paper_id, query, top_k))
        local_score = 0.99 if paper_id == "paper-1" else 0.01
        hits = [
            SimpleNamespace(
                source_refs=[
                    SimpleNamespace(
                        block_id=f"{paper_id}-block-{rank}",
                        char_start=rank,
                        char_end=rank + 8,
                    )
                ],
                chunk_id=f"{paper_id}-chunk-{rank}",
                pages=[rank],
                text=f"Evidence {rank} from {paper_id}",
                section_path=["Results"],
                retrieval_method="l2s1-hybrid",
                rank=rank,
                score=local_score,
            )
            for rank in range(1, min(top_k, 2) + 1)
        ]
        return SimpleNamespace(status="ok", hits=hits)


def _query() -> ResearchQuery:
    return ResearchQuery(
        query_id="query-1",
        session_id="session-1",
        workspace_id="workspace-1",
        query_text="Which adaptive holding evidence is most useful?",
    )


def _context(query: ResearchQuery) -> RetrievalContext:
    return RetrievalContext(
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
            eligible_paper_ids={"paper-1", "paper-2"},
            l2s1_ready_paper_ids={"paper-1", "paper-2"},
            schema_ready_paper_ids={"paper-1", "paper-2"},
            wiki_ready=True,
        ),
    )


def _service(actions: list[object]):
    gateway = EndToEndGateway()
    plan_provider = RecordingPlanProvider(actions)
    model_ranker = ReducingModelRanker()
    llm_provider = ReverseEvidenceRanker()
    hybrid_ranker = ModelThenFineRanker(
        model_ranker,
        LLMFineReranker(
            llm_provider,
            config=LLMFineRerankConfig(
                entry_candidates=50,
                group_size=10,
                max_rounds=3,
                final_top_k=2,
            ),
        ),
        model_top_k=3,
    )
    service = KnowledgeToolService(
        gateway,
        planner=HybridKnowledgeRetrievalPlanner(plan_provider),
        workspace_rag_retriever=WorkspaceRagRetriever(
            gateway, hybrid_ranker, per_paper_top_k=2
        ),
    )
    return service, gateway, plan_provider, model_ranker, llm_provider


def test_unified_query_runs_planner_workspace_fanout_and_hybrid_reranking():
    actions = [
        SchemaRetrievalAction(
            action_id="schema",
            source_query="method",
            field_ids=["method"],
            paper_ids=["paper-1"],
        ),
        WikiRetrievalAction(
            action_id="wiki",
            source_query="adaptive holding",
            discover_paper_ids=True,
        ),
        RagRetrievalAction(
            action_id="workspace-rag",
            source_query="adaptive holding outcomes",
            scope="workspace",
            limit=2,
        ),
    ]
    service, gateway, planner, model_ranker, llm_provider = _service(actions)
    query = _query()

    result = service.retrieve_knowledge(query, context=_context(query))

    assert len(planner.prompts) == 1
    assert [action.source_kind for action in result.strategy.actions] == [
        "schema",
        "wiki",
        "rag",
    ]
    assert result.schema_results[0].value == "adaptive holding"
    assert result.wiki_results[0].discovered_paper_ids == ["paper-2"]
    assert result.searched_paper_ids == ["paper-1", "paper-2"]
    assert result.workspace_revision == 9
    assert model_ranker.input_counts == [4]
    assert len(llm_provider.prompts) == 1

    diagnostics = result.rerank_diagnostics
    assert diagnostics is not None
    assert diagnostics.model_reranker_input_count == 4
    assert diagnostics.model_reranker_output_count == 3
    assert diagnostics.configured_entry_candidates == 50
    assert diagnostics.actual_llm_entry_count == 3
    assert diagnostics.effective_llm_entry_count == 3
    assert diagnostics.effective_final_top_k == 2
    assert diagnostics.selected_providers == ["dedicated-model", "llm-fine"]
    assert diagnostics.final_output_count == 2

    assert [evidence.final_rank for evidence in result.evidence_results] == [1, 2]
    first = result.evidence_results[0]
    assert first.paper_provenance.paper_id == "paper-2"
    assert first.retrieval_provenance["local_score"] == 0.01
    assert first.query_provenance.query_id == query.query_id
    assert first.locator.workspace_id == query.workspace_id
    assert first.rerank_provenance["selected_providers"] == [
        "dedicated-model",
        "llm-fine",
    ]
    assert not hasattr(first, "claim_status")
    assert not hasattr(first, "claim_confidence")


def test_expert_tools_bypass_planner_and_wiki_discovery_can_scope_rag():
    actions = [
        WikiRetrievalAction(
            action_id="discover",
            source_query="paper two",
            discover_paper_ids=True,
        ),
        RagRetrievalAction(
            action_id="paper-rag",
            source_query="holding outcomes",
            scope="papers",
            depends_on=["discover"],
            limit=1,
        ),
    ]
    service, gateway, planner, _, _ = _service(actions)
    query = _query()

    direct_schema = service.search_schema(
        query,
        SchemaRetrievalAction(
            action_id="direct-schema",
            source_query="method",
            field_ids=["method"],
            paper_ids=["paper-1"],
        ),
    )
    direct_wiki = service.search_wiki(
        query,
        WikiRetrievalAction(
            action_id="direct-wiki",
            source_query="paper two",
            discover_paper_ids=True,
        ),
    )
    direct_rag = service.search_workspace_rag(
        query,
        RagRetrievalAction(
            action_id="direct-workspace-rag",
            source_query="holding outcomes",
            scope="workspace",
            limit=1,
        ),
    )

    assert len(direct_schema.schema_results) == 1
    assert len(direct_wiki.wiki_results) == 1
    assert len(direct_rag.evidence_results) == 1
    assert service.inspect_evidence(direct_rag.evidence_results[0]).final_rank == 1
    assert planner.prompts == []

    gateway.evidence_calls.clear()
    planned = service.retrieve_knowledge(query, context=_context(query))

    assert len(planner.prompts) == 1
    assert [item.paper_provenance.paper_id for item in planned.evidence_results] == [
        "paper-2"
    ]
    assert [call[0] for call in gateway.evidence_calls] == ["paper-2"]


def test_l3s3_public_surface_has_no_session_planning_claim_or_agent_runtime_api():
    modules = [
        importlib.import_module("transit_scholar.layer3.retrieval"),
        importlib.import_module("transit_scholar.layer3.planner"),
        importlib.import_module("transit_scholar.layer3.tools"),
        importlib.import_module("transit_scholar.layer3.rerank"),
    ]
    exported = {
        name.lower()
        for module in modules
        for name in getattr(module, "__all__", ())
    }
    source = "\n".join(inspect.getsource(module) for module in modules).lower()

    assert "researchplan" not in exported
    assert not any("claim" in name for name in exported)
    assert not any("sessionquery" in name for name in exported)
    assert "langgraph" not in source
