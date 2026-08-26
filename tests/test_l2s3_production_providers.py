"""Offline checks for the L2S3 production-provider composition root."""
from __future__ import annotations

import pytest

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.retrieval.providers import (
    EmbeddingProvider,
    ProviderInfo,
    UnavailableEmbeddingProvider,
)
from transit_scholar.layer2.schema_extraction.errors import LLMUnavailableError
from transit_scholar.layer2.schema_extraction.llm import LLMConfig
from transit_scholar.layer2.wiki import (
    EntityProposal,
    EntityProposalRequest,
    EntityResolutionCandidate,
    EntityResolutionDecision,
    WikiStore,
    WorkspaceContext,
    create_production_entity_proposal_provider,
    create_production_wiki_composition,
    resolve_wiki_embedding_provider,
)


class _StructuredClient:
    is_fake = False
    provider_name = "test-structured"
    model_name = "test-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_structured(self, messages, output_schema, metadata=None):
        self.calls.append((messages, output_schema, metadata))
        return output_schema.model_validate(self.responses.pop(0))


class _Embedding(EmbeddingProvider):
    available = True
    reason = None
    info = ProviderInfo(provider="production-test", model="embedding-test", dimension=2)

    def dimension(self):
        return 2

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class _FailingEmbedding(_Embedding):
    def embed_documents(self, texts):
        raise RuntimeError("embedding failure")


def _context():
    return WorkspaceContext(workspace_id="production", schema_id="schema", schema_version="1", paper_ids=["p1"])


def _proposal():
    return EntityProposal(canonical_name="Signal Priority", description="priority", source_field_id="method", confidence=0.9)


def test_default_production_llm_provider_is_unavailable_not_a_fake():
    with pytest.raises(LLMUnavailableError):
        create_production_entity_proposal_provider(llm_config=LLMConfig())


def test_production_adapters_request_structured_candidate_bound_outputs(project_tmp_path):
    context = _context()
    client = _StructuredClient([
        {"proposals": [_proposal().model_dump(mode="json")]},
        {"action": "reuse", "reason": "same entity", "target_entity_id": "entity-1", "confidence": 0.9},
    ])
    composition = create_production_wiki_composition(
        context, WikiStore(context, project_tmp_path), llm_client=client, embedding_provider=_Embedding()
    )

    proposal_result = composition.proposal_runner.run(EntityProposalRequest(cards=()))
    candidate = EntityResolutionCandidate(entity_id="entity-1", canonical_name="Signal Priority", score=0.8)
    decision = composition.decision_provider(_proposal(), (candidate,))

    assert proposal_result.status == "success"
    assert decision == EntityResolutionDecision(action="reuse", reason="same entity", target_entity_id="entity-1", confidence=0.9).model_dump(mode="json")
    assert client.calls[0][2]["task"] == "wiki_entity_proposal"
    assert client.calls[1][2]["candidate_entity_ids"] == ["entity-1"]
    assert "entity-1" in client.calls[1][0][1]["content"]
    assert composition.proposal_provider.__class__.__name__ != "ProposalFake"
    assert composition.decision_provider.__class__.__name__ != "DecisionFake"


def test_production_composition_keeps_low_confidence_and_malformed_decisions_explicit(project_tmp_path):
    context = _context()
    composition = create_production_wiki_composition(
        context,
        WikiStore(context, project_tmp_path),
        llm_client=_StructuredClient([{"action": "reuse", "reason": "same", "target_entity_id": "not-a-candidate", "confidence": 0.9}]),
        embedding_provider=_Embedding(),
        minimum_confidence=0.95,
    )
    entity = composition.service.create_entity("Existing")
    result = composition.resolver.resolve(_proposal())
    assert result.decision == "ambiguous" and result.error_code == "low_confidence"

    malformed = create_production_wiki_composition(
        context,
        WikiStore(context, project_tmp_path / "malformed"),
        llm_client=_StructuredClient([{"action": "reuse"}]),
        embedding_provider=_Embedding(),
    )
    malformed.service.create_entity("Existing")
    malformed_result = malformed.resolver.resolve(_proposal())
    assert malformed_result.decision == "ambiguous"
    assert malformed_result.error_code == "invalid_decision"
    assert entity.entity_id


def test_wiki_embedding_resolution_and_service_results_are_explicit(project_tmp_path):
    unavailable = resolve_wiki_embedding_provider(Layer2Config(block_network=True))
    assert isinstance(unavailable, UnavailableEmbeddingProvider)
    assert unavailable.available is False

    context = _context()
    store = WikiStore(context, project_tmp_path)
    client = _StructuredClient([])
    success = create_production_wiki_composition(context, store, llm_client=client, embedding_provider=_Embedding())
    success.service.create_entity("Signal Priority", description="signal")
    assert success.service.search_entities("signal", mode="semantic").status == "ok"

    unavailable_composition = create_production_wiki_composition(context, store, llm_client=client, embedding_provider=unavailable)
    assert unavailable_composition.service.search_entities("signal", mode="semantic").error_code == "embedding_unavailable"

    failed = create_production_wiki_composition(context, store, llm_client=client, embedding_provider=_FailingEmbedding())
    assert failed.service.search_entities("signal", mode="semantic").error_code == "embedding_provider_failure"
    assert failed.embedding_provider.__class__.__name__ != "FakeEmbedding"
