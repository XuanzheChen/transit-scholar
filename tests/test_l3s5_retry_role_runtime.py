"""Retry and budget behavior for the plain-Python RoleRuntime."""

from transit_scholar.layer3.agent import QueryPlanningOutput, built_in_role_registry
from transit_scholar.layer3.runtime import ProviderRetryableError, RoleRuntime


def _execute(policy, **profile):
    registry = built_in_role_registry({"query_planning": profile})
    return RoleRuntime(registry).execute(
        registry.get("query_planning"),
        {"research_session_id": "session-1", "research_question": "Question"},
        policy,
        agent_run_id="run-1",
        research_session_id="session-1",
    )


class ProviderRetryPolicy:
    def __init__(self):
        self.calls = 0

    def decide(self, definition, role_input, state):
        self.calls += 1
        if self.calls == 1:
            raise ProviderRetryableError("temporary outage")
        return QueryPlanningOutput(completed=True, proposed_queries=["query"])


class RepairPolicy:
    def __init__(self):
        self.calls = 0

    def decide(self, definition, role_input, state):
        self.calls += 1
        if self.calls == 1:
            return {"completed": True, "unexpected": "field"}
        return {"completed": True, "proposed_queries": ["query"]}


class FeedbackRepairPolicy:
    def __init__(self):
        self.repair_contexts = []

    def decide(self, definition, role_input, state, role_context, repair_context):
        self.repair_contexts.append(repair_context)
        if repair_context is None:
            return {"completed": True, "unexpected": "field"}
        return {"completed": True, "proposed_queries": ["corrected query"]}


def test_provider_retry_does_not_increment_agentic_step():
    result = _execute(
        ProviderRetryPolicy(),
        max_steps=1,
        max_llm_calls=2,
        provider_retry_limit=1,
    )

    assert result.status == "completed"
    assert result.working_state.current_step == 1
    assert result.working_state.usage.llm_calls == 2
    assert result.working_state.retries.provider_retries == 1


def test_structured_output_repair_does_not_increment_agentic_step():
    result = _execute(
        RepairPolicy(),
        max_steps=1,
        max_llm_calls=2,
        structured_output_repair_limit=1,
    )

    assert result.status == "completed"
    assert result.working_state.current_step == 1
    assert result.working_state.usage.llm_calls == 2
    assert result.working_state.retries.structured_output_repairs == 1


def test_structured_output_repair_receives_validation_feedback():
    policy = FeedbackRepairPolicy()
    result = _execute(
        policy,
        max_steps=1,
        max_llm_calls=2,
        structured_output_repair_limit=1,
    )

    assert result.status == "completed"
    feedback = policy.repair_contexts[1]
    assert feedback.invalid_output == {"completed": True, "unexpected": "field"}
    assert feedback.validation_errors
    assert feedback.attempt == 1


def test_llm_budget_exhaustion_returns_structured_termination():
    result = _execute(
        ProviderRetryPolicy(),
        max_steps=2,
        max_llm_calls=1,
        provider_retry_limit=2,
    )

    assert result.status == "terminated"
    assert result.termination_reason == "max_llm_calls"
    assert result.working_state.current_step == 0


def test_policy_failure_is_isolated_in_role_result():
    class FailingPolicy:
        def decide(self, definition, role_input, state):
            raise RuntimeError("role-only failure")

    result = _execute(FailingPolicy(), max_steps=1, max_llm_calls=1, max_failures=1)

    assert result.status == "failed"
    assert result.termination_reason == "max_failures"
    assert result.failure_message == "role-only failure"
