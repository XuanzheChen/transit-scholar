"""Trace and action-boundary behavior for RoleRuntime."""

from pydantic import BaseModel, ConfigDict

from transit_scholar.layer3.agent import (
    QueryPlanningOutput,
    RoleDefinition,
    RoleRegistry,
    RoleRuntimeProfile,
    built_in_role_registry,
)
from transit_scholar.layer3.runtime import InMemoryRoleExecutionStore, RoleRuntime
from transit_scholar.layer3.context import RoleContext


class TraceSink:
    def __init__(self):
        self.events = []

    def append_event(self, **event):
        self.events.append(event)


class CompletingPolicy:
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return QueryPlanningOutput(completed=True, proposed_queries=["query"])


def test_role_events_use_agent_trace_shape_and_role_identity():
    registry = built_in_role_registry()
    trace = TraceSink()
    result = RoleRuntime(registry, trace=trace).execute(
        registry.get("query_planning"),
        {"research_session_id": "session-1", "research_question": "Question"},
        CompletingPolicy(),
        agent_run_id="run-1",
        research_session_id="session-1",
        role_execution_id="role-execution-1",
        role_context=RoleContext(role_id="query_planning", sections={}, omitted_sections=frozenset(), serialized_chars=2),
    )

    assert result.status == "completed"
    assert [event["event_type"] for event in trace.events] == [
        "role.start",
        "role.step",
        "role.result",
        "role.completion",
    ]
    assert all(
        event["payload"]["role_execution_id"] == "role-execution-1"
        and event["payload"]["role_id"] == "query_planning"
        for event in trace.events
    )


class ActionOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    completed: bool = False
    actions: list[object]


class ScriptedPolicy:
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        if state.current_step == 0:
            return ActionOutput(actions=[{"id": "committed"}])
        raise RuntimeError("failure after commit")


class RecordingExecutor:
    def __init__(self):
        self.committed = []

    def execute(self, action, role):
        self.committed.append(action)
        return {"durable_id": action["id"]}


def test_role_executes_two_actions_before_completion_and_counts_both():
    original = built_in_role_registry(
        {"query_planning": RoleRuntimeProfile(max_steps=1, max_llm_calls=1, max_tool_calls=2)}
    ).get("query_planning")
    role = original.model_copy(update={"output_contract": ActionOutput})
    registry = RoleRegistry([role])
    store = InMemoryRoleExecutionStore()
    executor = RecordingExecutor()
    trace = TraceSink()

    class TwoActionPolicy:
        def decide(self, definition, role_input, state, role_context, repair_context=None):
            return ActionOutput(
                completed=True,
                actions=[{"id": "action-a"}, {"id": "action-b"}],
            )

    result = RoleRuntime(registry, store, trace=trace, action_executor=executor).execute(
        role,
        {"research_session_id": "session-1", "research_question": "Question"},
        TwoActionPolicy(),
        agent_run_id="run-1",
        research_session_id="session-1",
        role_execution_id="two-action-role",
        role_context=RoleContext(
            role_id="query_planning",
            sections={},
            omitted_sections=frozenset(),
            serialized_chars=2,
        ),
    )

    persisted = store.load("two-action-role")
    assert executor.committed == [{"id": "action-a"}, {"id": "action-b"}]
    assert result.status == "completed"
    assert result.working_state.usage.tool_calls == 2
    assert [item["action"] for item in persisted.working_state.intermediate_artifacts] == [
        {"id": "action-a"},
        {"id": "action-b"},
    ]
    assert persisted.status == "completed"
    events = [
        (event["event_type"], event["payload"].get("classification"))
        for event in trace.events
        if event["payload"].get("classification") == "action_committed"
        or event["event_type"] == "role.completion"
    ]
    assert events == [
        ("role.action", "action_committed"),
        ("role.action", "action_committed"),
        ("role.completion", "semantic_completion"),
    ]


def test_zero_role_tool_budget_blocks_action_before_mutation():
    original = built_in_role_registry(
        {"query_planning": RoleRuntimeProfile(max_steps=1, max_llm_calls=1, max_tool_calls=0)}
    ).get("query_planning")
    role = original.model_copy(update={"output_contract": ActionOutput})
    registry = RoleRegistry([role])
    executor = RecordingExecutor()

    class ActionPolicy:
        def decide(self, definition, role_input, state, role_context, repair_context=None):
            return ActionOutput(completed=True, actions=[{"id": "blocked"}])

    result = RoleRuntime(registry, action_executor=executor).execute(
        role,
        {"research_session_id": "session-1", "research_question": "Question"},
        ActionPolicy(),
        agent_run_id="run-1",
        research_session_id="session-1",
        role_context=RoleContext(
            role_id="query_planning",
            sections={},
            omitted_sections=frozenset(),
            serialized_chars=2,
        ),
    )

    assert result.status == "terminated"
    assert result.termination_reason == "max_tool_calls"
    assert result.working_state.usage.tool_calls == 0
    assert result.working_state.intermediate_artifacts == []
    assert executor.committed == []


def test_committed_action_and_snapshot_survive_later_role_failure():
    original = built_in_role_registry(
        {"query_planning": RoleRuntimeProfile(max_steps=2, max_llm_calls=2, max_tool_calls=1)}
    ).get("query_planning")
    role = original.model_copy(update={"output_contract": ActionOutput})
    registry = RoleRegistry([role])
    store = InMemoryRoleExecutionStore()
    executor = RecordingExecutor()

    result = RoleRuntime(registry, store, action_executor=executor).execute(
        role,
        {"research_session_id": "session-1", "research_question": "Question"},
        ScriptedPolicy(),
        agent_run_id="run-1",
        research_session_id="session-1",
        role_execution_id="partial-role",
        role_context=RoleContext(role_id="query_planning", sections={}, omitted_sections=frozenset(), serialized_chars=2),
    )

    persisted = store.load("partial-role")
    assert result.status == "failed"
    assert executor.committed == [{"id": "committed"}]
    assert persisted.working_state.current_step == 1
    assert persisted.working_state.usage.tool_calls == 1
    assert persisted.working_state.intermediate_artifacts[0]["result"] == {
        "durable_id": "committed"
    }
