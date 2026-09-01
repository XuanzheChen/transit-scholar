"""Recovery from durable RoleRuntime boundaries."""

import pytest
from pydantic import BaseModel, ConfigDict

from transit_scholar.layer3.agent import RoleRegistry, RoleRuntimeProfile, built_in_role_registry
from transit_scholar.layer3.runtime import InMemoryRoleExecutionStore, RoleRuntime


class ActionOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    completed: bool = False
    actions: list[object]


def _runtime(store, executor):
    original = built_in_role_registry(
        {"query_planning": RoleRuntimeProfile(max_steps=3, max_llm_calls=3, max_tool_calls=3)}
    ).get("query_planning")
    role = original.model_copy(update={"output_contract": ActionOutput})
    return role, RoleRuntime(RoleRegistry([role]), store, action_executor=executor)


class Crash(BaseException):
    pass


class CompletingPolicy:
    def decide(self, definition, role_input, state):
        return ActionOutput(completed=True, actions=[])


def _execute(runtime, role, policy):
    return runtime.execute(
        role,
        {"research_session_id": "session-1", "research_question": "Question"},
        policy,
        agent_run_id="run-1",
        research_session_id="session-1",
        role_execution_id="recoverable-role",
    )


def test_resume_after_committed_action_does_not_replay_mutation():
    store = InMemoryRoleExecutionStore()
    mutations = []

    class Executor:
        def execute(self, action, role):
            mutations.append(action["id"])
            return {"id": action["id"]}

    role, runtime = _runtime(store, Executor())

    class Policy:
        def decide(self, definition, role_input, state):
            if state.current_step == 0:
                return ActionOutput(actions=[{"id": "query-1"}])
            raise Crash()

    with pytest.raises(Crash):
        _execute(runtime, role, Policy())

    result = _execute(runtime, role, CompletingPolicy())
    assert result.status == "completed"
    assert mutations == ["query-1"]
    assert result.working_state.usage.tool_calls == 1


def test_resume_abandons_in_flight_tool_without_partial_continuation():
    store = InMemoryRoleExecutionStore()
    calls = []

    class Executor:
        def execute(self, action, role):
            calls.append(action["id"])
            raise Crash()

    role, runtime = _runtime(store, Executor())
    with pytest.raises(Crash):
        class ActionPolicy:
            def decide(self, definition, role_input, state):
                return ActionOutput(actions=[{"id": "claim-1"}])
        _execute(runtime, role, ActionPolicy())

    resumed_role, resumed = _runtime(store, Executor())
    result = _execute(resumed, resumed_role, CompletingPolicy())
    assert result.status == "completed"
    assert calls == ["claim-1"]
    assert result.working_state.operation_in_flight is None


def test_resume_before_llm_call_restarts_decision_from_persisted_boundary():
    store = InMemoryRoleExecutionStore()
    role, runtime = _runtime(store, object())
    original_boundary = runtime._boundary

    def crash_after_boundary(execution, event_type, **payload):
        original_boundary(execution, event_type, **payload)
        if event_type == "role.step":
            raise Crash()

    runtime._boundary = crash_after_boundary
    with pytest.raises(Crash):
        _execute(runtime, role, CompletingPolicy())

    resumed_role, resumed = _runtime(store, object())
    result = _execute(resumed, resumed_role, CompletingPolicy())
    assert result.status == "completed"
    assert result.working_state.current_step == 1
    assert result.working_state.usage.llm_calls == 1


def test_recovery_uses_persisted_profile_instead_of_new_registry_profile():
    store = InMemoryRoleExecutionStore()
    role, runtime = _runtime(store, object())
    original_boundary = runtime._boundary

    def crash_after_start(execution, event_type, **payload):
        original_boundary(execution, event_type, **payload)
        if event_type == "role.start":
            raise Crash()

    runtime._boundary = crash_after_start
    with pytest.raises(Crash):
        _execute(runtime, role, CompletingPolicy())

    changed = role.model_copy(
        update={"runtime_profile": role.runtime_profile.model_copy(update={"max_llm_calls": 0})}
    )
    resumed = RoleRuntime(RoleRegistry([changed]), store, action_executor=object())
    result = _execute(resumed, changed, CompletingPolicy())

    assert result.status == "completed"
    assert result.working_state.usage.llm_calls == 1
