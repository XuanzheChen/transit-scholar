import pytest

from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.runtime.run_runtime import (
    RunOrchestrationConfigurationError,
    RunResearchRuntime,
)


class MainResearchRuntime:
    def execute(self, **kwargs):
        return {"status": "completed", "final_response": "ok"}


class Service:
    def __init__(self, fail=False):
        self.fail = fail
        self.sessions = {}

    def create_research_session(self, **kwargs):
        if self.fail:
            raise RuntimeError("create failed")
        self.sessions[kwargs["research_session_id"]] = dict(kwargs)
        return self.sessions[kwargs["research_session_id"]]

    def get_research_session(self, agent_run_id, research_session_id):
        return self.sessions[research_session_id]


class Store:
    def __init__(self):
        self.payload = None

    def save(self, _, payload):
        self.payload = payload

    def load(self, _):
        return self.payload


def test_missing_execution_service_is_orchestration_error_without_outcome():
    runtime = RunResearchRuntime(
        session_runtime=MainResearchRuntime(),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
    )
    with pytest.raises(RunOrchestrationConfigurationError):
        runtime.execute(agent_run_id="run", user_goal="goal")


def test_session_creation_failure_does_not_persist_pointer_or_outcome():
    store = Store()
    runtime = RunResearchRuntime(
        session_runtime=lambda session, handoff: {"status": "completed"},
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
        execution_service=Service(fail=True),
        state_store=store,
    )
    with pytest.raises(RunOrchestrationConfigurationError):
        runtime.execute(agent_run_id="run", user_goal="goal")
    assert store.payload["session_outcomes"] == []
    assert store.payload["orchestration_state"]["current_research_session_id"] is None


def test_session_is_persisted_before_l3s5_execution():
    service = Service()
    store = Store()
    ordering = []

    class Runtime(MainResearchRuntime):
        def execute(self, **kwargs):
            ordering.append(store.payload["orchestration_state"]["current_research_session_id"])
            return {"status": "completed"}

    runtime = RunResearchRuntime(
        session_runtime=Runtime(),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
        execution_service=service,
        state_store=store,
    )
    runtime.execute(agent_run_id="run", user_goal="goal")
    assert ordering and ordering[0] is not None
