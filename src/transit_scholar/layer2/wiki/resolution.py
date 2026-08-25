"""Bounded, workspace-scoped entity resolution for L2S3 Package D."""

from __future__ import annotations

from typing import Any, Callable, Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import WikiEntity, WorkspaceContext, normalize_entity_name
from .proposals import EntityProposal
from .service import WikiService


class EntityResolutionCandidate(BaseModel):
    """An explainable semantic candidate from the current workspace only."""

    model_config = ConfigDict(frozen=True)
    entity_id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    score: float


class EntityResolutionDecision(BaseModel):
    """Strict structured output accepted from an injected decision policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["reuse", "create", "ambiguous"]
    reason: str = Field(min_length=1)
    target_entity_id: str | None = None
    confidence: float = Field(ge=0, le=1)


class EntityResolutionResult(BaseModel):
    """Sanitized, serializable result of one non-recursive resolution attempt."""

    model_config = ConfigDict(frozen=True)
    decision: Literal["reuse", "create", "ambiguous"]
    reason_code: str
    error_code: str | None = None
    proposal: EntityProposal | None = None
    candidates: tuple[EntityResolutionCandidate, ...] = ()
    entity: WikiEntity | None = None

    @property
    def action(self) -> Literal["reuse", "create", "ambiguous"]:
        return self.decision


class ResolutionDecisionProvider(Protocol):
    def __call__(
        self, proposal: EntityProposal, candidates: tuple[EntityResolutionCandidate, ...]
    ) -> Any: ...


class EntityResolver:
    """Resolve a proposal through exact lookup, one semantic query, then one decision."""

    def __init__(
        self,
        context: WorkspaceContext,
        service: WikiService,
        decision_provider: ResolutionDecisionProvider | Callable[..., Any] | None = None,
        *,
        top_k: int = 5,
        minimum_confidence: float = 0.5,
    ) -> None:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 0 < top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        if not isinstance(minimum_confidence, (int, float)) or isinstance(minimum_confidence, bool) or not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between 0 and 1")
        self.context = context.model_copy(deep=True)
        self.service = service
        self.decision_provider = decision_provider
        self.top_k = top_k
        self.minimum_confidence = float(minimum_confidence)
        self._binding_error = not isinstance(service, WikiService) or (
            service.context != context or service.store.workspace_context != context
        )

    def _result(
        self,
        decision: Literal["reuse", "create", "ambiguous"],
        reason_code: str,
        proposal: EntityProposal | None,
        *,
        error_code: str | None = None,
        candidates: tuple[EntityResolutionCandidate, ...] = (),
        entity: WikiEntity | None = None,
    ) -> EntityResolutionResult:
        return EntityResolutionResult(
            decision=decision, reason_code=reason_code, error_code=error_code,
            proposal=proposal, candidates=candidates, entity=entity,
        )

    def _ambiguous(self, proposal: EntityProposal | None, error_code: str, candidates: tuple[EntityResolutionCandidate, ...] = ()) -> EntityResolutionResult:
        return self._result("ambiguous", "ambiguous_entity", proposal, error_code=error_code, candidates=candidates)

    @staticmethod
    def _exact_candidates(entities: list[WikiEntity]) -> tuple[EntityResolutionCandidate, ...]:
        return tuple(
            EntityResolutionCandidate(
                entity_id=entity.entity_id, canonical_name=entity.canonical_name,
                aliases=tuple(entity.aliases), description=entity.description, score=1.0,
            )
            for entity in sorted(entities, key=lambda item: item.entity_id)
        )

    def _reuse(self, proposal: EntityProposal, entity: WikiEntity, candidates: tuple[EntityResolutionCandidate, ...] = ()) -> EntityResolutionResult:
        if entity.workspace_id != self.context.workspace_id:
            return self._ambiguous(proposal, "invalid_target", candidates)
        try:
            self._add_distinct_aliases(proposal, entity)
        except Exception:
            return self._ambiguous(proposal, "service_failure", candidates)
        return self._result("reuse", "exact_match" if not candidates else "semantic_reuse", proposal, candidates=candidates, entity=entity)

    def _add_distinct_aliases(self, proposal: EntityProposal, entity: WikiEntity) -> None:
        existing = {normalize_entity_name(entity.canonical_name), *(normalize_entity_name(alias) for alias in entity.aliases)}
        pending: list[str] = []
        for spelling in (proposal.canonical_name, *proposal.aliases):
            try:
                normalized = normalize_entity_name(spelling)
            except ValueError:
                continue
            if normalized and normalized not in existing:
                pending.append(spelling)
                existing.add(normalized)
        if pending:
            self.service.add_entity_aliases(entity.entity_id, pending)

    def _create(self, proposal: EntityProposal, candidates: tuple[EntityResolutionCandidate, ...] = ()) -> EntityResolutionResult:
        try:
            entity = self.service.create_entity(
                proposal.canonical_name, description=proposal.description, aliases=list(proposal.aliases)
            )
        except Exception:
            return self._ambiguous(proposal, "service_failure", candidates)
        if entity.workspace_id != self.context.workspace_id:
            return self._ambiguous(proposal, "workspace_mismatch", candidates)
        return self._result("create", "created", proposal, candidates=candidates, entity=entity)

    def _semantic_candidates(self, proposal: EntityProposal) -> tuple[tuple[EntityResolutionCandidate, ...] | None, str | None]:
        try:
            result = self.service.search_entities(proposal.canonical_name, top_k=self.top_k, mode="semantic")
        except Exception:
            return None, "semantic_unavailable"
        if result.status != "ok":
            return None, "semantic_unavailable"
        candidates: list[EntityResolutionCandidate] = []
        seen: set[str] = set()
        try:
            for hit in result.hits:
                if hit.type != "entity" or hit.object_id in seen:
                    continue
                entity = self.service.get_entity(hit.object_id)
                if entity.workspace_id != self.context.workspace_id:
                    return None, "workspace_mismatch"
                seen.add(entity.entity_id)
                candidates.append(EntityResolutionCandidate(
                    entity_id=entity.entity_id, canonical_name=entity.canonical_name,
                    aliases=tuple(entity.aliases), description=entity.description, score=hit.score,
                ))
        except Exception:
            return None, "semantic_unavailable"
        return tuple(sorted(candidates, key=lambda item: (-item.score, item.entity_id))[:self.top_k]), None

    def _call_decider(self, proposal: EntityProposal, candidates: tuple[EntityResolutionCandidate, ...]) -> EntityResolutionDecision | None:
        provider = self.decision_provider
        if provider is None:
            return None
        try:
            if callable(provider):
                raw = provider(proposal, candidates)
            elif hasattr(provider, "resolve"):
                raw = provider.resolve(proposal, candidates)
            elif hasattr(provider, "decide"):
                raw = provider.decide(proposal, candidates)
            else:
                return None
            return EntityResolutionDecision.model_validate(raw)
        except (Exception, ValidationError):
            return None

    def resolve(self, proposal: EntityProposal) -> EntityResolutionResult:
        if not isinstance(proposal, EntityProposal):
            return self._ambiguous(None, "invalid_input")
        if self._binding_error:
            return self._ambiguous(proposal, "workspace_mismatch")
        try:
            normalized = normalize_entity_name(proposal.canonical_name)
        except ValueError:
            normalized = ""
        if not normalized:
            return self._ambiguous(proposal, "invalid_input")
        try:
            canonical = self.service.find_entities_by_canonical_name(proposal.canonical_name)
            canonical_candidates = self._exact_candidates(canonical)
            if len(canonical) == 1:
                return self._reuse(proposal, canonical[0], canonical_candidates)
            if len(canonical) > 1:
                return self._ambiguous(proposal, "exact_conflict", canonical_candidates)
            aliases = self.service.find_entities_by_alias(proposal.canonical_name)
            alias_candidates = self._exact_candidates(aliases)
            if len(aliases) == 1:
                return self._reuse(proposal, aliases[0], alias_candidates)
            if len(aliases) > 1:
                return self._ambiguous(proposal, "exact_conflict", alias_candidates)
        except Exception:
            return self._ambiguous(proposal, "service_failure")
        candidates, error_code = self._semantic_candidates(proposal)
        if candidates is None:
            return self._ambiguous(proposal, error_code or "semantic_unavailable")
        if not candidates:
            return self._create(proposal)
        if self.decision_provider is None:
            return self._ambiguous(proposal, "resolver_unavailable", candidates)
        decision = self._call_decider(proposal, candidates)
        if decision is None:
            return self._ambiguous(proposal, "invalid_decision", candidates)
        if decision.confidence < self.minimum_confidence:
            return self._ambiguous(proposal, "low_confidence", candidates)
        if decision.action == "reuse":
            if not decision.target_entity_id:
                return self._ambiguous(proposal, "invalid_target", candidates)
            matched = [candidate for candidate in candidates if candidate.entity_id == decision.target_entity_id]
            if len(matched) != 1:
                return self._ambiguous(proposal, "invalid_target", candidates)
            try:
                entity = self.service.get_entity(matched[0].entity_id)
            except Exception:
                return self._ambiguous(proposal, "invalid_target", candidates)
            return self._reuse(proposal, entity, candidates)
        if decision.action == "create":
            if decision.target_entity_id is not None:
                return self._ambiguous(proposal, "invalid_decision", candidates)
            return self._create(proposal, candidates)
        if decision.target_entity_id is not None:
            return self._ambiguous(proposal, "invalid_decision", candidates)
        return self._ambiguous(proposal, "ambiguous_entity", candidates)

    __call__ = resolve
