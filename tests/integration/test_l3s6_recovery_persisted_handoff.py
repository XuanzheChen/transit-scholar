"""Recovery acceptance for persisted L3S5 session handoffs."""

from __future__ import annotations

import json

from transit_scholar.layer3.agent import RoleId, built_in_role_registry
from transit_scholar.layer3.context import RoleContextProjector, RuntimeContextSnapshotBuilder
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.runtime import MainResearchRuntime, MainRuntimeState, RoleRuntime
from transit_scholar.layer3.state import ResearchStateService
from transit_scholar.layer3.workspace import WorkspaceService


class _CapturingContextBuilder(RuntimeContextSnapshotBuilder):
    def __init__(self, session):
        super().__init__(session)
        self.snapshots = []

    def build(self, **kwargs):
        snapshot = super().build(**kwargs)
        self.snapshots.append(snapshot)
        return snapshot


class _RecoveryPolicy:
    def __init__(self):
        self.contexts = {role_id: [] for role_id in RoleId}
        self.coordinator_calls = 0

    def decide(self, definition, role_input, state, role_context, repair_context=None):
        self.contexts[definition.role_id].append(role_context)
        if definition.role_id == RoleId.RESEARCH_COORDINATOR:
            self.coordinator_calls += 1
            next_roles = (
                RoleId.QUERY_PLANNING,
                RoleId.EVIDENCE_REASONING,
                None,
            )
            return {
                "completed": True,
                "next_role_id": next_roles[self.coordinator_calls - 1],
            }
        if definition.role_id == RoleId.QUERY_PLANNING:
            return {"completed": True, "proposed_queries": []}
        if definition.role_id == RoleId.EVIDENCE_REASONING:
            return {
                "completed": True,
                "admitted_evidence_ids": [],
                "rejected_evidence_ids": [],
            }
        raise AssertionError(f"unexpected role: {definition.role_id}")


def test_resume_uses_persisted_handoff_in_real_snapshot_and_role_context(session):
    workspace = WorkspaceService(session).create(name="L3S6 recovery").workspace
    execution = AgentRunService(session)
    run = execution.create_agent_run(
        workspace_id=workspace.workspace_id,
        user_goal="Recover the session with durable handoff continuity",
    )
    research_session = execution.create_research_session(
        agent_run_id=run.agent_run_id,
        research_question="Which handoff should the resumed roles observe?",
    )
    persisted_handoff = {
        "handoff_id": "persisted-H1",
        "run_goal": "durably preserved goal",
    }
    reconstructed_handoff = {
        "handoff_id": "reconstructed-H2",
        "run_goal": "conflicting recovery reconstruction",
    }
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
    context_builder = _CapturingContextBuilder(session)
    policy = _RecoveryPolicy()
    runtime = MainResearchRuntime(
        registry=registry,
        role_runtime=RoleRuntime(registry),
        execution_service=execution,
        context_builder=context_builder,
        policies={role_id: policy for role_id in RoleId},
        projector=RoleContextProjector(),
        state_store=state_store,
    )

    result = runtime.resume_session(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
        session_handoff=reconstructed_handoff,
    )

    assert result.status == "completed"
    assert context_builder.snapshots
    assert all(
        snapshot.session_handoff == persisted_handoff
        for snapshot in context_builder.snapshots
    )
    assert all(
        snapshot.session_handoff != reconstructed_handoff
        for snapshot in context_builder.snapshots
    )

    coordinator_context = policy.contexts[RoleId.RESEARCH_COORDINATOR][0]
    query_context = policy.contexts[RoleId.QUERY_PLANNING][0]
    evidence_context = policy.contexts[RoleId.EVIDENCE_REASONING][0]
    assert coordinator_context.sections["session_handoff"] == persisted_handoff
    assert query_context.sections["session_handoff"] == persisted_handoff
    assert "session_handoff" not in evidence_context.sections
    assert all(
        "reconstructed-H2"
        not in json.dumps(context.model_dump(mode="json"), sort_keys=True)
        for contexts in policy.contexts.values()
        for context in contexts
    )
