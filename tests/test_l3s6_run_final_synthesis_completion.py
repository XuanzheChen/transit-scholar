from transit_scholar.layer3.run_context import RunContextSnapshotBuilder, SessionOutcome
from transit_scholar.layer3.synthesis import RunFinalSynthesisRole


def _snapshot():
    return RunContextSnapshotBuilder().build(
        agent_run={"agent_run_id": "run-1", "user_goal": "answer the question"},
        session_outcomes=[
            SessionOutcome(
                research_session_id="session-1",
                research_question="question",
                status="completed",
                final_response="answer",
            )
        ],
    )


def test_direct_synthesis_is_not_semantically_completed():
    artifact = RunFinalSynthesisRole().synthesize(_snapshot())

    assert artifact.status != "completed"
    assert artifact.completion_reason is None


def test_direct_synthesis_can_receive_explicit_completion_authorization():
    artifact = RunFinalSynthesisRole().synthesize(
        _snapshot(), completion_authorized=True
    )

    assert artifact.status == "completed"
