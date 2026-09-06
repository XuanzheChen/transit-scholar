"""Fixture-backed coverage for the official RuntimeFactory composition path."""

from transit_scholar.db.engine import SessionLocal
import pytest
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunRuntimeConfig
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product.runtime import RuntimeFactory
from transit_scholar.layer3.tools import KnowledgeToolService
from transit_scholar.layer3.synthesis import RunFinalSynthesisRole
from transit_scholar.layer3.retrieval import RagRetrievalAction, ResearchQuery, RetrievalStrategy

def _factory(root):
    class Lifecycle:
        episodic_store = object()

        def configure_authoritative_readers(self, **_kwargs):
            pass

        def maintain_before_session(self, **_kwargs):
            return None

        def complete_agent_run(self, **_kwargs):
            return None

    return RuntimeFactory(
        session_factory=SessionLocal,
        data_root=root,
        runtime_root=root / "layer3" / "runs",
        coordinator=lambda _snapshot: RunDecision(
            mode="complete", completion_reason="fixture complete"
        ),
        l3s7_lifecycle=Lifecycle(),
        episodic_memory=object(),
        run_config=RunRuntimeConfig(max_episodic_memory_candidates=0),
    )


def test_factory_builds_and_executes_existing_run_without_manual_core_composition(
    session, project_tmp_path
):
    workspace = WorkspaceService(session).create(name="factory fixture").workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Answer the fixture question"
    )
    session.commit()

    scope = _factory(project_tmp_path).build_run_scope(run.agent_run_id)
    try:
        result = scope.run_runtime.execute(agent_run_id=run.agent_run_id)
        assert result["status"] == "completed"
        assert scope.agent_run_id == run.agent_run_id
        assert scope.workspace_id == workspace.workspace_id
        assert scope.workspace_revision == workspace.revision
    finally:
        scope.close()


def test_rebuilt_scope_reuses_authoritative_identity_and_durable_state(
    session, project_tmp_path
):
    workspace = WorkspaceService(session).create(name="recovery fixture").workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Recover this run"
    )
    session.commit()
    root = project_tmp_path / "layer3" / "runs"

    first = _factory(project_tmp_path).build_run_scope(run.agent_run_id)
    try:
        first.run_runtime.state_store.save(
            run.agent_run_id,
            {"orchestration_state": {"agent_run_id": run.agent_run_id, "status": "running", "run_steps": 3}},
        )
    finally:
        first.close()

    second = _factory(project_tmp_path).build_run_scope(run.agent_run_id)
    try:
        assert second.agent_run_id == run.agent_run_id
        assert second.workspace_id == workspace.workspace_id
        assert second.workspace_revision == workspace.revision
        assert second.knowledge.workspace_id == workspace.workspace_id
        assert second.knowledge.expected_revision == workspace.revision
        assert second.run_runtime.state_store.load(run.agent_run_id)["orchestration_state"]["run_steps"] == 3
    finally:
        second.close()


def test_run_scope_is_disposable_non_owning_container(session, project_tmp_path):
    workspace = WorkspaceService(session).create(name="disposable fixture").workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Dispose scope"
    )
    session.commit()
    scope = _factory(project_tmp_path).build_run_scope(run.agent_run_id)
    assert scope.agent_run is not None
    scope.close()
    assert scope.session.is_active is True


def test_factory_composes_retrieval_service_and_final_synthesis(session, project_tmp_path):
    workspace = WorkspaceService(session).create(name="composition fixture").workspace
    run = AgentRunService(session).create_agent_run(workspace_id=workspace.workspace_id, user_goal="Compose")
    session.commit()
    scope = _factory(project_tmp_path).build_run_scope(run.agent_run_id)
    try:
        assert isinstance(scope.knowledge, KnowledgeToolService)
        assert scope.knowledge.gateway.workspace_id == workspace.workspace_id
        assert isinstance(scope.run_runtime.synthesis, RunFinalSynthesisRole)
        assert scope.role_runtime.store.__class__.__name__ == "_CommitBeforeRoleCheckpointStore"
    finally:
        scope.close()


def test_workspace_gateway_rejects_other_workspace_content(session, project_tmp_path):
    workspaces = WorkspaceService(session)
    workspace_a = workspaces.create(name="workspace-a").workspace
    workspace_b = workspaces.create(name="workspace-b").workspace
    run = AgentRunService(session).create_agent_run(workspace_id=workspace_a.workspace_id, user_goal="Isolate")
    session.commit()
    scope = _factory(project_tmp_path).build_run_scope(run.agent_run_id)
    try:
        with pytest.raises(ValueError, match="not members"):
            scope.knowledge.search_rag(
                ResearchQuery(query_id="q", session_id="s", workspace_id=workspace_a.workspace_id, query_text="query"),
                RagRetrievalAction(action_id="rag", source_query="query", scope="papers", paper_ids=["paper-only-in-b"]),
            )
    finally:
        scope.close()


def test_shared_llm_client_is_wrapped_for_retrieval_planning(session, project_tmp_path):
    class Client:
        def __init__(self):
            self.calls = []

        def generate_structured(self, messages, schema, metadata):
            self.calls.append((messages, schema, metadata))
            return RetrievalStrategy(query_id="q", actions=[RagRetrievalAction(action_id="rag", source_query="query")])

    workspace = WorkspaceService(session).create(name="planner fixture").workspace
    run = AgentRunService(session).create_agent_run(workspace_id=workspace.workspace_id, user_goal="Plan")
    session.commit()
    client = Client()
    factory = RuntimeFactory(
        session_factory=SessionLocal,
        data_root=project_tmp_path,
        runtime_root=project_tmp_path / "layer3" / "runs",
        llm_client=client,
    )
    scope = factory.build_run_scope(run.agent_run_id)
    try:
        strategy = scope.knowledge.planner.provider.plan("retrieval prompt")
        assert strategy.query_id == "q"
        assert client.calls[0][1] is RetrievalStrategy
        assert client.calls[0][2]["prompt_key"] == "retrieval_planner"
    finally:
        scope.close()
