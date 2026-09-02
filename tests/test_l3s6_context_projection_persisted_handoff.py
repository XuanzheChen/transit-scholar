"""Role-specific visibility for the L3S6 session handoff section."""

from transit_scholar.layer3.agent import RoleId, built_in_role_registry
from transit_scholar.layer3.context import RoleContextProjector, RuntimeContextSnapshotBuilder
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.workspace import WorkspaceService


def test_default_role_policies_project_handoff_only_to_allowed_roles(session):
    workspace = WorkspaceService(session).create(name="L3S6 context projection").workspace
    execution = AgentRunService(session)
    run = execution.create_agent_run(
        workspace_id=workspace.workspace_id,
        user_goal="Check role-specific persisted context visibility",
    )
    research_session = execution.create_research_session(
        agent_run_id=run.agent_run_id,
        research_question="Which roles can observe a session handoff?",
    )
    handoff = {"handoff_id": "persisted-H1"}
    snapshot = RuntimeContextSnapshotBuilder(session).build(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
        session_handoff=handoff,
    )
    registry = built_in_role_registry()
    projector = RoleContextProjector()

    coordinator = projector.project(snapshot, registry.get(RoleId.RESEARCH_COORDINATOR))
    query_planning = projector.project(snapshot, registry.get(RoleId.QUERY_PLANNING))
    evidence_reasoning = projector.project(snapshot, registry.get(RoleId.EVIDENCE_REASONING))

    assert coordinator.sections["session_handoff"] == handoff
    assert query_planning.sections["session_handoff"] == handoff
    assert "session_handoff" not in evidence_reasoning.sections
