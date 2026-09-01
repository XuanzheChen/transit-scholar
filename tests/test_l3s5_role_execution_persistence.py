"""Role execution working-state persistence tests."""

from datetime import datetime, timezone

from transit_scholar.layer3.agent import (
    QueryPlanningOutput,
    RoleExecution,
    RoleWorkingState,
    RuntimeUsage,
    built_in_role_registry,
)
from transit_scholar.layer3.runtime import (
    FileRoleExecutionStore,
    InMemoryRoleExecutionStore,
    RoleRuntime,
)
from transit_scholar.layer3.context import RoleContext


class Policy:
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return QueryPlanningOutput(completed=True, proposed_queries=["query"])


def test_role_execution_can_be_reloaded_with_working_state_and_usage():
    registry = built_in_role_registry()
    store = InMemoryRoleExecutionStore()
    result = RoleRuntime(registry, store).execute(
        registry.get("query_planning"),
        {"research_session_id": "session-1", "research_question": "Question"},
        Policy(),
        agent_run_id="run-1",
        research_session_id="session-1",
        role_execution_id="execution-1",
        role_context=RoleContext(role_id="query_planning", sections={}, omitted_sections=frozenset(), serialized_chars=2),
    )

    restored = store.load("execution-1")
    assert restored.status == "completed"
    assert restored.working_state.current_step == 1
    assert restored.working_state.usage.llm_calls == 1
    assert restored.trace_scope == "role:execution-1"
    assert result.role_execution_id == restored.role_execution_id


def test_interrupted_execution_reloads_through_fresh_store_and_runtime(tmp_path):
    registry = built_in_role_registry()
    started_at = datetime.now(timezone.utc)
    execution = RoleExecution(
        role_execution_id="execution-interrupted",
        role_id="query_planning",
        agent_run_id="run-1",
        research_session_id="session-1",
        trace_scope="role:execution-interrupted",
        status="running",
        working_state=RoleWorkingState(
            current_step=2,
            last_observation={"query_count": 1},
            last_output={"completed": False, "proposed_queries": ["next query"]},
            intermediate_artifacts=[{"query_id": "query-1"}],
            usage=RuntimeUsage(llm_calls=2, tool_calls=1, failures=0),
        ),
        runtime_profile=registry.get("query_planning").runtime_profile,
        started_at=started_at,
    )
    FileRoleExecutionStore(tmp_path).save(execution)

    reconstructed_store = FileRoleExecutionStore(tmp_path)
    reconstructed_runtime = RoleRuntime(registry, reconstructed_store)
    restored = reconstructed_runtime.store.load("execution-interrupted")

    assert restored.status == "running"
    assert restored.role_id == "query_planning"
    assert restored.agent_run_id == "run-1"
    assert restored.research_session_id == "session-1"
    assert restored.working_state.current_step == 2
    assert restored.working_state.usage == RuntimeUsage(llm_calls=2, tool_calls=1, failures=0)
    assert restored.working_state.last_observation == {"query_count": 1}
    assert restored.started_at == started_at
    assert restored.ended_at is None
