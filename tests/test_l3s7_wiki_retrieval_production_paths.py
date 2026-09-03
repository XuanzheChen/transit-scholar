from transit_scholar.db.models import Paper
from transit_scholar.layer3.agentic_wiki import AgenticWikiStore
from transit_scholar.layer3.knowledge.gateway import WorkspaceKnowledgeGateway
from transit_scholar.layer3.knowledge_evolution.models import AgenticWikiEntry
from transit_scholar.layer3.retrieval import ResearchQuery, WikiRetrievalAction
from transit_scholar.layer3.tools.service import KnowledgeToolService
from transit_scholar.layer3.workspace.service import WorkspaceService
from transit_scholar.layer3.storage.paths import workspace_layout


def test_knowledge_tool_service_searches_persisted_agentic_entry_via_real_gateway(session, project_tmp_path):
    workspace = WorkspaceService(session).create(name="Production Agentic Wiki", workspace_id="prod-wiki").workspace
    current = Paper(id="paper-current", title="Current source", status="active")
    foreign = Paper(id="paper-foreign", title="Foreign source", status="active")
    session.add_all([current, foreign]); session.flush()
    WorkspaceService(session).add_paper(workspace.workspace_id, current.id)
    store_base = workspace_layout(workspace.workspace_id, data_root=project_tmp_path).base_dir
    AgenticWikiStore.for_workspace(workspace.workspace_id, base_dir=store_base).put(AgenticWikiEntry(
        entry_id="entry-prod", workspace_id=workspace.workspace_id, title="Agentic transit knowledge",
        content="Persisted agentic transit knowledge", originating_agent_run_id="run-prod",
        paper_ids=(current.id, foreign.id)))
    gateway = WorkspaceKnowledgeGateway(session, workspace_id=workspace.workspace_id, data_root=project_tmp_path)
    envelope = KnowledgeToolService(gateway).search_wiki(
        ResearchQuery(query_id="q-prod", session_id="s-prod", workspace_id=workspace.workspace_id, query_text="agentic transit"),
        WikiRetrievalAction(action_id="a-prod", source_query="agentic transit", discover_paper_ids=True))
    assert len(envelope.wiki_results) == 1
    result = envelope.wiki_results[0]
    assert result.node_id == "entry-prod"
    assert result.navigation["source_kind"] == "agentic_wiki"
    assert result.discovered_paper_ids == [current.id]
    assert envelope.diagnostics[0].status == "degraded"


def test_fresh_gateway_reads_same_durable_agentic_entry(session, project_tmp_path):
    workspace = WorkspaceService(session).create(name="Fresh Gateway Wiki", workspace_id="fresh-wiki").workspace
    paper = Paper(id="fresh-paper", title="Fresh source", status="active")
    session.add(paper); session.flush(); WorkspaceService(session).add_paper(workspace.workspace_id, paper.id)
    store_base = workspace_layout(workspace.workspace_id, data_root=project_tmp_path).base_dir
    AgenticWikiStore.for_workspace(workspace.workspace_id, base_dir=store_base).put(AgenticWikiEntry(
        entry_id="fresh-entry", workspace_id=workspace.workspace_id, title="Durable agentic topic",
        content="Durable agentic content", originating_agent_run_id="run-fresh", paper_ids=(paper.id,)))
    gateway = WorkspaceKnowledgeGateway(session, workspace_id=workspace.workspace_id, data_root=project_tmp_path)
    result = gateway.search_wiki("durable agentic", limit=10, mode="lexical")
    assert [hit.object_id for hit in result.hits] == ["fresh-entry"]
    assert result.hits[0].source_kind == "agentic_wiki"
