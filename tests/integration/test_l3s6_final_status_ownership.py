import pytest

from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunRuntimeConfig
from transit_scholar.layer3.runtime.run_runtime import RunResearchRuntime
from transit_scholar.layer3.synthesis import RunFinalSynthesisRole


def _runtime(*, coordinator, config=None, is_cancelled=None, session_status="completed"):
    return RunResearchRuntime(
        session_runtime=lambda session, handoff: {
            "status": session_status,
            "final_response": "session answer",
        },
        coordinator=coordinator,
        synthesis=RunFinalSynthesisRole(),
        config=config,
        is_cancelled=is_cancelled,
    )


def test_validated_complete_decision_owns_completed_final_status():
    result = _runtime(
        coordinator=lambda snapshot: RunDecision(
            mode="complete", completion_reason="research_sufficient"
        )
    ).execute(agent_run_id="run-complete", user_goal="goal")

    assert result["status"] == "completed"
    assert result["final_response"].status == "completed"
    assert result["final_response"].completion_reason == "research_sufficient"


@pytest.mark.parametrize(
    ("reason", "config", "cancelled"),
    [
        ("max_run_steps", RunRuntimeConfig(max_run_steps=1), False),
        ("max_sessions", RunRuntimeConfig(max_sessions=1), False),
        ("max_planning_rounds", RunRuntimeConfig(max_planning_rounds=1), False),
        ("max_failed_sessions", RunRuntimeConfig(max_failed_sessions=0), False),
        ("cancelled", RunRuntimeConfig(), True),
    ],
)
def test_non_semantic_termination_has_no_completed_artifact(reason, config, cancelled):
    mode = "planned_research" if reason == "max_planning_rounds" else "direct_session"
    result = _runtime(
        coordinator=lambda snapshot: RunDecision(
            mode=mode,
            proposed_questions=["planned question"] if mode == "planned_research" else [],
        ),
        config=config,
        is_cancelled=lambda: cancelled,
        session_status="failed" if reason == "max_failed_sessions" else "completed",
    ).execute(agent_run_id=f"run-{reason}", user_goal="goal")

    assert result["termination_reason"] == reason
    assert result["final_response"] is None
