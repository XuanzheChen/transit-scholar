"""Durable Main Runtime continuation without replaying Role actions."""

from types import SimpleNamespace

import pytest

from transit_scholar.layer3.actions import CreateQueryAction
from transit_scholar.layer3.agent import RoleId, RoleRuntimeProfile, built_in_role_registry
from transit_scholar.layer3.runtime import (
    InMemoryRoleExecutionStore,
    MainResearchRuntime,
    RoleRuntime,
)
from transit_scholar.layer3.context import RoleContext


class Crash(BaseException):
    pass


class ExecutionService:
    def get_agent_run(self, agent_run_id):
        return {"agent_run_id": agent_run_id}

    def get_research_session(self, agent_run_id, research_session_id):
        return {"research_session_id": research_session_id}

    def update_research_session_status(self, agent_run_id, research_session_id, status):
        return status


class ContextBuilder:
    def __init__(self, mutations):
        self.mutations = mutations

    def build(self, **kwargs):
        return SimpleNamespace(mutations=list(self.mutations))


class Projector:
    def project(self, snapshot, role):
        return RoleContext(
            role_id=role.role_id.value,
            sections={},
            omitted_sections=frozenset(),
            serialized_chars=2,
        )


class StateStore:
    def __init__(self):
        self.payload = {}

    def load_research_state(self, **kwargs):
        return SimpleNamespace(payload=self.payload) if self.payload else None

    def save_research_state(self, *, payload, **kwargs):
        self.payload = payload
        return SimpleNamespace(payload=payload)


class SelectQueryPolicy:
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return {"completed": True, "next_role_id": "query_planning"}


class CompleteCoordinatorPolicy:
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return {"completed": True, "next_role_id": None}


class QueryPolicy:
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return {"completed": True, "proposed_queries": ["query"]}


def _input(role_id, context):
    if role_id == RoleId.RESEARCH_COORDINATOR:
        return {"research_session_id": "session-1", "research_goal": "goal"}
    return {"research_session_id": "session-1", "research_question": "question"}


def test_resume_does_not_replay_action_committed_before_main_boundary():
    registry = built_in_role_registry(
        {role: RoleRuntimeProfile(max_steps=2, max_llm_calls=2, max_tool_calls=2) for role in RoleId}
    )
    role_store = InMemoryRoleExecutionStore()
    state_store = StateStore()
    mutations = []

    class RecordingExecutor:
        def execute(self, action, role):
            mutations.append(action.query_text)
            return {"query_text": action.query_text}

    planner = lambda role, output, context: [
        CreateQueryAction(
            workspace_id="workspace-1",
            agent_run_id="run-1",
            research_session_id="session-1",
            query_text=output["proposed_queries"][0],
        )
    ] if role.role_id == RoleId.QUERY_PLANNING else []

    recording_executor = RecordingExecutor()
    first_role_runtime = RoleRuntime(registry, role_store, action_executor=recording_executor)
    original_boundary = first_role_runtime._boundary

    def crash_after_commit(execution, event_type, **payload):
        original_boundary(execution, event_type, **payload)
        if event_type == "role.action" and payload.get("classification") == "action_committed":
            raise Crash()

    first_role_runtime._boundary = crash_after_commit
    first = MainResearchRuntime(
        registry=registry,
        role_runtime=first_role_runtime,
        execution_service=ExecutionService(),
        context_builder=ContextBuilder(mutations),
        policies={
            RoleId.RESEARCH_COORDINATOR: SelectQueryPolicy(),
            RoleId.QUERY_PLANNING: QueryPolicy(),
        },
        projector=Projector(),
        role_input_factory=_input,
        action_planner=planner,
        action_executor=recording_executor,
        state_store=state_store,
    )

    with pytest.raises(Crash):
        first.execute(agent_run_id="run-1", research_session_id="session-1")

    class RejectReplayExecutor:
        def execute(self, action, role):
            raise AssertionError("committed action was replayed")

    reject_replay_executor = RejectReplayExecutor()
    resumed = MainResearchRuntime(
        registry=registry,
        role_runtime=RoleRuntime(registry, role_store, action_executor=reject_replay_executor),
        execution_service=ExecutionService(),
        context_builder=ContextBuilder(mutations),
        policies={
            RoleId.RESEARCH_COORDINATOR: CompleteCoordinatorPolicy(),
            RoleId.QUERY_PLANNING: QueryPolicy(),
        },
        projector=Projector(),
        role_input_factory=_input,
        action_planner=planner,
        action_executor=reject_replay_executor,
        state_store=state_store,
    )
    result = resumed.resume_session(agent_run_id="run-1", research_session_id="session-1")

    assert result.status == "completed"
    assert mutations == ["query"]
    assert result.usage.tool_calls == 1
