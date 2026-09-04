"""Direct and unified Layer3 knowledge tool surface tests."""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace

import pytest

from transit_scholar.layer2.schema_extraction.models import FieldResult
from transit_scholar.layer3.planner import RetrievalCapabilities, RetrievalContext
from transit_scholar.layer3.retrieval import (
    RagRetrievalAction,
    ResearchQuery,
    SchemaRetrievalAction,
    WikiRetrievalAction,
    WorkspaceRagRetriever,
)
from transit_scholar.layer3.tools import KnowledgeToolService


class RecordingPlanner:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0

    def plan(self, prompt: str) -> object:
        self.calls += 1
        return self.response


class FakeGateway:
    workspace_id = "workspace-1"

    def __init__(self) -> None:
        self.state_calls = 0
        self.schema_calls = 0
        self.wiki_calls = 0
        self.rag_calls = 0

    def current_state(self):
        self.state_calls += 1
        return SimpleNamespace(revision=1)

    def current_source_identity(self, paper_id: str) -> str:
        return f"{paper_id}-parse-v1"

    def list_papers(self):
        return [
            SimpleNamespace(
                paper_id="paper-1", l2s1_ready=True, schema_status="ready"
            ),
            SimpleNamespace(
                paper_id="paper-2", l2s1_ready=True, schema_status="ready"
            ),
        ]

    def get_schema_instance(self, paper_id: str):
        self.schema_calls += 1
        return SimpleNamespace(
            fields={"method": FieldResult(value="signal priority", status="explicit")}
        )

    def search_wiki(self, query: str, *, limit: int, mode: str):
        self.wiki_calls += 1
        return SimpleNamespace(
            status="ok",
            hits=[
                SimpleNamespace(
                    object_id="page-1",
                    title="Signal priority",
                    type="page",
                    snippet="A navigation result",
                    retrieval_mode=mode,
                    score=0.8,
                )
            ],
        )

    def search_evidence(self, paper_id: str, query: str, *, top_k: int):
        self.rag_calls += 1
        return SimpleNamespace(
            status="ok",
            hits=[
                SimpleNamespace(
                    source_refs=[SimpleNamespace(block_id="block-1", char_start=0, char_end=8)],
                    chunk_id="chunk-1",
                    pages=[3],
                    text=f"Evidence from {paper_id}",
                    section_path=["Results"],
                    retrieval_method="bm25",
                    rank=1,
                    score=0.5,
                )
            ],
        )


def _query() -> ResearchQuery:
    return ResearchQuery(
        query_id="query-1",
        session_id="session-1",
        workspace_id="workspace-1",
        query_text="Find signal priority evidence",
    )


def test_expert_tools_call_gateway_directly_without_planning():
    gateway = FakeGateway()
    planner = RecordingPlanner({})
    tools = KnowledgeToolService(gateway, planner=planner)
    query = _query()

    schema = tools.search_schema(
        query, SchemaRetrievalAction(action_id="schema", source_query="method")
    )
    wiki = tools.search_wiki(
        query, WikiRetrievalAction(action_id="wiki", source_query="priority")
    )
    paper_rag = tools.search_rag(
        query,
        RagRetrievalAction(
            action_id="paper-rag",
            source_query="priority",
            scope="papers",
            paper_ids=["paper-1"],
        ),
    )
    with pytest.raises(RuntimeError, match="semantic WorkspaceRagRetriever"):
        tools.search_workspace_rag(
            query, RagRetrievalAction(action_id="workspace-rag", source_query="priority")
        )

    assert schema.schema_results[0].value == "signal priority"
    assert wiki.wiki_results[0].node_id == "page-1"
    assert paper_rag.evidence_results[0].locator.paper_id == "paper-1"
    assert tools.inspect_evidence(paper_rag.evidence_results[0]) == paper_rag.evidence_results[0]
    assert planner.calls == 0
    assert gateway.schema_calls == 2
    assert gateway.wiki_calls == 1
    assert gateway.rag_calls == 1


