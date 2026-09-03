import pytest

from transit_scholar.layer3.agentic_wiki import AgenticWikiStore
from transit_scholar.layer3.knowledge_evolution import AgenticWikiEntry


def entry(workspace="ws", entry_id="e1", status="active"):
    return AgenticWikiEntry(
        entry_id=entry_id, workspace_id=workspace, title="Topic", content="Synthesized",
        source_claim_ids=("c1",), evidence_refs=("ev1",), originating_agent_run_id="run1", status=status,
    )


def test_provenance_round_trip_and_lifecycle_retrieval():
    store = AgenticWikiStore()
    stored = store.put(entry())
    assert store.get("e1", "ws").model_dump() == stored.model_dump()
    store.put(entry(entry_id="stale", status="stale"))
    store.put(entry(entry_id="sup", status="superseded"))
    assert {item.entry_id for item in store.list("ws")} == {"e1"}
    assert {item.entry_id for item in store.list("ws", include_stale=True)} == {"e1", "stale"}


def test_maintenance_marks_unresolvable_provenance_stale():
    store = AgenticWikiStore()
    store.put(entry())
    changed = store.maintain("ws", claims={"other"}, evidence={"ev1"})
    assert changed[0].status == "stale"
    assert store.list("ws") == []


def test_cross_workspace_read_write_rejected_and_delete_isolated():
    store = AgenticWikiStore()
    store.put(entry("ws-a"))
    with pytest.raises(PermissionError):
        store.get("e1", "ws-b")
    with pytest.raises(PermissionError):
        store.put(entry("ws-a"), workspace_id="ws-b")
    store.put(entry("ws-b", entry_id="e2"))
    store.delete_workspace("ws-a")
    with pytest.raises(PermissionError):
        store.get("e1", "ws-a")
    assert store.get("e2", "ws-b").workspace_id == "ws-b"
