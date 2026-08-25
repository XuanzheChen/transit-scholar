import os

import pytest

from transit_scholar.layer2.wiki import (
    PageEntityLink,
    WikiCorruptionError,
    WikiEntity,
    WikiManifest,
    WikiNotInitializedError,
    WikiReferentialIntegrityError,
    WikiStore,
    WikiStoreError,
    WikiPage,
    WorkspaceContext,
    entity_id_for,
    link_id_for,
    page_id_for,
)
import transit_scholar.layer2.wiki.store as store_module


def _context(workspace="ws"):
    return WorkspaceContext(workspace_id=workspace, schema_id="schema", schema_version="1", paper_ids=["p1", "p2"])


def _page(workspace="ws", paper="p1", title="Title"):
    return WikiPage(page_id=page_id_for(workspace, paper), workspace_id=workspace, paper_id=paper, title=title, schema_id="schema", schema_version="1")


def _entity(workspace="ws", name="Entity"):
    return WikiEntity(entity_id=entity_id_for(workspace, name), workspace_id=workspace, canonical_name=name)


def _link(page, entity):
    return PageEntityLink(link_id=link_id_for(page.workspace_id, page.page_id, entity.entity_id, "f1", "explicit", "schema", "1"), workspace_id=page.workspace_id, page_id=page.page_id, entity_id=entity.entity_id, paper_id=page.paper_id, schema_id="schema", schema_version="1", source_field_id="f1", source_status="explicit")


def test_bootstrap_crud_reload_and_layout(project_tmp_path):
    store = WikiStore(_context(), project_tmp_path)
    with pytest.raises(WikiNotInitializedError):
        store.list_pages()
    page, entity = _page(), _entity()
    assert store.create_page(page) == page
    assert store.create_page(page) == page
    assert store.create_entity(entity) == entity
    link = store.create_link(_link(page, entity))
    assert store.create_link(link) == link
    assert store.root == project_tmp_path / "ws" / "wiki"
    assert all(path.is_file() for path in (store.manifest_path, store.pages_path, store.entities_path, store.links_path))
    assert store.index_path.is_dir()
    reload = WikiStore(_context(), project_tmp_path)
    assert reload.get_page(page.page_id) == page
    assert reload.get_entity(entity.entity_id) == entity
    assert reload.list_links() == [link]
    assert reload.get_link(link.link_id) == link
    assert reload.get_manifest().paper_ids == ["p1", "p2"]


def test_integrity_isolation_and_identity_updates(project_tmp_path):
    first, second = WikiStore(_context("one"), project_tmp_path), WikiStore(_context("two"), project_tmp_path)
    first_page, first_entity = _page("one"), _entity("one")
    second_page, second_entity = _page("two"), _entity("two")
    first.create_page(first_page); first.create_entity(first_entity)
    second.create_page(second_page); second.create_entity(second_entity)
    assert first_entity.entity_id != second_entity.entity_id
    with pytest.raises(WikiReferentialIntegrityError):
        first.create_link(_link(first_page, second_entity))
    changed = first.update_entity(first_entity.model_copy(update={"description": "updated"}))
    assert changed.created_at == first_entity.created_at
    with pytest.raises(Exception):
        first.update_entity(first_entity.model_copy(update={"canonical_name": "Different"}))


def test_corruption_and_atomic_failure_leave_prior_snapshot(project_tmp_path, monkeypatch):
    store = WikiStore(_context(), project_tmp_path)
    store.create_page(_page())
    before = {path.name: path.read_bytes() for path in (store.manifest_path, store.pages_path, store.entities_path, store.links_path)}
    real_replace = os.replace
    def fail_replace(source, target):
        raise OSError("injected")
    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    with pytest.raises(WikiStoreError):
        store.create_entity(_entity())
    monkeypatch.setattr(store_module.os, "replace", real_replace)
    assert {path.name: path.read_bytes() for path in (store.manifest_path, store.pages_path, store.entities_path, store.links_path)} == before
    assert WikiStore(_context(), project_tmp_path).list_pages()
    store.pages_path.write_text("{bad}\n", encoding="utf-8")
    with pytest.raises(WikiCorruptionError):
        WikiStore(_context(), project_tmp_path).list_pages()
