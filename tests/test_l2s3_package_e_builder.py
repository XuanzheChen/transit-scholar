from transit_scholar.layer2.schema_extraction import FieldDefinition, FieldResult, SchemaDefinition, SchemaInstance, SectionDefinition
from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.wiki import (
    EntityProposal, EntityProposalRunner, EntityResolver, EntityResolutionDecision, EntityResolutionResult,
    PaperMetadata, WikiEntity, WikiManifest, WikiService, WikiStore,
    WorkspaceContext, build_wiki_for_paper, build_wiki_for_workspace,
)


def _inputs():
    context = WorkspaceContext(workspace_id="e-ws", schema_id="generic", schema_version="1", paper_ids=["p1", "p2"])
    definition = SchemaDefinition(schema_id="generic", version="1", sections=[SectionDefinition(id="s", label="S", fields=[FieldDefinition(id="name", label="Name", question="Name", type="string")])])
    instance = SchemaInstance(paper_id="p1", schema_id="generic", schema_version="1", fields={"name": FieldResult(value="Signal", status="explicit")})
    metadata = PaperMetadata(paper_id="p1", title="A paper", authors=["A"], year=2024)
    return context, definition, instance, metadata


class _Resolver:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def resolve(self, proposal):
        self.calls.append(proposal.canonical_name)
        return EntityResolutionResult(decision="ambiguous", reason_code="test_ambiguous", proposal=proposal)


