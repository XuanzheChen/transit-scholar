from types import SimpleNamespace

from transit_scholar.layer2.wiki.models import WikiSearchHit, WikiSearchResult
from transit_scholar.layer3.agentic_wiki import AgenticWikiStore
from transit_scholar.layer3.knowledge_evolution import AgenticWikiEntry
from transit_scholar.layer3.wiki.models import WorkspaceWikiStatus
from transit_scholar.layer3.wiki.service import WorkspaceWikiService


def _entry(entry_id, status="active"):
    return AgenticWikiEntry(
        entry_id=entry_id, workspace_id="ws", title="Agentic topic",
        content="agentic content", originating_agent_run_id="run", status=status,
    )


def test_search_merges_sources_preserves_identity_and_limit(monkeypatch):
    store = AgenticWikiStore()
    store.put(_entry("a1"))
    service = WorkspaceWikiService.__new__(WorkspaceWikiService)
    service.data_root = None
    service.agentic_wiki_store = store
    service.workspaces = SimpleNamespace(
        get=lambda _: SimpleNamespace(schema_mode="bound", schema_binding=object()),
        list_memberships=lambda _: [],
    )
    service.status = lambda _: WorkspaceWikiStatus(workspace_id="ws", status="ready")
    monkeypatch.setattr("transit_scholar.layer3.wiki.service.derive_workspace_context", lambda *a: object())
    monkeypatch.setattr("transit_scholar.layer3.wiki.service.workspace_layout", lambda *a, **k: SimpleNamespace(wiki_store=lambda _: object()))
    monkeypatch.setattr("transit_scholar.layer2.wiki.service.WikiService", lambda *a: SimpleNamespace(
        search_wiki=lambda *a, **k: WikiSearchResult(hits=[WikiSearchHit(type="page", object_id="b1", title="Base", snippet="base", score=0.9, retrieval_mode="lexical")])
    ))
    result = service.search("ws", "base", limit=1)
    assert len(result.hits) == 1
    assert result.hits[0].source_kind == "base_wiki"
    result = service.search("ws", "agentic", limit=5)
    assert {hit.source_kind for hit in result.hits} == {"base_wiki", "agentic_wiki"}


def test_stale_agentic_entries_are_degraded_and_superseded_excluded(monkeypatch):
    store = AgenticWikiStore()
    store.put(_entry("stale", "stale"))
    store.put(_entry("sup", "superseded"))
    service = WorkspaceWikiService.__new__(WorkspaceWikiService)
    service.data_root = None
    service.agentic_wiki_store = store
    service.workspaces = SimpleNamespace(get=lambda _: SimpleNamespace(schema_mode="bound", schema_binding=object()), list_memberships=lambda _: [])
    service.status = lambda _: WorkspaceWikiStatus(workspace_id="ws", status="ready")
    monkeypatch.setattr("transit_scholar.layer3.wiki.service.derive_workspace_context", lambda *a: object())
    monkeypatch.setattr("transit_scholar.layer3.wiki.service.workspace_layout", lambda *a, **k: SimpleNamespace(wiki_store=lambda _: object()))
    monkeypatch.setattr("transit_scholar.layer2.wiki.service.WikiService", lambda *a: SimpleNamespace(search_wiki=lambda *a, **k: WikiSearchResult()))
    result = service.search("ws", "agentic", include_stale=True)
    assert [h.object_id for h in result.hits] == ["stale"]
    assert result.status == "degraded"
    assert result.hits[0].lifecycle_status == "stale"
