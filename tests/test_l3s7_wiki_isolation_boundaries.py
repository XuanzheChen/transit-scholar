import pytest

from transit_scholar.layer3.agentic_wiki import AgenticWikiStore
from transit_scholar.layer3.knowledge_evolution import AgenticWikiEntry


def _entry(workspace_id):
    return AgenticWikiEntry(
        entry_id=f"entry-{workspace_id}", workspace_id=workspace_id,
        title="Knowledge", content="Evidence-backed knowledge",
        originating_agent_run_id="run-1",
    )


def test_agentic_store_workspace_isolation_and_delete():
    store = AgenticWikiStore()
    store.put(_entry("workspace-a"))
    store.put(_entry("workspace-b"))
    assert [item.workspace_id for item in store.list("workspace-a")] == ["workspace-a"]
    with pytest.raises(PermissionError):
        store.get("entry-workspace-a", "workspace-b")
    store.delete_workspace("workspace-a")
    assert store.list("workspace-a") == []
    assert len(store.list("workspace-b")) == 1


def test_episodic_memory_is_not_an_agentic_wiki_result():
    store = AgenticWikiStore()
    store.put(_entry("workspace-a"))
    result_kinds = {"agentic_wiki" for _ in store.list("workspace-a")}
    assert "episodic_memory" not in result_kinds
