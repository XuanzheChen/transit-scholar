"""Real L3S6-to-L3S5 RoleContext projection coverage."""

from __future__ import annotations

from collections import defaultdict

from transit_scholar.layer3.agent import RoleId, built_in_role_registry
from transit_scholar.layer3.context import (
    RoleContext,
    RoleContextProjector,
    RuntimeContextSnapshotBuilder,
)
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.runtime import MainResearchRuntime, RoleRuntime, RunResearchRuntime
from transit_scholar.layer3.workspace import WorkspaceService


class DeterministicRolePolicy:
    """Return valid built-in Role outputs while recording projected context."""

    def __init__(self, role_id: RoleId) -> None:
        self.role_id = role_id
        self.contexts: list[RoleContext] = []

    def decide(
        self,
        definition,
        role_input,
        state,
        role_context,
        repair_context=None,
    ):
        assert definition.role_id == self.role_id
        assert role_context.role_id == self.role_id.value
        self.contexts.append(role_context)
        if self.role_id == RoleId.RESEARCH_COORDINATOR:
            invocation = len(self.contexts)
            if invocation == 1:
                return {"completed": True, "next_role_id": RoleId.QUERY_PLANNING}
            if invocation == 2:
                return {"completed": True, "next_role_id": RoleId.EVIDENCE_REASONING}
            return {
                "completed": True,
                "next_role_id": None,
                "completion_reason": "all deterministic checks passed",
            }
        if self.role_id == RoleId.QUERY_PLANNING:
            return {"completed": True, "proposed_queries": []}
        if self.role_id == RoleId.EVIDENCE_REASONING:
            return {
                "completed": True,
                "admitted_evidence_ids": [],
                "rejected_evidence_ids": [],
            }
        raise AssertionError(f"unexpected Role in integration path: {self.role_id}")


class RecordingRoleRuntime(RoleRuntime):
    """Capture the exact RoleContext passed through the real RoleRuntime API."""

    def __init__(self, registry) -> None:
        super().__init__(registry)
        self.contexts: defaultdict[RoleId, list[RoleContext]] = defaultdict(list)

    def execute(self, role_definition, role_input, policy, **kwargs):
        role_context = kwargs["role_context"]
        assert isinstance(role_context, RoleContext)
        self.contexts[role_definition.role_id].append(role_context)
        return super().execute(role_definition, role_input, policy, **kwargs)


def test_run_research_runtime_projects_real_handoff_to_builtin_roles(session):
    workspace = WorkspaceService(session).create(name="L3S6 real RoleContext path").workspace
    execution = AgentRunService(session)
    run = execution.create_agent_run(
        workspace_id=workspace.workspace_id,
        user_goal="Trace one authoritative handoff through the L3S5 Roles",
    )

    registry = built_in_role_registry()
    role_runtime = RecordingRoleRuntime(registry)
    policies = {
        role_id: DeterministicRolePolicy(role_id)
        for role_id in (
            RoleId.RESEARCH_COORDINATOR,
            RoleId.QUERY_PLANNING,
            RoleId.EVIDENCE_REASONING,
        )
    }
    policies.update(
        {
            role_id: DeterministicRolePolicy(role_id)
            for role_id in (
                RoleId.CLAIM_REASONING,
                RoleId.FINAL_SYNTHESIS,
            )
        }
    )
    main_runtime = MainResearchRuntime(
        registry=registry,
        role_runtime=role_runtime,
        execution_service=execution,
        context_builder=RuntimeContextSnapshotBuilder(session),
        projector=RoleContextProjector(),
        policies=policies,
    )

    coordination_calls = []

    def coordinate(snapshot):
        coordination_calls.append(snapshot)
        if len(coordination_calls) == 1:
            return RunDecision(
                mode="direct_session",
                proposed_questions=["Which RoleContexts receive this handoff?"],
            )
        return RunDecision(mode="complete", completion_reason="session inspected")

    result = RunResearchRuntime(
        session_runtime=main_runtime,
        execution_service=execution,
        coordinator=coordinate,
    ).execute(agent_run_id=run.agent_run_id, user_goal=run.user_goal)

    assert result["status"] == "completed"
    assert len(result["session_outcomes"]) == 1
    created_sessions = execution.list_research_sessions(run.agent_run_id)
    assert len(created_sessions) == 1
    assert created_sessions[0].research_session_id == result["session_outcomes"][0].research_session_id
    assert created_sessions[0].status == "completed"

    coordinator_context = role_runtime.contexts[RoleId.RESEARCH_COORDINATOR]
    query_context = role_runtime.contexts[RoleId.QUERY_PLANNING]
    evidence_context = role_runtime.contexts[RoleId.EVIDENCE_REASONING]
    assert len(coordinator_context) == 3
    assert len(query_context) == 1
    assert len(evidence_context) == 1

    handoff = coordinator_context[0].sections["session_handoff"]
    assert handoff["run_goal"] == run.user_goal
    assert handoff["current_research_question"] == "Which RoleContexts receive this handoff?"
    assert query_context[0].sections["session_handoff"] == handoff
    assert all("session_handoff" not in context.sections for context in evidence_context)

    assert all(isinstance(context, RoleContext) for context in coordinator_context)
    assert all(isinstance(context, RoleContext) for context in query_context)
    assert all(isinstance(context, RoleContext) for context in evidence_context)
    assert all(snapshot.agent_run_id == run.agent_run_id for snapshot in coordination_calls)
