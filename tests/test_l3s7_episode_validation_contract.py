import pytest
from pydantic import ValidationError

from transit_scholar.layer3.evidence import ResearchEvidence
from transit_scholar.layer3.memory import (
    EpisodicSemanticOutput,
    NormalizedEpisodeInput,
    build_episodic_record,
    validate_semantic_output,
)


def _normalized() -> NormalizedEpisodeInput:
    return NormalizedEpisodeInput(
        workspace_id="workspace-1",
        agent_run_id="run-1",
        user_goal_raw="goal",
        session_ids=("session-1",),
        final_outcome="completed",
        claim_ids=("claim-1",),
    )


def _semantic(claim_id: str) -> EpisodicSemanticOutput:
    return EpisodicSemanticOutput(
        goal_summary="goal",
        research_summary="summary",
        important_claim_ids=(claim_id,),
        unresolved_summary="",
    )


def test_unknown_claim_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown or unauthorized"):
        validate_semantic_output(_semantic("unknown"), _normalized())


@pytest.mark.parametrize(
    "claim",
    [
        {"claim_id": "claim-1", "workspace_id": "workspace-2", "agent_run_id": "run-1", "research_session_id": "session-1"},
        {"claim_id": "claim-1", "workspace_id": "workspace-1", "agent_run_id": "run-2", "research_session_id": "session-1"},
        {"claim_id": "claim-1", "workspace_id": "workspace-1", "agent_run_id": "run-1", "research_session_id": "session-2"},
    ],
)
def test_wrong_workspace_run_or_session_claim_is_rejected(claim) -> None:
    with pytest.raises(ValueError, match="claim ownership mismatch"):
        validate_semantic_output(_semantic("claim-1"), _normalized(), claims=[claim])


def test_claim_must_exist_in_authoritative_claim_collection() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        validate_semantic_output(_semantic("claim-1"), _normalized(), claims=[])


def test_episode_cannot_validate_as_research_evidence() -> None:
    record = build_episodic_record(
        _normalized(),
        _semantic("claim-1"),
        claims=[{
            "claim_id": "claim-1",
            "workspace_id": "workspace-1",
            "agent_run_id": "run-1",
            "research_session_id": "session-1",
        }],
    )

    assert record.is_authoritative_evidence is False
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(record.model_dump())
