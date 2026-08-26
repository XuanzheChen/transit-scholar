from __future__ import annotations

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


def _service(project_tmp_path, instances, metadata, *, composition_calls=None):
    def composition(context, store):
        if composition_calls is not None:
            composition_calls.append(context.workspace_id)
        return create_production_wiki_composition(
            context, store, llm_client=_Client(), embedding_provider=_Embedding()
        )

    return WorkspaceWikiBuildService(
        schema_definition_loader=lambda _: _definition(),
        schema_instance_loader=lambda paper_id, _: instances.get(paper_id),
        paper_metadata_loader=lambda paper_id: metadata.get(paper_id),
        composition_factory=composition,
        wiki_storage_root=project_tmp_path,
    )


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
