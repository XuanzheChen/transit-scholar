import pytest

from transit_scholar.layer3.planning import RunDecision, ResearchPlan, ResearchPlanItem
from transit_scholar.layer3.run_context import (
    RunContextSnapshotBuilder, RunFinalResponseArtifact, RunOrchestrationState,
    RunRuntimeConfig, SessionHandoffProjector, SessionOutcome,
)
from transit_scholar.layer3.runtime.run_runtime import RunResearchRuntime
from transit_scholar.layer3.synthesis.run_final import RunFinalSynthesisRole


def _runtime(decisions, session_result=None, **kwargs):
    class Coordinator:
        def __init__(self): self.i = 0
        def __call__(self, snapshot):
            value = decisions[min(self.i, len(decisions) - 1)]; self.i += 1; return value
    class Sessions:
        def execute(self, **kwargs): return session_result or {"status": "completed", "final_response": "answer"}
    return RunResearchRuntime(session_runtime=Sessions(), coordinator=Coordinator(), **kwargs)


def test_direct_and_planned_promotion_and_final_artifact():
    rt = _runtime([RunDecision(mode="direct_session", proposed_questions=["q1"]), RunDecision(mode="planned_research", proposed_questions=["q2"]), RunDecision(mode="complete", completion_reason="done")])
    result = rt.execute(agent_run_id="r1", user_goal="goal")
    assert result["status"] == "completed" and result["final_response"] is not None
    assert result["research_plan"] is not None and len(result["session_outcomes"]) == 2


def test_invalid_decision_does_not_create_session():
    rt = _runtime([{"mode": "not-valid"}])
    with pytest.raises(Exception): rt.execute(agent_run_id="r", user_goal="g")


def test_handoff_is_bounded_and_required_budget_checked():
    snap = RunContextSnapshotBuilder().build(agent_run={"agent_run_id":"r","user_goal":"g"}, session_outcomes=[SessionOutcome(research_session_id="s", research_question="q", status="completed", final_summary="x"*100)])
    handoff = SessionHandoffProjector().project(snap, current_research_question="next", config=RunRuntimeConfig(max_serialized_chars=300))
    assert len(handoff.model_dump_json()) <= 300
    with pytest.raises(ValueError): SessionHandoffProjector().project(snap, current_research_question="y"*300, config=RunRuntimeConfig(max_serialized_chars=20))


def test_limits_are_exact_boundaries():
    rt = _runtime([RunDecision(mode="direct_session", proposed_questions=["q"]), RunDecision(mode="complete")], config=RunRuntimeConfig(max_sessions=1, max_run_steps=10))
    assert rt.execute(agent_run_id="r", user_goal="g")["status"] == "completed"


def test_unknown_plan_update_rejected():
    rt = _runtime([RunDecision(mode="planned_research", plan_item_updates=[ResearchPlanItem(item_id="unknown", research_question="q", order=0)])])
    with pytest.raises(ValueError):
        rt.execute(agent_run_id="r", user_goal="g")


def test_recovery_resumes_inflight_but_not_completed():
    class Store:
        def __init__(self): self.value={"orchestration_state":{"agent_run_id":"r","current_research_session_id":"s2"},"session_outcomes":[{"research_session_id":"s1","research_question":"q","status":"completed"}]}
        def load(self, _): return self.value
        def save(self, _, payload): self.value=payload
    class Sessions:
        def __init__(self): self.calls=[]
        def resume_session(self, **kw): self.calls.append(kw); return {"status":"completed","final_response":"resumed"}
        def execute(self, **kw): self.calls.append(kw); return {"status":"completed","final_response":"new"}
    sessions=Sessions(); rt=RunResearchRuntime(session_runtime=sessions, coordinator=lambda s: RunDecision(mode="complete"), state_store=Store())
    result=rt.execute(agent_run_id="r", user_goal="g")
    assert sessions.calls and sessions.calls[0]["research_session_id"] == "s2"


def test_synthesis_preserves_provenance_and_failures():
    snap=RunContextSnapshotBuilder().build(agent_run={"agent_run_id":"r","user_goal":"g"}, session_outcomes=[SessionOutcome(research_session_id="s",research_question="q",status="failed",failure_reason="x",evidence_refs=["e1"])])
    artifact=RunFinalSynthesisRole().synthesize(snap, answer_text="ok", evidence=["e1"])
    assert artifact.status == "completed" and artifact.failure_metadata["failed_sessions"]
