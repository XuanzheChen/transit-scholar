from __future__ import annotations

import json

import pytest

import transit_scholar.layer2.wiki.application as wiki_application
from transit_scholar.layer2.wiki.builder import PageTrace, PaperWikiBuildResult, WorkspaceWikiBuildResult

from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.schema_extraction import FieldDefinition, FieldResult, SchemaDefinition, SchemaInstance, SectionDefinition
from transit_scholar.layer2.wiki import (
    PaperMetadata,
    WikiBuildInputError,
    WikiService,
    WikiStore,
    WorkspaceContext,
    WorkspaceWikiBuildService,
    create_production_wiki_composition,
)


class _Client:
    is_fake = False
    provider_name = "test"
    model_name = "test"

    def generate_structured(self, messages, output_schema, metadata=None):
        return output_schema.model_validate({"proposals": []})


class _Embedding(EmbeddingProvider):
    available = True
    reason = None
    info = ProviderInfo(provider="test", model="test", dimension=2)

    def dimension(self):
        return 2

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


def _definition():
    return SchemaDefinition(
        schema_id="schema", version="1",
        sections=[SectionDefinition(id="section", label="Section", fields=[FieldDefinition(id="name", label="Name", question="Name", type="string")])],
    )


def _instance(paper_id: str):
    return SchemaInstance(
        paper_id=paper_id, schema_id="schema", schema_version="1",
        fields={"name": FieldResult(value=f"Signal {paper_id}", status="explicit")},
    )


def _metadata(paper_id: str):
    return PaperMetadata(paper_id=paper_id, title=f"Paper {paper_id}", authors=["Author"], year=2024)


def _service(project_tmp_path, instances, metadata, *, composition_calls=None, embedding_provider=None, llm_client=None):
    def composition(context, store):
        if composition_calls is not None:
            composition_calls.append(context.workspace_id)
        return create_production_wiki_composition(
            context, store, llm_client=llm_client or _Client(), embedding_provider=embedding_provider or _Embedding()
        )

    return WorkspaceWikiBuildService(
        schema_definition_loader=lambda _: _definition(),
        schema_instance_loader=lambda paper_id, _: instances.get(paper_id),
        paper_metadata_loader=lambda paper_id: metadata.get(paper_id),
        composition_factory=composition,
        wiki_storage_root=project_tmp_path,
    )


class _UnavailableEmbedding(_Embedding):
    available = False


class _FailingEmbedding(_Embedding):
    def embed_documents(self, texts):
        raise RuntimeError("provider failed")


class _WrongCountEmbedding(_Embedding):
    def embed_documents(self, texts):
        return []


class _WrongDimensionEmbedding(_Embedding):
    def embed_documents(self, texts):
        return [[1.0] for _ in texts]


class _EntityClient(_Client):
    def generate_structured(self, messages, output_schema, metadata=None):
        return output_schema.model_validate({"proposals": [{
            "canonical_name": "Signal",
            "description": "A measured signal",
            "source_field_id": "name",
            "confidence": 0.9,
        }]})


def test_application_service_loads_authoritative_inputs_and_finalizes_build(project_tmp_path):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1", "p2"])
    instances = {paper_id: _instance(paper_id) for paper_id in context.paper_ids}
    metadata = {paper_id: _metadata(paper_id) for paper_id in context.paper_ids}
    service = _service(project_tmp_path, instances, metadata)

    inputs = service.load_build_inputs(context)
    result = service.build_wiki_for_workspace(context)

    assert list(inputs.instances_by_paper) == context.paper_ids
    assert list(inputs.metadata_by_paper) == context.paper_ids
    assert result.build.status == "complete"
    assert result.manifest.build_status == "complete"
    assert result.index.index_version
    assert result.audit.ok
    assert result.index.source_fingerprint == result.audit.source_fingerprint


