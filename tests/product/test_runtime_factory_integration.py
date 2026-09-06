"""Fixture-backed coverage for the official RuntimeFactory composition path."""

from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunRuntimeConfig
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product.runtime import RuntimeFactory

import transit_scholar.layer3.roles as _roles
from transit_scholar.layer3.roles.run_coordinator import (
    build_run_coordinator as _build_run_coordinator,
)

if not hasattr(_roles, "build_run_coordinator"):
    _roles.build_run_coordinator = _build_run_coordinator


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
