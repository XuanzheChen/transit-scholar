"""Focused acceptance tests for the Layer3 Stage5 main research runtime."""

from collections import deque

import pytest

from transit_scholar.layer3.agent import (
    RoleId,
    RoleResult,
    RoleRuntimeProfile,
    RoleWorkingState,
    RuntimeUsage,
    built_in_role_registry,
)
from transit_scholar.layer3.actions import CreateQueryAction
from transit_scholar.layer3.runtime import MainResearchRuntime, MainRuntimeConfig, RoleRuntime


class ExecutionService:
    def __init__(self):
        self.statuses = []

    def get_agent_run(self, agent_run_id):
        return {"agent_run_id": agent_run_id}

    def get_research_session(self, agent_run_id, research_session_id):
        return {"research_session_id": research_session_id}

    def update_research_session_status(self, agent_run_id, research_session_id, status):
        self.statuses.append(status)


class ContextBuilder:
    def build(self, **kwargs):
        return object()


class Projector:
    def project(self, snapshot, role):
        return {"role_id": role.role_id.value}


class TraceSink:
    def __init__(self):
        self.events = []

    def append_event(self, **event):
        self.events.append(event)


def role_result(
    role_id,
    *,
    output=None,
    status="completed",
    llm_calls=1,
    tool_calls=0,
    failure_message=None,
):
    return RoleResult(
        role_execution_id=f"execution-{role_id.value}",
        role_id=role_id,
        status=status,
        output=output,
        working_state=RoleWorkingState(
            current_step=1,
            usage=RuntimeUsage(llm_calls=llm_calls, tool_calls=tool_calls),
        ),
        termination_reason=("semantic_completion" if status == "completed" else "failure"),
        failure_message=failure_message,
    )


class ScriptedRoleRuntime:
    def __init__(self, results):
        self.results = deque(results)
        self.calls = []

    def execute(self, role, role_input, policy, **ownership):
        self.calls.append((role.role_id, role.runtime_profile, ownership))
        return self.results.popleft()


def runtime(results, *, config=None, registry=None, policies=None, cancelled=None):
    registry = registry or built_in_role_registry()
    execution = ExecutionService()
    trace = TraceSink()
    role_runtime = ScriptedRoleRuntime(results)
    instance = MainResearchRuntime(
        registry=registry,
        role_runtime=role_runtime,
        execution_service=execution,
        context_builder=ContextBuilder(),
        policies=policies or {definition.role_id: object() for definition in registry.list()},
        config=config,
        projector=Projector(),
        trace=trace,
        role_input_factory=lambda role_id, context: {
            "research_session_id": "session-1",
            "claims": (),
            "accepted_evidence": (),
            "claim_evidence_links": (),
        },
        is_cancelled=cancelled,
    )
    return instance, execution, trace, role_runtime


def test_main_loop_finishes_session_through_coordinator_and_final_synthesis():
    instance, execution, trace, role_runtime = runtime(
        [
            role_result(
                RoleId.RESEARCH_COORDINATOR,
                output={"completed": False, "next_role_id": "final_synthesis"},
            ),
            role_result(
                RoleId.FINAL_SYNTHESIS,
                output={
                    "completed": True,
                    "answer_text": "Finished answer",
                    "citation_references": [],
                },
            ),
        ]
    )

    result = instance.execute(agent_run_id="run-1", research_session_id="session-1")

    assert result.status == "completed"
    assert result.termination_reason == "semantic_completion"
    assert result.final_response.answer_text == "Finished answer"
    assert result.final_response.citation_references == []
    assert execution.statuses == ["running", "completed"]
    assert [call[0] for call in role_runtime.calls] == [
        RoleId.RESEARCH_COORDINATOR,
        RoleId.FINAL_SYNTHESIS,
    ]
    assert [event["event_type"] for event in trace.events] == [
        "runtime.start",
        "runtime.step",
        "runtime.step",
        "runtime.completion",
    ]
    assert trace.events[-1]["payload"]["status"] == "completed"


@pytest.mark.parametrize(
    ("config", "first_result", "expected_reason"),
    [
        (
            MainRuntimeConfig(max_steps=1),
            role_result(
                RoleId.RESEARCH_COORDINATOR,
                output={"completed": False, "next_role_id": "query_planning"},
            ),
            "max_steps",
        ),
        (
            MainRuntimeConfig(max_llm_calls=1),
            role_result(
                RoleId.RESEARCH_COORDINATOR,
                output={"completed": False, "next_role_id": "query_planning"},
                llm_calls=1,
            ),
            "max_llm_calls",
        ),
        (
            MainRuntimeConfig(max_tool_calls=1),
            role_result(
                RoleId.RESEARCH_COORDINATOR,
                output={"completed": False, "next_role_id": "query_planning"},
                tool_calls=1,
            ),
            "max_tool_calls",
        ),
    ],
)
def test_main_runtime_limits_terminate_before_another_role(config, first_result, expected_reason):
    instance, execution, trace, role_runtime = runtime([first_result], config=config)

    result = instance.execute(agent_run_id="run-1", research_session_id="session-1")

    assert result.status == "terminated"
    assert result.termination_reason == expected_reason
    assert len(role_runtime.calls) == 1
    assert execution.statuses == ["running", "failed"]
    assert trace.events[-1]["payload"]["termination_reason"] == expected_reason


