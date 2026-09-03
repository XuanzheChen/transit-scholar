from datetime import datetime, timezone

from transit_scholar.layer3.memory.models import EpisodicMemoryProvenance, EpisodicMemoryRecord
from transit_scholar.layer3.memory.retrieval import EpisodicMemoryRetriever, EpisodicMemoryStore


def episode(workspace, run, text):
    return EpisodicMemoryRecord(
        memory_id=f"m-{run}", workspace_id=workspace, agent_run_id=run,
        user_goal_raw=text, goal_summary=text, research_summary=text,
        unresolved_summary="", final_outcome="done",
        provenance=EpisodicMemoryProvenance(workspace_id=workspace, agent_run_id=run),
        created_at=datetime.now(timezone.utc),
    )


def test_retrieval_is_workspace_scoped_and_bounded():
    store = EpisodicMemoryStore([episode("a", "1", "quantum transport"), episode("a", "2", "classical transit"), episode("b", "3", "quantum transport")])
    results = EpisodicMemoryRetriever(store).retrieve(workspace_id="a", query="quantum", top_k=1)
    assert len(results) == 1
    assert results[0].workspace_id == "a"
    assert results[0].source_kind == "episodic_memory"
    assert results[0].auxiliary is True
