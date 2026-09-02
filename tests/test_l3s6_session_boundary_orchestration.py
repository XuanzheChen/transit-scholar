from functools import wraps

import pytest

from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunRuntimeConfig
from transit_scholar.layer3.runtime.main_runtime import MainResearchRuntime as ProductionMainResearchRuntime
from transit_scholar.layer3.runtime.run_runtime import (
    RunOrchestrationConfigurationError,
    RunResearchRuntime,
)


class MainResearchRuntime:
    requires_authoritative_session = True

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


def test_session_pointer_is_persisted_before_created_and_started_trace_events():
    ordering = []

    class RecordingStore:
        def __init__(self):
            self.payload = None

        def save(self, _, payload):
            self.payload = payload
            pointer = payload["orchestration_state"]["current_research_session_id"]
            if pointer is not None:
                ordering.append(("persist", pointer))

        def load(self, _):
            return self.payload

    store = RecordingStore()

    class Trace:
        def append_event(self, **event):
            if event["event_type"] in {"run.session.created", "run.session.started"}:
                pointer = store.payload["orchestration_state"]["current_research_session_id"]
                ordering.append((event["event_type"], pointer))

    class Sessions:
        requires_authoritative_session = True

        def execute(self, **kwargs):
            ordering.append(("execute", kwargs["research_session_id"]))
            return {"status": "completed"}

    class RecordingService(Service):
        def create_research_session(self, **kwargs):
            ordering.append(("create", kwargs["research_session_id"]))
            return super().create_research_session(**kwargs)

        def get_research_session(self, agent_run_id, research_session_id):
            ordering.append(("verify", research_session_id))
            return super().get_research_session(agent_run_id, research_session_id)

    service = RecordingService()
    runtime = RunResearchRuntime(
        session_runtime=Sessions(),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
        execution_service=service,
        state_store=store,
        trace=Trace(),
        config=RunRuntimeConfig(max_sessions=1),
    )

    runtime.execute(agent_run_id="run", user_goal="goal")

    kinds = [kind for kind, _ in ordering]
    assert kinds == [
        "create", "verify", "persist", "run.session.created", "run.session.started", "execute"
    ]
    session_id = ordering[2][1]
    assert all(pointer == session_id for _, pointer in ordering)


def test_crash_after_session_pointer_persistence_leaves_recoverable_session():
    class Trace:
        def append_event(self, **event):
            if event["event_type"] == "run.session.started":
                raise RuntimeError("crash before L3S5 execute")

    service = Service()
    store = Store()
    first = RunResearchRuntime(
        session_runtime=MainResearchRuntime(),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
        execution_service=service,
        state_store=store,
        trace=Trace(),
    )
    with pytest.raises(RuntimeError, match="crash before L3S5 execute"):
        first.execute(agent_run_id="run", user_goal="goal")

    session_id = store.payload["orchestration_state"]["current_research_session_id"]
    assert session_id is not None

    class RecoveryRuntime:
        def __init__(self):
            self.resumed = []

        def resume_session(self, **kwargs):
            self.resumed.append(kwargs["research_session_id"])
            return {"status": "completed"}

    recovery_runtime = RecoveryRuntime()
    result = RunResearchRuntime(
        session_runtime=recovery_runtime,
        coordinator=lambda snapshot: RunDecision(mode="complete"),
        execution_service=service,
        state_store=store,
    ).execute(agent_run_id="run", user_goal="goal")

    assert recovery_runtime.resumed == [session_id]
    assert result["session_outcomes"][0].research_session_id == session_id


def test_real_main_runtime_requires_execution_service():
    runtime = RunResearchRuntime(
        session_runtime=object.__new__(ProductionMainResearchRuntime),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
    )
    with pytest.raises(RunOrchestrationConfigurationError):
        runtime.execute(agent_run_id="run", user_goal="goal")


def test_authoritative_capability_is_inherited_by_subclasses():
    class ProductionRuntime(ProductionMainResearchRuntime):
        pass

    runtime = RunResearchRuntime(
        session_runtime=object.__new__(ProductionRuntime),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
    )
    with pytest.raises(RunOrchestrationConfigurationError):
        runtime.execute(agent_run_id="run", user_goal="goal")


def test_main_runtime_subclass_cannot_disable_authoritative_capability():
    class ProductionRuntime(ProductionMainResearchRuntime):
        requires_authoritative_session = False

    runtime = RunResearchRuntime(
        session_runtime=object.__new__(ProductionRuntime),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
    )
    with pytest.raises(RunOrchestrationConfigurationError):
        runtime.execute(agent_run_id="run", user_goal="goal")


def test_wrapped_authoritative_runtime_cannot_bypass_requirement():
    class Proxy:
        def __init__(self, runtime):
            self.target = runtime

        def execute(self, **kwargs):
            return self.target.execute(**kwargs)

    runtime = RunResearchRuntime(
        session_runtime=Proxy(object.__new__(ProductionMainResearchRuntime)),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
    )
    with pytest.raises(RunOrchestrationConfigurationError):
        runtime.execute(agent_run_id="run", user_goal="goal")


def test_decorated_authoritative_runtime_cannot_bypass_requirement():
    target = object.__new__(ProductionMainResearchRuntime)

    class Decorator:
        def __init__(self, runtime):
            @wraps(runtime.execute)
            def execute(**kwargs):
                return runtime.execute(**kwargs)

            self.execute = execute

    runtime = RunResearchRuntime(
        session_runtime=Decorator(target),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
    )
    with pytest.raises(RunOrchestrationConfigurationError):
        runtime.execute(agent_run_id="run", user_goal="goal")


def test_authoritative_runtime_cannot_be_disabled_by_unit_double_override():
    runtime = RunResearchRuntime(
        session_runtime=object.__new__(ProductionMainResearchRuntime),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
        requires_execution_service=False,
    )
    with pytest.raises(RunOrchestrationConfigurationError):
        runtime.execute(agent_run_id="run", user_goal="goal")


def test_unit_double_can_explicitly_opt_out_of_authoritative_persistence():
    class UnitDouble:
        requires_authoritative_session = False

        def execute(self, **kwargs):
            return {"status": "completed"}

    runtime = RunResearchRuntime(
        session_runtime=UnitDouble(),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
        config=RunRuntimeConfig(max_sessions=1),
    )
    result = runtime.execute(agent_run_id="run", user_goal="goal")
    assert result["session_outcomes"][0].status == "completed"
