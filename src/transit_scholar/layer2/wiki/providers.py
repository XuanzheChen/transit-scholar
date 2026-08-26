"""Production provider adapters and composition for the workspace Wiki.

The adapters turn the shared structured LLM boundary into the two small Wiki
provider protocols.  They intentionally retain injection points so callers
can supply deterministic clients and embeddings in tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, resolve_embedding_provider
from transit_scholar.layer2.schema_extraction.llm import (
    LLMConfig,
    StructuredLLMClient,
    resolve_runtime_llm_client,
)

from .proposals import (
    EntityProposal,
    EntityProposalRequest,
    EntityProposalRunner,
)
from .resolution import (
    EntityResolutionCandidate,
    EntityResolutionDecision,
    EntityResolver,
)
from .models import WorkspaceContext
from .service import WikiService
from .store import WikiStore


class EntityProposalLLMOutput(BaseModel):
    """The strict envelope requested from the shared LLM client."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    proposals: tuple[EntityProposal, ...] = ()


class EntityProposalLLMAdapter:
    """Adapt a structured LLM client to the Entity Proposal provider protocol."""

    def __init__(self, client: StructuredLLMClient) -> None:
        self.client = client

    def __call__(self, request: EntityProposalRequest) -> dict[str, Any]:
        if not isinstance(request, EntityProposalRequest):
            raise TypeError("request must be an EntityProposalRequest")
        output = self.client.generate_structured(
            [
                {
                    "role": "system",
                    "content": (
                        "Identify reusable wiki entities from the supplied field cards. "
                        "Return only the requested structured output. Every proposal must "
                        "cite one supplied source_field_id."
                    ),
                },
                {"role": "user", "content": request.to_json()},
            ],
            EntityProposalLLMOutput,
            {
                "task": "wiki_entity_proposal",
                "paper_id": request.paper_id,
                "schema_id": request.schema_id,
                "schema_version": request.schema_version,
            },
        )
        return EntityProposalLLMOutput.model_validate(output).model_dump(mode="json")


class EntityResolutionDecisionLLMAdapter:
    """Adapt a structured LLM client to a candidate-bound resolution policy."""

    def __init__(self, client: StructuredLLMClient) -> None:
        self.client = client

    def __call__(
        self,
        proposal: EntityProposal,
        candidates: tuple[EntityResolutionCandidate, ...],
    ) -> dict[str, Any]:
        if not isinstance(proposal, EntityProposal):
            raise TypeError("proposal must be an EntityProposal")
        if not isinstance(candidates, tuple) or not all(
            isinstance(candidate, EntityResolutionCandidate) for candidate in candidates
        ):
            raise TypeError("candidates must be EntityResolutionCandidate values")
        payload = {
            "proposal": proposal.model_dump(mode="json"),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        output = self.client.generate_structured(
            [
                {
                    "role": "system",
                    "content": (
                        "Resolve the proposal only against the supplied workspace candidates. "
                        "Use reuse only with a listed target_entity_id; otherwise choose "
                        "create or ambiguous. Return only the requested structured output."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
            ],
            EntityResolutionDecision,
            {
                "task": "wiki_entity_resolution",
                "candidate_entity_ids": [candidate.entity_id for candidate in candidates],
                "source_field_id": proposal.source_field_id,
            },
        )
        return EntityResolutionDecision.model_validate(output).model_dump(mode="json")


def create_production_entity_proposal_provider(
    *,
    llm_client: StructuredLLMClient | None = None,
    llm_config: LLMConfig | None = None,
) -> EntityProposalLLMAdapter:
    """Create the proposal adapter using a real runtime client unless injected."""
    client = llm_client if llm_client is not None else resolve_runtime_llm_client(llm_config)
    return EntityProposalLLMAdapter(client)


def create_production_resolution_decision_provider(
    *,
    llm_client: StructuredLLMClient | None = None,
    llm_config: LLMConfig | None = None,
) -> EntityResolutionDecisionLLMAdapter:
    """Create the decision adapter using a real runtime client unless injected."""
    client = llm_client if llm_client is not None else resolve_runtime_llm_client(llm_config)
    return EntityResolutionDecisionLLMAdapter(client)


def resolve_wiki_embedding_provider(
    config: Layer2Config | None = None,
) -> EmbeddingProvider:
    """Resolve the shared cloud embedding provider; never substitute a fake."""
    if config is None:
        from transit_scholar.config import settings

        config = Layer2Config.from_settings(settings)
    return resolve_embedding_provider(config)


@dataclass(frozen=True)
class WikiProductionComposition:
    """Production Wiki collaborators, with all boundaries visible for injection."""

    service: WikiService
    proposal_runner: EntityProposalRunner
    resolver: EntityResolver
    embedding_provider: EmbeddingProvider
    proposal_provider: EntityProposalLLMAdapter
    decision_provider: EntityResolutionDecisionLLMAdapter


def create_production_wiki_composition(
    context: WorkspaceContext,
    store: WikiStore,
    *,
    llm_client: StructuredLLMClient | None = None,
    llm_config: LLMConfig | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_config: Layer2Config | None = None,
    top_k: int = 5,
    minimum_confidence: float = 0.5,
) -> WikiProductionComposition:
    """Compose production Wiki services without importing Package F test fakes."""
    client = llm_client if llm_client is not None else resolve_runtime_llm_client(llm_config)
    resolved_embedding = (
        embedding_provider
        if embedding_provider is not None
        else resolve_wiki_embedding_provider(embedding_config)
    )
    service = WikiService(context, store, resolved_embedding)
    proposal_provider = EntityProposalLLMAdapter(client)
    decision_provider = EntityResolutionDecisionLLMAdapter(client)
    return WikiProductionComposition(
        service=service,
        proposal_runner=EntityProposalRunner(proposal_provider),
        resolver=EntityResolver(
            context,
            service,
            decision_provider,
            top_k=top_k,
            minimum_confidence=minimum_confidence,
        ),
        embedding_provider=resolved_embedding,
        proposal_provider=proposal_provider,
        decision_provider=decision_provider,
    )
