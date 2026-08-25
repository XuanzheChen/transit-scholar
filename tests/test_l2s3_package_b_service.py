from __future__ import annotations

from datetime import UTC

import pytest

from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.wiki import (
    PaperMetadata,
    WikiNotFoundError,
    WikiService,
    WikiStore,
    WikiValidationError,
    WikiWorkspaceMismatchError,
    WorkspaceContext,
)


def _service(root, workspace="ws"):
    context = WorkspaceContext(workspace_id=workspace, schema_id="schema", schema_version="1", paper_ids=["p1", "p2"])
    return WikiService(context, WikiStore(context, root))


def test_maintenance_relationships_and_lexical_freshness(project_tmp_path):
    service = _service(project_tmp_path)
    first = service.ensure_paper_page(PaperMetadata(paper_id="p1", title="Transit signal priority"))
    second = service.ensure_paper_page(PaperMetadata(paper_id="p2", title="Bus corridor"))
    assert service.ensure_paper_page(PaperMetadata(paper_id="p1", title="Changed title")) == first
    service.update_page_summary(first.page_id, "adaptive corridor control")
    entity = service.create_entity("Signal Priority", description="adaptive control", aliases=["SP"])
    assert service.create_entity(" signal   priority ").entity_id == entity.entity_id
    link = service.link_page_entity(first.page_id, entity.entity_id, source_field_id="field", source_status="explicit")
    service.link_page_entity(second.page_id, entity.entity_id, source_field_id="field", source_status="explicit")
    assert [hit.object_id for hit in service.search_pages("adaptive").hits] == [first.page_id]
    assert service.search_entities("sp").hits[0].object_id == entity.entity_id
    assert service.find_related_pages(first.page_id)[0].page.page_id == second.page_id
    assert service.unlink_page_entity(link.link_id).link_id == link.link_id
    assert service.list_page_entities(first.page_id).entities == []


def test_workspace_isolation_and_degraded_semantic_search(project_tmp_path):
    first, second = _service(project_tmp_path, "one"), _service(project_tmp_path, "two")
    page = first.ensure_paper_page(PaperMetadata(paper_id="p1", title="one title"))
    second.ensure_paper_page(PaperMetadata(paper_id="p1", title="two title"))
    assert first.search_wiki("two").hits == []
    result = first.search_pages("one", mode="semantic")
    assert result.status == "degraded" and result.error_code == "embedding_unavailable"
    assert first.get_page(page.page_id).workspace_id == "one"


class _Embedding(EmbeddingProvider):
    available = True
    reason = None
    info = ProviderInfo(provider="fake", model="fake", dimension=2)

    def dimension(self):
        return 2

    def embed_documents(self, texts):
        return [[1.0, 0.0] if "signal" in text.casefold() else [0.0, 1.0] for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class _FailingEmbedding(_Embedding):
    def embed_documents(self, texts):
        raise RuntimeError("provider failure TOKEN_SENTINEL_987")


def test_public_boundaries_validation_and_semantic_failure(project_tmp_path):
    service = _service(project_tmp_path)
    page = service.ensure_paper_page(PaperMetadata(paper_id="p1", title="Signal study"))
    entity = service.create_entity("Signal Priority", aliases=["SP"], description="priority")
    link = service.link_page_entity(page, entity, source_field_id="field", source_status="explicit")
    assert service.search_wiki("signal", mode="semantic").status == "degraded"
    semantic = WikiService(service.context, service.store, _Embedding()).search_wiki("signal", mode="semantic")
    assert semantic.status == "ok"
    assert semantic.hits[0].retrieval_mode == "semantic"
    failed = WikiService(service.context, service.store, _FailingEmbedding()).search_wiki("signal", mode="semantic")
    assert failed.status == "error" and failed.error_code == "embedding_provider_failure"
    assert failed.hits and "TOKEN_SENTINEL_987" not in failed.model_dump_json()
    assert service.add_entity_alias(entity.entity_id, " signal priority ").entity_id == entity.entity_id
    assert service.add_entity_alias(entity.entity_id, "  sP  ").aliases == ["SP"]
    assert service.unlink_page_entity(link).status == "removed"
    with pytest.raises(WikiNotFoundError):
        service.unlink_page_entity(link.link_id)
    for invalid in ("", " "):
        with pytest.raises(WikiValidationError):
            service.search_pages(invalid)
    with pytest.raises(WikiValidationError):
        service.search_pages("signal", mode="unknown")
    with pytest.raises(WikiValidationError):
        service.search_pages("signal", limit=0)


def test_foreign_models_are_rejected_before_mutation(project_tmp_path):
    first, second = _service(project_tmp_path, "one"), _service(project_tmp_path, "two")
    foreign_page = second.ensure_paper_page(PaperMetadata(paper_id="p1", title="foreign"))
    foreign_entity = second.create_entity("foreign entity")
    before = {name: value["sha256"] for name, value in first.store.read_raw_snapshot().items()}
    with pytest.raises(WikiWorkspaceMismatchError) as error:
        first.link_page_entity(foreign_page, foreign_entity, source_field_id="field", source_status="explicit")
    assert error.value.code == "workspace_mismatch"
    with pytest.raises(WikiNotFoundError):
        first.get_page(foreign_page.page_id)
    assert {name: value["sha256"] for name, value in first.store.read_raw_snapshot().items()} == before


def test_identity_and_utc_update_semantics(project_tmp_path):
    service = _service(project_tmp_path)
    page = service.ensure_paper_page(PaperMetadata(paper_id="p1", title="Title"))
    updated = service.update_page_summary(page.page_id, "fresh summary")
    assert updated.page_id == page.page_id
    assert updated.paper_id == page.paper_id
    assert updated.schema_id == page.schema_id
    assert updated.build_revision == page.build_revision + 1
    assert updated.created_at == page.created_at
    assert updated.updated_at.tzinfo == UTC
    entity = service.create_entity("Unified Name", aliases=["Alias"])
    changed = service.update_entity(entity.entity_id, description="changed", kind="method")
    assert changed.entity_id == entity.entity_id and changed.created_at == entity.created_at
    assert service.search_entities("changed").hits[0].object_id == entity.entity_id


def test_entity_updates_revalidate_aliases_before_persisting(project_tmp_path):
    service = _service(project_tmp_path)
    entity = service.create_entity("Unified Name", aliases=["Original Alias"])
    updated = service.update_entity(
        entity.entity_id,
        aliases=[" unified   name ", " Alias ", "alias", ""],
    )
    assert updated.entity_id == entity.entity_id
    assert updated.created_at == entity.created_at
    assert updated.aliases == ["Alias"]
    assert service.add_entity_alias(entity.entity_id, " unified name ").aliases == ["Alias"]
    with pytest.raises(WikiValidationError) as error:
        service.update_entity(entity.entity_id, aliases=["safe", 1])
    assert error.value.code == "invalid_input"
    assert service.get_entity(entity.entity_id).aliases == ["Alias"]
