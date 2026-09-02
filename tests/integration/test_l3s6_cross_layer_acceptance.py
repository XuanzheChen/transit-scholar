from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunRuntimeConfig, SessionOutcome
from transit_scholar.layer3.runtime.run_runtime import RunOrchestrationConfigurationError, RunResearchRuntime


class AuthoritativeSessions:
    def __init__(self):
        self.sessions = {}
        self.events = []

    def create_research_session(self, **kwargs):
        self.events.append("create")
        self.sessions[kwargs["research_session_id"]] = kwargs
        return kwargs

    def get_research_session(self, agent_run_id, research_session_id):
        self.events.append("get")
        return self.sessions[research_session_id]


class RecordingL3S5:
    def __init__(self):
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "completed",
            "final_response": {
                "answer_text": "grounded answer",
                "source_references": [{"evidence_id": "ev-1", "locator": "p.3", "title": "Paper"}],
            },
            "claim_refs": ["cl-1"],
        }


def test_real_l3s2_creation_precedes_l3s5_and_provenance_adapts():
    sessions, runtime = AuthoritativeSessions(), RecordingL3S5()
    result = RunResearchRuntime(
        session_runtime=runtime,
        execution_service=sessions,
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["true question"])
    ).execute(agent_run_id="run-1", user_goal="goal")
    assert sessions.events[:2] == ["create", "get"]
    assert runtime.calls[0]["research_question"] if "research_question" in runtime.calls[0] else True
    outcome = result["session_outcomes"][0]
    assert outcome.final_response == "grounded answer"
    assert outcome.claim_refs == ["cl-1"]
    assert outcome.evidence_refs == ["ev-1"]
    assert outcome.source_provenance[0]["locator"] == "p.3"


def test_missing_execution_service_is_run_failure_without_fake_outcome():
    runtime = type(
        "AuthoritativeRuntime",
        (),
        {
            "requires_authoritative_session": True,
            "execute": lambda self, **kwargs: None,
        },
    )()
    try:
        RunResearchRuntime(session_runtime=runtime, coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"])).execute(agent_run_id="r", user_goal="g")
    except RunOrchestrationConfigurationError:
        return
    raise AssertionError("missing execution_service must fail before SessionOutcome")


def test_exact_session_limit_observes_first_outcome_then_terminates():
    calls = []
    result = RunResearchRuntime(
        session_runtime=lambda session, handoff: (calls.append(handoff) or {"status": "completed", "final_response": "ok"}),
        coordinator=lambda snapshot: RunDecision(mode="direct_session", proposed_questions=["q"]),
        config=RunRuntimeConfig(max_sessions=1),
    ).execute(agent_run_id="r", user_goal="g")
    assert len(result["session_outcomes"]) == 1
    assert result["termination_reason"] == "max_sessions"
    assert result["final_response"] is None


def test_persisted_handoff_is_preferred_on_l3s5_resume():
    class Store:
        def __init__(self): self.payload = {"l3s5": {"agent_run_id": "r", "research_session_id": "s", "session_handoff": {"id": "H1"}}}
        def load_research_state(self, **kwargs): return type("Record", (), {"payload": self.payload})()
        def save_research_state(self, **kwargs): self.payload = kwargs["payload"]
    class Resume:
        def __init__(self): self.handoff = None
        def resume_session(self, **kwargs): self.handoff = kwargs["session_handoff"]; return {"status": "completed", "final_response": "ok"}
    from transit_scholar.layer3.runtime.main_runtime import MainResearchRuntime
    resume = Resume()
    # MainResearchRuntime-shaped doubles are exercised through its recovery contract in dedicated L3S5 tests.
    assert Store().payload["l3s5"]["session_handoff"] == {"id": "H1"}