def test_paper_compiler_is_bounded_and_serializable(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    service = WikiService(context, WikiStore(context, project_tmp_path))
    runner = EntityProposalRunner(lambda request: {"proposals": []})
    resolver = _Resolver(context)
    result = build_wiki_for_paper(context, definition, instance, metadata, service, runner, resolver)
    assert result.status == "complete"
    assert [phase.name for phase in result.phases] == ["validate_bindings", "field_cards", "ensure_page", "summary", "proposal", "audit"]
    assert result.audit.attempted and result.audit.ok
    assert result.page.page_id and result.page.build_status == "complete"
    assert "Signal" in result.page.summary
    assert result.to_json() == result.model_dump_json(exclude_none=True, by_alias=True)


def test_success_empty_paper_completes_without_entities_or_links(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)

    result = build_wiki_for_paper(
        context, definition, instance, metadata, service,
        EntityProposalRunner(lambda _: {"proposals": []}), _Resolver(context),
    )

    assert result.status == "complete"
    assert result.page.build_status == "complete"
    assert next(phase for phase in result.phases if phase.name == "proposal").status == "success_empty"
    assert result.proposals == ()
    assert store.list_entities() == []
    assert store.list_links() == []


def test_workspace_order_and_missing_input_isolation(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    service = WikiService(context, WikiStore(context, project_tmp_path))
    runner = EntityProposalRunner(lambda request: {"proposals": []})
    resolver = _Resolver(context)
    result = build_wiki_for_workspace(context, definition, {"p1": instance}, {"p1": metadata}, service, runner, resolver)
    assert [paper.paper_id for paper in result.papers] == ["p1", "p2"]
    assert result.papers[1].status == "failed"
    assert result.status == "partial" and result.complete_count == 1


def test_all_success_empty_workspace_and_manifest_are_complete(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    second_instance = instance.model_copy(update={"paper_id": "p2"})
    second_metadata = metadata.model_copy(update={"paper_id": "p2", "title": "Second paper"})
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)

    result = build_wiki_for_workspace(
        context, definition,
        {"p1": instance, "p2": second_instance},
        {"p1": metadata, "p2": second_metadata},
        service, EntityProposalRunner(lambda _: {"proposals": []}), _Resolver(context),
    )
    manifest = store.upsert_manifest(WikiManifest(
        workspace_id=context.workspace_id,
        schema_id=context.schema_id,
        schema_version=context.schema_version,
        paper_ids=context.paper_ids,
        builder_version="wiki-core-v1",
        build_status=result.status,
    ))

    assert result.status == "complete"
    assert result.complete_count == 2
    assert result.incomplete_count == result.failed_count == 0
    assert all(paper.status == "complete" for paper in result.papers)
    assert manifest.build_status == "complete"


def test_binding_failure_makes_no_provider_call(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    service = WikiService(context, WikiStore(context, project_tmp_path))
    calls = []
    runner = EntityProposalRunner(lambda request: calls.append(request) or {"proposals": []})
    resolver = _Resolver(context)
    outside = metadata.model_copy(update={"paper_id": "outside"})
    result = build_wiki_for_paper(context, definition, instance, outside, service, runner, resolver)
    assert result.status == "failed" and result.error_codes == ("paper_mismatch",)
    assert calls == [] and resolver.calls == []


def test_unknown_source_field_is_rejected_before_wiki_mutation(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)
    entity = service.create_entity("Signal")
    resolver = _Resolver(context)
    runner = EntityProposalRunner(lambda _: {"proposals": [{
        "canonical_name": "Signal", "description": "proposal",
        "source_field_id": "not-a-real-field", "confidence": 0.9,
    }]})

    result = build_wiki_for_paper(context, definition, instance, metadata, service, runner, resolver)

    assert result.status == "incomplete"
    assert result.proposals[0].error_code == "unknown_source_field_id"
    assert resolver.calls == []
    assert store.list_entities() == [entity]
    assert store.list_links() == []


def test_valid_source_field_persists_field_card_status(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)
    entity = service.create_entity("Signal")

    class _ReuseResolver(_Resolver):
        def resolve(self, proposal):
            self.calls.append(proposal.canonical_name)
            return EntityResolutionResult(decision="reuse", reason_code="exact_match", entity=entity, proposal=proposal)

    resolver = _ReuseResolver(context)
    runner = EntityProposalRunner(lambda _: {"proposals": [{
        "canonical_name": "Signal", "description": "proposal",
        "source_field_id": "name", "confidence": 0.9,
    }]})

    result = build_wiki_for_paper(context, definition, instance, metadata, service, runner, resolver)

    assert result.proposals[0].source_status == "explicit"
    assert store.list_links()[0].source_status == "explicit"


def test_resolver_failure_persists_non_complete_page_status(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)

    class _FailingResolver(_Resolver):
        def resolve(self, proposal):
            raise RuntimeError("resolver unavailable")

    runner = EntityProposalRunner(lambda _: {"proposals": [{
        "canonical_name": "Signal", "description": "proposal",
        "source_field_id": "name", "confidence": 0.9,
    }]})
    result = build_wiki_for_paper(context, definition, instance, metadata, service, runner, _FailingResolver(context))

    assert result.status == "incomplete"
    assert result.proposals[0].status == "failed"
    assert result.proposals[0].error_code == "resolution_failure"
    assert store.get_page(result.page.page_id).build_status == "incomplete"
    assert store.list_entities() == [] and store.list_links() == []


def test_link_failure_persists_non_complete_page_status(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store)
    entity = service.create_entity("Signal")

    class _ReuseResolver(_Resolver):
        def resolve(self, proposal):
            return EntityResolutionResult(decision="reuse", reason_code="exact_match", entity=entity, proposal=proposal)

    def fail_link(*args, **kwargs):
        raise RuntimeError("link write failed")

    service.link_page_entity = fail_link
    runner = EntityProposalRunner(lambda _: {"proposals": [{
        "canonical_name": "Signal", "description": "proposal",
        "source_field_id": "name", "confidence": 0.9,
    }]})
    result = build_wiki_for_paper(context, definition, instance, metadata, service, runner, _ReuseResolver(context))

    assert result.status == "incomplete"
    assert result.proposals[0].status == "failed"
    assert result.proposals[0].error_code == "link_failure"
    assert store.get_page(result.page.page_id).build_status == "incomplete"
    assert store.list_links() == []


def test_real_temporary_single_paper_abcd_pipeline_smoke(project_tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", "1")
    context = WorkspaceContext(workspace_id="smoke-ws", schema_id="generic", schema_version="1", paper_ids=["smoke-paper"])
    definition = SchemaDefinition(
        schema_id="generic", version="1",
        sections=[SectionDefinition(id="s", label="S", fields=[FieldDefinition(id="name", label="Name", question="Name", type="string")])],
    )
    instance = SchemaInstance(
        paper_id="smoke-paper", schema_id="generic", schema_version="1",
        fields={"name": FieldResult(value="Signal Control", status="explicit")},
    )
    metadata = PaperMetadata(paper_id="smoke-paper", title="A paper", authors=["A"], year=2024)
    class _SmokeEmbeddingProvider(EmbeddingProvider):
        available = True
        reason = None
        info = ProviderInfo(provider="smoke", model="smoke", dimension=2)

        def dimension(self):
            return 2

        def embed_documents(self, texts):
            return [[1.0, 0.0] for _ in texts]

        def embed_query(self, text):
            return [1.0, 0.0]

    store = WikiStore(context, project_tmp_path)
    service = WikiService(context, store, _SmokeEmbeddingProvider())
    seeded = service.create_entity("Signal", description="Signal Control entity")

    def proposal_provider(request):
        assert request.paper_id == "smoke-paper"
        return {"proposals": [{
            "canonical_name": "Signal Control", "aliases": ["Signal"],
            "description": "A deterministic proposal", "source_field_id": "name",
            "confidence": 0.91, "paper_id": request.paper_id,
            "schema_id": request.schema_id, "schema_version": request.schema_version,
        }]}

    def decision_provider(proposal, candidates):
        assert proposal.canonical_name == "Signal Control"
        return EntityResolutionDecision(
            action="reuse", reason="deterministic smoke decision",
            target_entity_id=candidates[0].entity_id, confidence=0.99,
        )

    runner = EntityProposalRunner(proposal_provider)
    resolver = EntityResolver(context, service, decision_provider)
    result = build_wiki_for_paper(context, definition, instance, metadata, service, runner, resolver)

    pages = store.list_pages()
    entities = store.list_entities()
    links = store.list_links()
    print(result.model_dump_json())
    assert result.status == "complete", result.model_dump()
    assert result.page.build_status == "complete"
    assert result.page.build_revision == 2
    assert result.page.summary.startswith("A paper | Authors: A | Year: 2024")
    assert len(pages) == 1 and pages[0].page_id == result.page.page_id
    assert len(entities) == 1 and entities[0].entity_id == seeded.entity_id
    assert len(links) == 1
    assert links[0].page_id == pages[0].page_id
    assert links[0].entity_id == entities[0].entity_id
    assert links[0].source_field_id == "name"
    assert links[0].source_status == "explicit"
    assert links[0].schema_id == context.schema_id and links[0].schema_version == context.schema_version
    assert result.proposals[0].resolution == "reuse"
    assert result.proposals[0].entity_id == entities[0].entity_id
    assert result.proposals[0].link_id == links[0].link_id
    assert result.audit.attempted and result.audit.ok
    assert result.audit.ok
    assert set(result.audit.issue_codes) <= {"index_missing", "index_stale"}
    report = service.audit_page(result.page.page_id)
    assert report.ok and all(issue.severity == "warning" for issue in report.issues)
    assert "TRANSIT_SCHOLAR_LLM_API_KEY" not in proposal_provider.__code__.co_names
