import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.paths import save_current
from transit_scholar.layer3.agentic_wiki import AgenticWikiMaintenance, AgenticWikiStore
from transit_scholar.layer3.knowledge.gateway import L2S1EvidenceDelegate, WorkspaceKnowledgeGateway
from transit_scholar.layer3.workspace.service import WorkspaceService
from transit_scholar.layer3.storage.paths import workspace_layout
from transit_scholar.layer2.schema import RetrievalHit, RetrievalResult, SourceRef
from transit_scholar.layer3.retrieval import RagRetrievalAction, ResearchQuery
from transit_scholar.layer3.tools import KnowledgeToolService
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import (
    ClaimEvidenceLinkService,
    ClaimService,
    EvidenceService,
    QueryService,
    ResearchReasoningLedgerService,
)
from transit_scholar.layer3.knowledge_evolution.models import AgenticWikiEntry
from transit_scholar.layer3.runtime.main_runtime import MainResearchRuntime
from transit_scholar.layer3.agent.registry import RoleRegistry


def test_gateway_exposes_current_parse_identity_for_member_paper(session, project_tmp_path):
    workspace = WorkspaceService(session).create(name="identity", workspace_id="identity-ws").workspace
    paper = Paper(id="identity-paper", title="Identity", status="active")
    session.add(paper)
    session.flush()
    WorkspaceService(session).add_paper(workspace.workspace_id, paper.id)
    config = Layer2Config(data_root=project_tmp_path)
    save_current(config.parsed_paper_dir(paper.id), "parse-a")
    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace.workspace_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config),
    )
    assert gateway.current_source_identity(paper.id) == "parse-a"


def test_authoritative_reader_typeerror_propagates_once():
    calls = []

    def reader(workspace_id):
        calls.append(workspace_id)
        raise TypeError("reader body failure")

    maintenance = AgenticWikiMaintenance(
        AgenticWikiStore(), claims_resolver=reader
    )
    with pytest.raises(TypeError, match="reader body failure"):
        maintenance("ws")
    assert calls == ["ws"]


def test_reader_alternate_noarg_signature_adapts():
    calls = []

    def reader():
        calls.append(True)
        return []

    maintenance = AgenticWikiMaintenance(
        AgenticWikiStore(), claims_resolver=reader
    )
    maintenance("ws")
    assert calls == [True]


def test_knowledge_tool_rag_stamps_canonical_parse_identity_from_gateway(
    session, project_tmp_path
):
    workspace = WorkspaceService(session).create(
        name="rag-identity", workspace_id="rag-identity-ws"
    ).workspace
    paper = Paper(id="rag-identity-paper", title="Identity", status="active")
    session.add(paper)
    session.flush()
    WorkspaceService(session).add_paper(workspace.workspace_id, paper.id)
    config = Layer2Config(data_root=project_tmp_path)
    save_current(config.parsed_paper_dir(paper.id), "parse-a")
    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace.workspace_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config),
    )

    def search_evidence(_paper_id, _query, *, top_k=20, filters=None):
        return RetrievalResult(
            status="ok",
            method="bm25",
            hits=[
                RetrievalHit(
                    paper_id=paper.id,
                    chunk_id="chunk-1",
                    score=1.0,
                    retrieval_method="bm25",
                    section_path=[],
                    pages=[],
                    source_refs=[SourceRef("block-1", 0, 8)],
                    text="grounded text",
                    rank=1,
                )
            ],
        )

    gateway.search_evidence = search_evidence
    service = KnowledgeToolService(gateway)
    query = ResearchQuery(
        query_id="q-identity", session_id="s-identity", workspace_id=workspace.workspace_id,
        query_text="identity",
    )
    action = RagRetrievalAction(
        action_id="rag-identity", source_query="identity", scope="papers", paper_ids=[paper.id]
    )
    evidence = service.search_rag(query, action).evidence_results[0]
    assert evidence.locator.parse_run_id == "parse-a"
    assert evidence.locator.canonical_source_version == "parse-a"
    assert evidence.paper_provenance.parse_run_id == "parse-a"
    assert evidence.paper_provenance.canonical_source_version == "parse-a"

    # The production-shaped service output is fully serializable and retains
    # the reparse-stable identity that the ledger persists.
    payload = evidence.model_dump(mode="json")
    assert payload["locator"]["parse_run_id"] == "parse-a"
    assert payload["paper_provenance"]["canonical_source_version"] == "parse-a"

    save_current(config.parsed_paper_dir(paper.id), "parse-b")
    assert gateway.current_source_identity(paper.id) == "parse-b"

    # Reconstructing the gateway/tool service observes the new canonical
    # source identity without any manually shared in-memory state.
    gateway2 = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace.workspace_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config),
    )
    gateway2.search_evidence = search_evidence
    evidence_b = KnowledgeToolService(gateway2).search_rag(query, action).evidence_results[0]
    assert evidence_b.locator.parse_run_id == "parse-b"
    assert evidence_b.paper_provenance.canonical_source_version == "parse-b"