def test_direct_workspace_rag_uses_injected_retriever_and_stamps_identity():
    gateway = FakeGateway()

    class Ranker:
        provider_name = "semantic-test"

        def rerank(self, query, candidates, *, top_k):
            return [candidate.candidate_id for candidate in candidates[:top_k]]

    service = KnowledgeToolService(
        gateway, workspace_rag_retriever=WorkspaceRagRetriever(gateway, Ranker())
    )
    evidence = service.search_workspace_rag(
        _query(), RagRetrievalAction(action_id="workspace-rag", source_query="priority")
    ).evidence_results[0]
    assert evidence.locator.parse_run_id == "paper-1-parse-v1"
    assert evidence.locator.canonical_source_version == "paper-1-parse-v1"
    assert evidence.paper_provenance.parse_run_id == "paper-1-parse-v1"
    assert evidence.paper_provenance.canonical_source_version == "paper-1-parse-v1"
    assert evidence.locator.parse_run_id == evidence.paper_provenance.parse_run_id
    assert evidence.locator.canonical_source_version == evidence.paper_provenance.canonical_source_version


@pytest.mark.parametrize(
    ("operation", "action"),
    [
        (
            "schema",
            SchemaRetrievalAction(
                action_id="schema-outside",
                source_query="method",
                paper_ids=["outside-paper"],
            ),
        ),
        (
            "rag",
            RagRetrievalAction(
                action_id="rag-outside",
                source_query="priority",
                scope="papers",
                paper_ids=["outside-paper"],
            ),
        ),
    ],
)
def test_expert_paper_operations_reject_non_workspace_papers_before_source_reads(
    operation: str, action: SchemaRetrievalAction | RagRetrievalAction
):
    gateway = FakeGateway()
    tools = KnowledgeToolService(gateway)

    with pytest.raises(ValueError, match="not members of the current workspace"):
        getattr(tools, f"search_{operation}")(_query(), action)

    assert gateway.schema_calls == 0
    assert gateway.rag_calls == 0


def test_unified_tool_uses_planner_and_preserves_source_semantics():
    gateway = FakeGateway()
    actions = [
        SchemaRetrievalAction(action_id="schema", source_query="method", paper_ids=["paper-1"]),
        WikiRetrievalAction(action_id="wiki", source_query="priority"),
        RagRetrievalAction(
            action_id="rag",
            source_query="evidence",
            scope="papers",
            paper_ids=["paper-1"],
        ),
    ]
    planner = RecordingPlanner({"query_id": "query-1", "actions": actions})
    query = _query()
    context = RetrievalContext(
        query=query,
        capabilities=RetrievalCapabilities(
            available_sources={"schema", "wiki", "rag"},
            available_tools={"schema", "wiki", "rag"},
            eligible_paper_ids={"paper-1", "paper-2"},
            schema_ready_paper_ids={"paper-1", "paper-2"},
            l2s1_ready_paper_ids={"paper-1", "paper-2"},
            wiki_ready=True,
        ),
    )
    from transit_scholar.layer3.planner import HybridKnowledgeRetrievalPlanner

    result = KnowledgeToolService(
        gateway, planner=HybridKnowledgeRetrievalPlanner(planner)
    ).retrieve_knowledge(query, context=context)

    assert planner.calls == 1
    assert result.strategy is not None
    assert len(result.schema_results) == 1
    assert len(result.wiki_results) == 1
    assert len(result.evidence_results) == 1
    assert result.evidence_results[0].query_provenance.query_id == query.query_id


def test_tool_surface_has_no_agent_runtime_dependencies():
    tools_module = importlib.import_module("transit_scholar.layer3.tools")
    source = inspect.getsource(tools_module) + inspect.getsource(
        importlib.import_module("transit_scholar.layer3.tools.service")
    )

    assert tools_module.KnowledgeToolService is KnowledgeToolService
    assert "langgraph" not in source.lower()
    assert "openai" not in source.lower()
    assert "mcp" not in source.lower()
