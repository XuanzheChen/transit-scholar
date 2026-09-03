from datetime import datetime, timezone

from transit_scholar.layer3.context.models import RuntimeContextSnapshot
from transit_scholar.layer3.context.projector import RoleContextProjector
from transit_scholar.layer3.agent.models import RoleId
from transit_scholar.layer3.roles.builtin import (
    ResearchCoordinatorRole, QueryPlanningRole, EvidenceReasoningRole,
    ClaimReasoningRole, FinalSynthesisRole,
)
from transit_scholar.layer3.memory import EpisodicMemoryCandidate, EpisodicMemoryProvenance, EpisodicMemoryRecord


def test_only_query_planning_receives_session_memory():
    record = EpisodicMemoryRecord(
        memory_id="m", workspace_id="w", agent_run_id="r", user_goal_raw="goal",
        goal_summary="goal", research_summary="summary", unresolved_summary="",
        final_outcome="done", created_at=datetime.now(timezone.utc),
        provenance=EpisodicMemoryProvenance(workspace_id="w", agent_run_id="r"),
    )
    candidate = EpisodicMemoryCandidate(memory_id="m", workspace_id="w", agent_run_id="r", relevance=1.0, record=record)
    snapshot = RuntimeContextSnapshot.model_construct(episodic_memory=(candidate,))
    projector = RoleContextProjector()
    assert "episodic_memory" in projector.project(snapshot, QueryPlanningRole()).sections
    assert "episodic_memory" not in projector.project(snapshot, ResearchCoordinatorRole()).sections
    for role in (EvidenceReasoningRole(), ClaimReasoningRole(), FinalSynthesisRole()):
        assert "episodic_memory" not in projector.project(snapshot, role).sections
