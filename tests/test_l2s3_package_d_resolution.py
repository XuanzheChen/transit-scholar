from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.wiki import EntityProposal, EntityResolver, WikiService, WikiStore, WorkspaceContext


class _Embedding(EmbeddingProvider):
    available = True
    reason = None
    info = ProviderInfo(provider="fake", model="fake", dimension=2)

    def dimension(self): return 2
    def embed_documents(self, texts): return [[1.0, 0.0] for _ in texts]
    def embed_query(self, text): return [1.0, 0.0]


def _service(root, workspace="ws"):
    context = WorkspaceContext(workspace_id=workspace, schema_id="schema", schema_version="1", paper_ids=["p1"])
    return WikiService(context, WikiStore(context, root), _Embedding())


def _proposal(name="Signal Priority"):
    return EntityProposal(canonical_name=name, aliases=("SP",), description="priority", source_field_id="method", confidence=1)


def test_canonical_and_alias_exact_skip_semantic_and_decider(project_tmp_path, monkeypatch):
    service = _service(project_tmp_path)
    entity = service.create_entity("Signal Priority", aliases=["SP"])
    calls = []
    monkeypatch.setattr(service, "search_entities", lambda *args, **kwargs: calls.append(args))
    resolver = EntityResolver(service.context, service, lambda *_: calls.append("decider"))
    canonical_result = resolver.resolve(_proposal())
    alias_result = resolver.resolve(_proposal("sp"))
    assert canonical_result.entity.entity_id == entity.entity_id
    assert alias_result.entity.entity_id == entity.entity_id
    assert canonical_result.reason_code == "exact_match"
    assert alias_result.reason_code == "exact_match"
    assert canonical_result.reason_code != "semantic_reuse"
    assert alias_result.reason_code != "semantic_reuse"
    assert calls == []


def test_empty_semantic_result_creates_once_through_service(project_tmp_path):
    service = _service(project_tmp_path)
    calls = []
    original = service.create_entity
    service.create_entity = lambda *args, **kwargs: (calls.append((args, kwargs)), original(*args, **kwargs))[1]
    resolver = EntityResolver(service.context, service, lambda *_: (_ for _ in ()).throw(AssertionError("not called")))
    first = resolver.resolve(_proposal("New Entity"))
    second = resolver.resolve(_proposal("New Entity"))
    assert first.decision == "create" and second.decision == "reuse"
    assert len(calls) == 1


def test_semantic_reuse_is_candidate_bound_and_deterministic(project_tmp_path):
    service = _service(project_tmp_path)
    first = service.create_entity("First")
    service.create_entity("Second")
    seen = []
    resolver = EntityResolver(service.context, service, lambda proposal, candidates: (seen.append(candidates), {"action": "reuse", "reason": "same", "target_entity_id": first.entity_id, "confidence": 1})[1], top_k=2)
    result = resolver.resolve(_proposal("Related"))
    assert result.decision == "reuse" and result.entity.entity_id == first.entity_id
    assert result.reason_code == "semantic_reuse"
    assert len(seen) == 1 and [item.entity_id for item in result.candidates] == sorted(item.entity_id for item in result.candidates)