def test_production_chain_ledger_promotion_and_default_maintenance_stales_on_reparse(
    session, project_tmp_path
):
    workspace = WorkspaceService(session).create(
        name="chain", workspace_id="chain-ws"
    ).workspace
    paper = Paper(id="chain-paper", title="Chain", status="active")
    session.add(paper); session.flush()
    config = Layer2Config(data_root=project_tmp_path)
    workspace_service = WorkspaceService(session, l2_config=config)
    workspace_service.add_paper(workspace.workspace_id, paper.id)
    save_current(config.parsed_paper_dir(paper.id), "parse-a")
    gateway = WorkspaceKnowledgeGateway(
        session, workspace_id=workspace.workspace_id, data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config),
    )

    def search_evidence(_paper_id, _query, *, top_k=20, filters=None):
        return RetrievalResult(status="ok", method="bm25", hits=[RetrievalHit(
            paper_id=paper.id, chunk_id="chunk-1", score=1.0,
            retrieval_method="bm25", section_path=[], pages=[],
            source_refs=[SourceRef("block-1", 0, 8)], text="grounded", rank=1,
        )])

    gateway.search_evidence = search_evidence
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="chain"
    )
    research_session = AgentRunService(session).create_research_session(
        agent_run_id=run.agent_run_id, research_question="chain"
    )
    query = QueryService(session).create_query(
        research_session_id=research_session.research_session_id,
        query_text="chain", status="completed",
    )
    research_query = ResearchQuery(
        query_id=query.query_id, session_id=research_session.research_session_id,
        workspace_id=workspace.workspace_id, query_text="chain",
    )
    action = RagRetrievalAction(
        action_id="chain-rag", source_query="chain", scope="papers", paper_ids=[paper.id]
    )
    produced = KnowledgeToolService(gateway).search_rag(research_query, action).evidence_results[0]
    assert produced.locator.parse_run_id == "parse-a"
    admitted = EvidenceService(session).admit_evidence(
        research_session_id=research_session.research_session_id,
        source_query_id=query.query_id, evidence=produced,
    )
    reread = ResearchReasoningLedgerService(session).get_evidence(
        evidence_id=admitted.evidence_id,
        research_session_id=research_session.research_session_id,
    )
    assert reread.locator.parse_run_id == "parse-a"
    claim = ClaimService(session).create_claim(
        research_session_id=research_session.research_session_id,
        statement="Chain claim", status="supported",
    )
    ClaimEvidenceLinkService(session).link_evidence_to_claim(
        research_session_id=research_session.research_session_id,
        claim_id=claim.claim_id, evidence_id=admitted.evidence_id, relation="supports",
    )
    store = AgenticWikiStore.for_workspace(workspace.workspace_id, base_dir=project_tmp_path)
    store.put(AgenticWikiEntry(
        entry_id="chain-entry", workspace_id=workspace.workspace_id, title="Chain",
        content="Grounded", originating_agent_run_id=run.agent_run_id,
        source_claim_ids=(claim.claim_id,), evidence_refs=(admitted.evidence_id,),
    ))
    runtime = MainResearchRuntime(
        registry=RoleRegistry([]), role_runtime=object(),
        execution_service=AgentRunService(session), context_builder=object(), policies={},
        agentic_wiki_base_dir=project_tmp_path,
        workspace_service=workspace_service,
        ledger_service=ResearchReasoningLedgerService(session),
    )
    save_current(config.parsed_paper_dir(paper.id), "parse-b")
    runtime.agentic_wiki_maintenance(workspace.workspace_id)
    fresh = AgenticWikiStore.for_workspace(workspace.workspace_id, base_dir=project_tmp_path)
    assert fresh.get("chain-entry", workspace.workspace_id).status == "stale"
    assert fresh.list(workspace.workspace_id) == []