def test_main_max_failures_handles_role_failure_without_crashing_agent_run():
    failed = role_result(
        RoleId.RESEARCH_COORDINATOR,
        status="failed",
        failure_message="provider unavailable",
    )
    instance, execution, trace, _ = runtime(
        [failed], config=MainRuntimeConfig(max_failures=1)
    )

    result = instance.execute(agent_run_id="run-1", research_session_id="session-1")

    assert result.status == "failed"
    assert result.termination_reason == "max_failures"
    assert result.failure_message == "provider unavailable"
    assert result.role_results == [failed]
    assert execution.statuses == ["running", "failed"]
    assert "runtime.failure" in [event["event_type"] for event in trace.events]


def test_cancellation_is_deterministic_without_invoking_a_role():
    instance, execution, trace, role_runtime = runtime([], cancelled=lambda: True)

    result = instance.execute(agent_run_id="run-1", research_session_id="session-1")

    assert result.status == "terminated"
    assert result.termination_reason == "cancelled"
    assert role_runtime.calls == []
    assert execution.statuses == ["running", "cancelled"]
    assert trace.events[-1]["payload"]["termination_reason"] == "cancelled"


class TwoStepCoordinatorPolicy:
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return {
            "completed": state.current_step == 1,
            "next_role_id": None,
            "completion_reason": "done" if state.current_step == 1 else None,
        }


def test_role_budget_is_not_clamped_to_main_remaining_budget():
    registry = built_in_role_registry(
        {
            RoleId.RESEARCH_COORDINATOR: RoleRuntimeProfile(
                max_steps=2, max_llm_calls=2
            )
        }
    )
    execution = ExecutionService()
    instance = MainResearchRuntime(
        registry=registry,
        role_runtime=RoleRuntime(registry),
        execution_service=execution,
        context_builder=ContextBuilder(),
        policies={RoleId.RESEARCH_COORDINATOR: TwoStepCoordinatorPolicy()},
        config=MainRuntimeConfig(max_steps=1, max_llm_calls=1),
        projector=Projector(),
        role_input_factory=lambda role_id, context: {
            "research_session_id": "session-1",
            "research_goal": "goal",
        },
    )

    result = instance.execute(agent_run_id="run-1", research_session_id="session-1")

    assert result.status == "completed"
    assert result.usage.steps == 1
    assert result.usage.llm_calls == 2
    assert result.role_results[0].working_state.current_step == 2
    assert result.role_results[0].status == "completed"


class InvalidCoordinatorPolicy:
    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return {"completed": False, "next_role_id": "invented_role"}


def test_unregistered_role_selection_fails_structured_validation_and_never_executes():
    registry = built_in_role_registry()
    execution = ExecutionService()
    trace = TraceSink()
    instance = MainResearchRuntime(
        registry=registry,
        role_runtime=RoleRuntime(registry, trace=trace),
        execution_service=execution,
        context_builder=ContextBuilder(),
        policies={RoleId.RESEARCH_COORDINATOR: InvalidCoordinatorPolicy()},
        config=MainRuntimeConfig(max_failures=1),
        projector=Projector(),
        role_input_factory=lambda role_id, context: {
            "research_session_id": "session-1",
            "research_goal": "goal",
        },
        trace=trace,
    )

    result = instance.execute(agent_run_id="run-1", research_session_id="session-1")

    assert result.status == "failed"
    assert result.role_results[0].role_id == RoleId.RESEARCH_COORDINATOR
    assert result.role_results[0].status == "failed"
    assert result.role_results[0].working_state.current_step == 0
    assert all(
        event.get("payload", {}).get("role_id") != "invented_role" for event in trace.events
    )


def test_main_cycle_dispatches_structured_actions_and_traces_committed_results():
    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        def execute(self, action, role):
            self.calls.append((action, role.role_id))
            return {"query_id": "query-1"}

    action_executor = RecordingExecutor()
    instance, _, trace, _ = runtime(
        [
            role_result(
                RoleId.RESEARCH_COORDINATOR,
                output={"completed": False, "next_role_id": "query_planning"},
            ),
            role_result(
                RoleId.QUERY_PLANNING,
                output={"completed": True, "proposed_queries": ["first query"]},
            ),
            role_result(
                RoleId.RESEARCH_COORDINATOR,
                output={"completed": True, "next_role_id": None},
            ),
        ]
    )
    instance.action_executor = action_executor
    instance.action_planner = lambda role, output, context: (
        [
            CreateQueryAction(
                workspace_id="workspace-1",
                agent_run_id="run-1",
                research_session_id="session-1",
                query_text=output["proposed_queries"][0],
            )
        ]
        if role.role_id == RoleId.QUERY_PLANNING
        else []
    )

    result = instance.execute(agent_run_id="run-1", research_session_id="session-1")

    assert result.status == "completed"
    assert result.usage.tool_calls == 1
    assert action_executor.calls[0][1] == RoleId.QUERY_PLANNING
    assert action_executor.calls[0][0].action_type.value == "CREATE_QUERY"
    action_events = [event for event in trace.events if event["event_type"] == "runtime.action"]
    assert action_events[0]["payload"]["role_execution_id"] == "execution-query_planning"
    assert action_events[0]["payload"]["action_type"] == "CREATE_QUERY"