def test_application_service_finalization_order(project_tmp_path):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1"])
    events = []

    class RecordingStore(WikiStore):
        def upsert_manifest(self, manifest):
            events.append("manifest")
            return super().upsert_manifest(manifest)

    def composition(context, store):
        composed = create_production_wiki_composition(context, store, llm_client=_Client(), embedding_provider=_Embedding())
        rebuild = composed.service.rebuild_indexes
        audit = composed.service.audit_wiki
        composed.service.rebuild_indexes = lambda: (events.append("index") or rebuild())
        composed.service.audit_wiki = lambda: (events.append("audit") or audit())
        return composed

    service = WorkspaceWikiBuildService(
        schema_definition_loader=lambda _: _definition(),
        schema_instance_loader=lambda paper_id, _: _instance(paper_id),
        paper_metadata_loader=lambda paper_id: _metadata(paper_id),
        store_factory=lambda context: RecordingStore(context, project_tmp_path),
        composition_factory=composition,
    )
    result = service.build_wiki_for_workspace(context)
    assert result.manifest.build_status == "complete"
    assert events[-3:] == ["manifest", "index", "audit"]


def test_application_service_mixed_papers_never_persists_complete_manifest(project_tmp_path, monkeypatch):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1", "p2"])
    complete = PaperWikiBuildResult(
        status="complete", paper_id="p1",
        page=PageTrace(paper_id="p1", schema_id="schema", schema_version="1", build_status="complete"),
    )
    incomplete = complete.model_copy(update={"status": "incomplete", "paper_id": "p2"})
    mixed = WorkspaceWikiBuildResult(
        status="complete", workspace_id=context.workspace_id, schema_id=context.schema_id,
        schema_version=context.schema_version, papers=(complete, incomplete),
        complete_count=2, incomplete_count=0, failed_count=0,
    )
    monkeypatch.setattr(wiki_application, "build_wiki_for_workspace", lambda *args, **kwargs: mixed)
    service = _service(project_tmp_path, {"p1": _instance("p1"), "p2": _instance("p2")}, {"p1": _metadata("p1"), "p2": _metadata("p2")})
    result = service.build_wiki_for_workspace(context)
    assert result.manifest.build_status == "partial"


def test_application_service_rejects_schema_mismatch_before_composition(project_tmp_path):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1"])
    calls = []
    service = _service(project_tmp_path, {"p1": _instance("p1")}, {"p1": _metadata("p1")}, composition_calls=calls)
    service.schema_definition_loader = lambda _: _definition().model_copy(update={"version": "2"})

    with pytest.raises(WikiBuildInputError, match="schema definition") as error:
        service.build_wiki_for_workspace(context)

    assert error.value.code == "schema_mismatch"
    assert calls == []
    assert not (project_tmp_path / context.workspace_id / "wiki").exists()


def test_application_service_rejects_missing_and_foreign_papers_before_composition(project_tmp_path):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1", "p2"])
    calls = []
    service = _service(project_tmp_path, {"p1": _instance("p1")}, {"p1": _metadata("p1")}, composition_calls=calls)

    with pytest.raises(WikiBuildInputError) as missing:
        service.build_wiki_for_workspace(context)

    assert missing.value.code == "missing_input"
    assert calls == []

    foreign = _service(project_tmp_path, {"p1": _instance("other"), "p2": _instance("p2")}, {"p1": _metadata("p1"), "p2": _metadata("p2")}, composition_calls=calls)
    with pytest.raises(WikiBuildInputError) as mismatch:
        foreign.build_wiki_for_workspace(context)

    assert mismatch.value.code == "paper_mismatch"
    assert calls == []


@pytest.mark.parametrize(
    ("provider", "error_code"),
    [
        (_UnavailableEmbedding(), "embedding_unavailable"),
        (_FailingEmbedding(), "embedding_provider_failure"),
        (_WrongCountEmbedding(), "embedding_provider_failure"),
        (_WrongDimensionEmbedding(), "embedding_provider_failure"),
    ],
)
def test_production_finalization_blocks_mandatory_vector_failures(project_tmp_path, provider, error_code):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1"])
    service = _service(project_tmp_path, {"p1": _instance("p1")}, {"p1": _metadata("p1")}, embedding_provider=provider)

    result = service.build_wiki_for_workspace(context)

    assert result.manifest.build_status != "complete"
    if error_code == "embedding_unavailable":
        assert result.index.status == "rebuilt"
        assert any(issue.code == error_code for issue in result.audit.issues)
    else:
        assert result.index.error_code == error_code
    persisted = json.loads((project_tmp_path / context.workspace_id / "wiki" / "manifest.json").read_text())
    assert persisted["build_status"] != "complete"


