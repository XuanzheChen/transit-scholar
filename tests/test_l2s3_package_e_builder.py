from transit_scholar.layer2.schema_extraction import FieldDefinition, FieldResult, SchemaDefinition, SchemaInstance, SectionDefinition
from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.wiki import (
    EntityProposal, EntityProposalRunner, EntityResolver, EntityResolutionDecision, EntityResolutionResult,
    PaperMetadata, WikiEntity, WikiService, WikiStore,
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
    assert result.status == "incomplete"
    assert [phase.name for phase in result.phases] == ["validate_bindings", "field_cards", "ensure_page", "summary", "proposal", "audit"]
    assert result.audit.attempted and result.audit.ok
    assert result.page.page_id and result.page.build_status == "incomplete"
    assert "Signal" in result.page.summary
    assert result.to_json() == result.model_dump_json(exclude_none=True, by_alias=True)


def test_workspace_order_and_missing_input_isolation(project_tmp_path):
    context, definition, instance, metadata = _inputs()
    service = WikiService(context, WikiStore(context, project_tmp_path))
    runner = EntityProposalRunner(lambda request: {"proposals": []})
    resolver = _Resolver(context)
    result = build_wiki_for_workspace(context, definition, {"p1": instance}, {"p1": metadata}, service, runner, resolver)
    assert [paper.paper_id for paper in result.papers] == ["p1", "p2"]
    assert result.papers[1].status == "failed"
    assert result.status == "failed" and result.complete_count == 0


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
