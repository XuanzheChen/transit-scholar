from transit_scholar.layer3.agentic_wiki import AgenticWikiMaintenance, AgenticWikiStore
from transit_scholar.layer3.knowledge_evolution.models import AgenticWikiEntry


def entry(**kw):
    data = dict(entry_id="e1", workspace_id="w1", title="T", content="C",
                originating_agent_run_id="r1", source_claim_ids=("c1",), evidence_refs=("ev1",))
    data.update(kw)
    return AgenticWikiEntry(**data)


def test_missing_paper_marks_stale_without_delete():
    store = AgenticWikiStore(); store.put(entry())
    changed = AgenticWikiMaintenance(store, claims=["c1"], evidence=[{"id": "ev1", "paper_id": "p1"}], papers=[])("w1")
    assert changed and store.get("e1", "w1").status == "stale"


def test_unresolvable_provenance_and_rejected_claim_stale():
    store = AgenticWikiStore(); store.put(entry())
    AgenticWikiMaintenance(store, claims=[{"id": "c1", "status": "rejected"}], evidence=[{"id": "ev1", "provenance_resolvable": False}])("w1")
    assert store.get("e1", "w1").status == "stale"


def test_invalid_version_and_supersession_stale_and_isolation():
    store = AgenticWikiStore(); store.put(entry(evidence_refs=("ev1",)))
    AgenticWikiMaintenance(store, evidence=[{"id": "ev1", "source_version_valid": False}])("w1")
    assert store.get("e1", "w1").status == "stale"
    store.put(entry(entry_id="e2", workspace_id="w2"))
    assert [e.entry_id for e in store.list("w1")] == []
