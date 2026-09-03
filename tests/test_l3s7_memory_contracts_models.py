from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from transit_scholar.layer3.memory import (
    EpisodicMemoryProvenance,
    EpisodicMemoryRecord,
    MemoryKind,
    MemorySourceKind,
)


def test_three_memory_semantics_are_explicit_and_distinct() -> None:
    assert {kind.value for kind in MemoryKind} == {
        "working_memory",
        "episodic_memory",
        "semantic_memory",
    }
    assert MemorySourceKind.EVIDENCE != MemorySourceKind.EPISODIC_MEMORY


def test_one_agent_run_has_one_canonical_episode_identity() -> None:
    assert EpisodicMemoryRecord.canonical_memory_id("run-1") == "episodic-memory:run-1"
    assert EpisodicMemoryRecord.canonical_memory_id("run-1") == EpisodicMemoryRecord.canonical_memory_id("run-1")


def test_structured_episode_validates_identity_provenance_and_auxiliary_semantics() -> None:
    provenance = EpisodicMemoryProvenance(
        workspace_id="workspace-1",
        agent_run_id="run-1",
        research_session_ids=("session-1", "session-2"),
        claim_ids=("claim-1",),
        evidence_ids=("evidence-1",),
        durable_state_refs=("agent-run:run-1",),
    )
    episode = EpisodicMemoryRecord(
        memory_id=EpisodicMemoryRecord.canonical_memory_id("run-1"),
        workspace_id="workspace-1",
        agent_run_id="run-1",
        user_goal_raw="Preserve this exact user goal.",
        goal_summary="Understand the topic.",
        research_summary="Two sessions completed the research.",
        important_claim_ids=("claim-1",),
        useful_queries=("useful query",),
        failed_or_unhelpful_queries=("failed query",),
        unresolved_summary="One question remains.",
        final_outcome="completed",
        provenance=provenance,
        created_at=datetime.now(timezone.utc),
    )
    assert episode.canonical_episode_key == ("workspace-1", "run-1")
    assert episode.provenance.research_session_ids == ("session-1", "session-2")
    assert episode.is_authoritative_evidence is False

    with pytest.raises(ValidationError):
        EpisodicMemoryRecord.model_validate(
            {**episode.model_dump(), "is_authoritative_evidence": True}
        )
