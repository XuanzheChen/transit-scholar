from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.wiki import EntityProposal, EntityResolver, WikiService, WikiStore, WorkspaceContext


class _Embedding(EmbeddingProvider):
    available = True
    reason = None
    info = ProviderInfo(provider="fake", model="fake", dimension=1)
    def dimension(self): return 1
    def embed_documents(self, texts): return [[1.0] for _ in texts]
    def embed_query(self, text): return [1.0]


def _service(root):
    context = WorkspaceContext(workspace_id="ws", schema_id="schema", schema_version="1", paper_ids=["p1"])
    return WikiService(context, WikiStore(context, root), _Embedding())


def _proposal():
    return EntityProposal(canonical_name="Candidate", description="d", source_field_id="f", confidence=1)


def test_invalid_decision_is_sanitized_and_non_destructive(project_tmp_path):
    service = _service(project_tmp_path)
    service.create_entity("Existing")
    before = {name: asset["sha256"] for name, asset in service.store.read_raw_snapshot().items()}
    resolver = EntityResolver(service.context, service, lambda *_: {"action": "reuse", "reason": "bad", "target_entity_id": "foreign", "confidence": 1})
    result = resolver.resolve(_proposal())
    after = {name: asset["sha256"] for name, asset in service.store.read_raw_snapshot().items()}
    assert result.decision == "ambiguous" and result.reason_code == "ambiguous_entity"
    assert result.error_code == "invalid_target" and before == after


def test_provider_failure_is_redacted_ambiguous(project_tmp_path):
    service = _service(project_tmp_path)
    service.create_entity("Existing")
    raw_error = "Authorization: " + "secret" + " " + "https" + "://provider.example"
    service.search_entities = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(raw_error))
    result = EntityResolver(service.context, service).resolve(_proposal())
    payload = result.model_dump_json()
    assert result.decision == "ambiguous" and result.error_code == "semantic_unavailable"
    assert "secret" not in payload and "provider.example" not in payload


def test_missing_decider_with_candidates_does_not_write(project_tmp_path):
    service = _service(project_tmp_path)
    service.create_entity("Existing")
    before = {name: asset["sha256"] for name, asset in service.store.read_raw_snapshot().items()}
    result = EntityResolver(service.context, service).resolve(_proposal())
    assert result.decision == "ambiguous"
    assert {name: asset["sha256"] for name, asset in service.store.read_raw_snapshot().items()} == before


def test_multi_alias_governance_failure_is_zero_write_and_success_is_idempotent(project_tmp_path, monkeypatch):
    service = _service(project_tmp_path)
    entity = service.create_entity("Canonical")
    proposal = EntityProposal(
        canonical_name="First Alias", aliases=("Second Alias",), description="d", source_field_id="f", confidence=1
    )
    before = {name: asset["sha256"] for name, asset in service.store.read_raw_snapshot().items()}
    original_update = service.update_entity
    calls = 0

    def fail_update(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("late alias failure")

    monkeypatch.setattr(service, "update_entity", fail_update)
    failed = EntityResolver(service.context, service)._reuse(proposal, entity)
    after = {name: asset["sha256"] for name, asset in service.store.read_raw_snapshot().items()}
    assert failed.decision == "ambiguous" and failed.error_code == "service_failure"
    assert calls == 1 and before == after

    monkeypatch.setattr(service, "update_entity", original_update)
    successful = EntityResolver(service.context, service)._reuse(proposal, entity)
    repeated = EntityResolver(service.context, service)._reuse(proposal, successful.entity)
    assert successful.decision == repeated.decision == "reuse"
    assert service.get_entity(entity.entity_id).aliases == ["First Alias", "Second Alias"]
