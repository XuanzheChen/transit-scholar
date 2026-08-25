from __future__ import annotations

import json

from transit_scholar.layer2.wiki import PaperMetadata, WikiService, WikiStore, WorkspaceContext


def test_explicit_index_rebuild_and_read_only_audit(project_tmp_path):
    context = WorkspaceContext(workspace_id="ws", schema_id="schema", schema_version="1", paper_ids=["p1"])
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)
    page = service.ensure_paper_page(PaperMetadata(paper_id="p1", title="Index title"))
    source_before = {name: path.read_bytes() for name, path in store.snapshot_paths.items()}
    report = service.audit_wiki()
    assert any(issue.code == "index_missing" for issue in report.issues)
    assert {name: path.read_bytes() for name, path in store.snapshot_paths.items()} == source_before
    rebuilt = service.rebuild_indexes()
    assert rebuilt.status == "rebuilt"
    assert (store.index_path / "package_b_index.json").is_file()
    assert service.search_pages("index").hits[0].object_id == page.page_id


def test_new_service_rebuilds_view_from_source(project_tmp_path):
    context = WorkspaceContext(workspace_id="ws", schema_id="schema", schema_version="1", paper_ids=["p1"])
    first = WikiService(context, WikiStore(context, project_tmp_path))
    page = first.ensure_paper_page(PaperMetadata(paper_id="p1", title="Reload title"))
    second = WikiService(context, WikiStore(context, project_tmp_path))
    assert second.search_pages("reload").hits[0].object_id == page.page_id


def test_audit_reports_corrupt_index_without_writing_any_asset(project_tmp_path):
    context = WorkspaceContext(workspace_id="ws", schema_id="schema", schema_version="1", paper_ids=["p1"])
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)
    service.ensure_paper_page(PaperMetadata(paper_id="p1", title="Index title"))
    service.rebuild_indexes()
    index = store.index_path / "package_b_index.json"
    index.write_bytes(b'{"index_version":1,"source_fingerprint":"wrong","pages":[],"entities":[],"links":[]}\n')
    paths = [*store.snapshot_paths.values(), index]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    report = service.audit_wiki()
    assert any(issue.code == "index_stale" for issue in report.issues)
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before


def test_index_projection_damage_never_controls_source_search(project_tmp_path):
    context = WorkspaceContext(workspace_id="ws", schema_id="schema", schema_version="1", paper_ids=["p1"])
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)
    page = service.ensure_paper_page(PaperMetadata(paper_id="p1", title="Source derived title"))
    service.rebuild_indexes()
    index = store.index_path / "package_b_index.json"
    payload = json.loads(index.read_text(encoding="utf-8"))
    payload["pages"] = []
    index.write_text(json.dumps(payload), encoding="utf-8")
    report = service.audit_wiki()
    assert any(issue.code == "index_corrupt" for issue in report.issues)
    assert service.search_pages("derived").hits[0].object_id == page.page_id


def test_source_fingerprint_invalidates_a_live_store_view(project_tmp_path):
    context = WorkspaceContext(workspace_id="ws", schema_id="schema", schema_version="1", paper_ids=["p1"])
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)
    page = service.ensure_paper_page(PaperMetadata(paper_id="p1", title="Original title"))
    store.pages_path.write_bytes(store.pages_path.read_bytes().replace(b'"summary":""', b'"summary":"external freshness"'))
    assert service.search_pages("freshness").hits[0].object_id == page.page_id
