from datetime import datetime, timezone

from transit_scholar.layer3.memory import (
    EpisodicMemoryProvenance,
    EpisodicMemoryRecord,
    EpisodicMemoryRetriever,
    EpisodicMemoryStore,
)


def _episode(workspace: str, run: str, text: str) -> EpisodicMemoryRecord:
    return EpisodicMemoryRecord(
        memory_id=f"m-{run}", workspace_id=workspace, agent_run_id=run,
        user_goal_raw=text, goal_summary=text, research_summary=text,
        unresolved_summary="", final_outcome="done", created_at=datetime.now(timezone.utc),
        provenance=EpisodicMemoryProvenance(workspace_id=workspace, agent_run_id=run),
    )


def test_retrieval_filters_workspace_and_bounds_top_k():
    store = EpisodicMemoryStore([
        _episode("workspace-a", "run-1", "quantum transport"),
        _episode("workspace-a", "run-2", "quantum transit"),
        _episode("workspace-b", "run-3", "quantum transport"),
    ])
    results = EpisodicMemoryRetriever(store).retrieve(
        workspace_id="workspace-a", query="quantum", top_k=1
    )
    assert len(results) == 1
    assert results[0].workspace_id == "workspace-a"
    assert results[0].source_kind == "episodic_memory"
    assert results[0].auxiliary is True