def test_vector_audit_reports_missing_stale_and_incompatible_state(project_tmp_path):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1"])
    application = _service(project_tmp_path, {"p1": _instance("p1")}, {"p1": _metadata("p1")})
    application.build_wiki_for_workspace(context)
    store = WikiStore(context, project_tmp_path)
    wiki = WikiService(context, store, _Embedding())
    index = store.index_path / "package_b_index.json"
    payload = json.loads(index.read_text())

    payload.pop("vector_metadata")
    index.write_text(json.dumps(payload))
    assert any(issue.code == "vector_index_missing" for issue in wiki.audit_wiki().issues)

    payload = json.loads(index.read_text())
    payload["vector_metadata"] = {"provider": "wrong", "model": "wrong", "revision": "wrong", "dimension": 99, "implementation": "wrong"}
    payload["source_fingerprint"] = "stale"
    index.write_text(json.dumps(payload))
    codes = {issue.code for issue in wiki.audit_wiki().issues}
    assert {"vector_index_stale", "vector_index_incompatible"}.issubset(codes)


def test_valid_vector_audit_requires_full_page_and_entity_coverage(project_tmp_path):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1"])
    store = WikiStore(context, project_tmp_path)
    wiki = WikiService(context, store, _Embedding())
    page = wiki.ensure_paper_page(_metadata("p1"))
    entity = wiki.create_entity("Signal", description="A measured signal")
    wiki.link_page_entity(page, entity, source_field_id="name", source_status="explicit")
    wiki.rebuild_indexes()

    assert not {"vector_index_missing", "vector_index_stale", "vector_index_incompatible"}.intersection(
        issue.code for issue in wiki.audit_wiki().issues
    )
    payload = json.loads((store.index_path / "package_b_index.json").read_text())
    assert {(item["kind"], item["object_id"]) for item in payload["vectors"]} == {
        ("page", page.page_id), ("entity", entity.entity_id)
    }
    payload["vectors"] = [item for item in payload["vectors"] if item["kind"] != "entity"]
    (store.index_path / "package_b_index.json").write_text(json.dumps(payload))
    assert any(issue.code == "vector_index_missing" for issue in wiki.audit_wiki().issues)


def test_production_complete_manifest_has_page_and_entity_vectors(project_tmp_path):
    context = WorkspaceContext(workspace_id="workspace", schema_id="schema", schema_version="1", paper_ids=["p1"])
    application = _service(
        project_tmp_path, {"p1": _instance("p1")}, {"p1": _metadata("p1")}, llm_client=_EntityClient()
    )

    result = application.build_wiki_for_workspace(context)

    assert result.manifest.build_status == "complete"
    payload = json.loads((project_tmp_path / context.workspace_id / "wiki" / "index" / "package_b_index.json").read_text())
    assert {item["kind"] for item in payload["vectors"]} == {"page", "entity"}
    assert len(payload["vectors"]) == 2


def test_application_service_keeps_workspace_builds_isolated(project_tmp_path):
    first = WorkspaceContext(workspace_id="first", schema_id="schema", schema_version="1", paper_ids=["p1"])
    second = WorkspaceContext(workspace_id="second", schema_id="schema", schema_version="1", paper_ids=["p2"])
    instances = {"p1": _instance("p1"), "p2": _instance("p2")}
    metadata = {"p1": _metadata("p1"), "p2": _metadata("p2")}
    service = _service(project_tmp_path, instances, metadata)

    service.build_wiki_for_workspace(first)
    service.build_wiki_for_workspace(second)

    first_wiki = WikiService(first, WikiStore(first, project_tmp_path), _Embedding())
    second_wiki = WikiService(second, WikiStore(second, project_tmp_path), _Embedding())
    assert [hit.object_id for hit in first_wiki.search_pages("p1").hits] == [
        WikiStore(first, project_tmp_path).list_pages()[0].page_id
    ]
    assert second_wiki.search_pages("p1").hits == []
