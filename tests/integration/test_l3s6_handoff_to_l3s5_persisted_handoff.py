"""Durable handoff transfer into the real L3S5 resume boundary."""

from transit_scholar.layer3.agent import RoleId, built_in_role_registry
from transit_scholar.layer3.context import RuntimeContextSnapshotBuilder
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.runtime import MainResearchRuntime, MainRuntimeState, RoleRuntime
from transit_scholar.layer3.state import ResearchStateService
from transit_scholar.layer3.workspace import WorkspaceService


def test_supplied_recovery_handoff_cannot_replace_durable_l3s5_handoff(session):
    workspace = WorkspaceService(session).create(name="L3S6 handoff transfer").workspace
    execution = AgentRunService(session)
    run = execution.create_agent_run(
        workspace_id=workspace.workspace_id,
        user_goal="Keep the durable handoff authoritative during resume",
    )
    research_session = execution.create_research_session(
        agent_run_id=run.agent_run_id,
        research_question="Which handoff reaches L3S5?",
    )
    persisted_handoff = {"handoff_id": "persisted-H1"}
    supplied_handoff = {"handoff_id": "supplied-H2"}
    state_store = ResearchStateService(session)
    state_store.save_research_state(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
        payload={
            "l3s5": MainRuntimeState(
                agent_run_id=run.agent_run_id,
                research_session_id=research_session.research_session_id,
                session_handoff=persisted_handoff,
            ).model_dump(mode="json")
        },
    )

    registry = built_in_role_registry()

    class CompletePolicy:
        def decide(self, definition, role_input, state, role_context, repair_context=None):
            assert role_context.sections["session_handoff"] == persisted_handoff
            return {"completed": True, "next_role_id": None}

    runtime = MainResearchRuntime(
        registry=registry,
        role_runtime=RoleRuntime(registry),
        execution_service=execution,
        context_builder=RuntimeContextSnapshotBuilder(session),
        policies={role_id: CompletePolicy() for role_id in RoleId},
        state_store=state_store,
    )

    result = runtime.resume_session(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
        session_handoff=supplied_handoff,
    )

    assert result.status == "completed"
    durable = state_store.load_research_state(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
    )
    assert durable is not None
    assert durable.payload["l3s5"]["session_handoff"] == persisted_handoff
    assert durable.payload["l3s5"]["session_handoff"] != supplied_handoff
