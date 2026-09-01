"""Recovery from durable RoleRuntime boundaries."""

import pytest
from pydantic import BaseModel, ConfigDict

from transit_scholar.layer3.agent import RoleRegistry, RoleRuntimeProfile, built_in_role_registry
from transit_scholar.layer3.runtime import InMemoryRoleExecutionStore, RoleRuntime
from transit_scholar.layer3.context import RoleContext


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
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return ActionOutput(completed=True, actions=[])


def _execute(runtime, role, policy):
    return runtime.execute(
        role,
        {"research_session_id": "session-1", "research_question": "Question"},
        policy,
        agent_run_id="run-1",
        research_session_id="session-1",
        role_execution_id="recoverable-role",
        role_context=RoleContext(role_id=role.role_id.value, sections={}, omitted_sections=frozenset(), serialized_chars=2),
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
        def decide(self, definition, role_input, state, role_context, repair_context=None):
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
            def decide(self, definition, role_input, state, role_context, repair_context=None):
                return ActionOutput(actions=[{"id": "claim-1"}])
        _execute(runtime, role, ActionPolicy())

    resumed_role, resumed = _runtime(store, Executor())
    result = _execute(resumed, resumed_role, CompletingPolicy())
    assert result.status == "terminated"
    assert result.termination_reason == "in_flight_action_abandoned"
    assert calls == ["claim-1"]
    assert result.working_state.operation_in_flight is None
    assert result.working_state.intermediate_artifacts[-1] == {
        "action": {"id": "claim-1"},
        "failure": "in_flight_action_abandoned_during_recovery",
    }


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


def test_recovery_uses_persisted_max_failures_for_failure_classification():
    store = InMemoryRoleExecutionStore()
    role, runtime = _runtime(store, object())
    persisted_role = role.model_copy(
        update={"runtime_profile": role.runtime_profile.model_copy(update={"max_failures": 2})}
    )
    runtime = RoleRuntime(RoleRegistry([persisted_role]), store, action_executor=object())
    original_boundary = runtime._boundary

    def crash_after_start(execution, event_type, **payload):
        original_boundary(execution, event_type, **payload)
        if event_type == "role.start":
            raise Crash()

    runtime._boundary = crash_after_start
    with pytest.raises(Crash):
        _execute(runtime, persisted_role, CompletingPolicy())

    changed = persisted_role.model_copy(
        update={
            "runtime_profile": persisted_role.runtime_profile.model_copy(
                update={"max_failures": 1}
            )
        }
    )

    class FailingPolicy:
        def decide(self, definition, role_input, state, role_context, repair_context=None):
            raise RuntimeError("recovered failure")

    result = _execute(
        RoleRuntime(RoleRegistry([changed]), store, action_executor=object()),
        changed,
        FailingPolicy(),
    )

    assert result.status == "failed"
    assert result.termination_reason == "unrecoverable_failure"
    assert result.working_state.usage.failures == 1
