"""Bounded, injectable Entity Proposal boundary for L2S3 Package C."""
from __future__ import annotations

import json
import unicodedata
from typing import Any, Callable, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .field_cards import FieldCard, _canonical


def _norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class EntityProposal(BaseModel):
    model_config = ConfigDict(frozen=True)
    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    description: str
    source_field_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    paper_id: str | None = None
    schema_id: str | None = None
    schema_version: str | None = None

    @field_validator("canonical_name", "description", "source_field_id", mode="before")
    @classmethod
    def _strings(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return " ".join(unicodedata.normalize("NFKC", value).split()).strip() if value else value

    @field_validator("aliases", mode="before")
    @classmethod
    def _aliases(cls, values: Any) -> tuple[str, ...]:
        if values is None:
            return ()
        if not isinstance(values, (list, tuple)):
            raise ValueError("aliases must be a sequence")
        aliases: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                raise ValueError("aliases must contain strings")
            spelling = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
            key = _norm(spelling)
            if spelling and key not in seen:
                aliases.append(spelling)
                seen.add(key)
        return tuple(aliases)

    def model_post_init(self, __context: Any) -> None:
        canonical = _norm(self.canonical_name)
        object.__setattr__(self, "aliases", tuple(alias for alias in self.aliases if _norm(alias) != canonical))


class EntityProposalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    cards: tuple[FieldCard, ...]
    paper_id: str | None = None
    schema_id: str | None = None
    schema_version: str | None = None

    @field_validator("cards", mode="before")
    @classmethod
    def _cards(cls, values: Any) -> tuple[FieldCard, ...]:
        if not isinstance(values, (list, tuple)):
            raise ValueError("cards must be a sequence")
        if not all(isinstance(card, FieldCard) for card in values):
            raise ValueError("cards must contain FieldCard values")
        try:
            return tuple(FieldCard.model_validate(card.model_dump(mode="python")) for card in values)
        except Exception as exc:
            raise ValueError("cards must contain valid FieldCard values") from exc

    def to_payload(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "cards": [card.model_dump(mode="json") for card in self.cards],
        }

    def to_json(self) -> str:
        return json.dumps(_canonical(self.to_payload()), ensure_ascii=False, separators=(",", ":"))


def build_entity_proposal_request(cards: list[FieldCard] | tuple[FieldCard, ...], *, paper_id: str | None = None,
                                  schema_id: str | None = None, schema_version: str | None = None) -> EntityProposalRequest:
    return EntityProposalRequest(cards=cards, paper_id=paper_id, schema_id=schema_id, schema_version=schema_version)


build_proposal_request = build_entity_proposal_request


def build_entity_proposal_prompt(request: EntityProposalRequest) -> str:
    """Return deterministic structured request text without accessing sources."""
    if not isinstance(request, EntityProposalRequest):
        raise TypeError("request must be an EntityProposalRequest")
    return request.to_json()


build_proposal_prompt = build_entity_proposal_prompt


class StructuredOutputProvider(Protocol):
    def __call__(self, request: EntityProposalRequest) -> Any: ...


class EntityProposalResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: str
    proposals: tuple[EntityProposal, ...] = ()
    error_code: str | None = None
    cards: tuple[FieldCard, ...] = ()


class EntityProposalRunner:
    def __init__(self, provider: StructuredOutputProvider | Callable[[EntityProposalRequest], Any]):
        self.provider = provider

    def run(self, request: EntityProposalRequest) -> EntityProposalResult:
        if not isinstance(request, EntityProposalRequest):
            return EntityProposalResult(status="invalid", error_code="invalid_input")
        cards = tuple(card.model_copy(deep=True) for card in request.cards)
        try:
            if callable(self.provider):
                output = self.provider(request)
            elif hasattr(self.provider, "generate"):
                output = self.provider.generate(request)
            elif hasattr(self.provider, "complete"):
                output = self.provider.complete(request)
            else:
                return EntityProposalResult(status="provider_failure", error_code="provider_failure", cards=cards)
        except Exception:
            return EntityProposalResult(status="provider_failure", error_code="provider_failure", cards=cards)
        if output is None:
            return EntityProposalResult(status="missing", error_code="missing_output", cards=cards)
        if not isinstance(output, Mapping) or "proposals" not in output:
            return EntityProposalResult(status="malformed", error_code="malformed_output", cards=cards)
        records = output["proposals"]
        if not isinstance(records, (list, tuple)):
            return EntityProposalResult(status="malformed", error_code="malformed_output", cards=cards)
        try:
            proposals = tuple(EntityProposal.model_validate(record) for record in records)
        except Exception:
            return EntityProposalResult(status="invalid", error_code="invalid_output", cards=cards)
        proposals = tuple(sorted(proposals, key=lambda item: (_norm(item.source_field_id), _norm(item.canonical_name))))
        return EntityProposalResult(status="success_empty" if not proposals else "success", proposals=proposals, cards=cards)

    __call__ = run


run_entity_proposals = EntityProposalRunner


def generate_entity_proposals(request: EntityProposalRequest, provider: StructuredOutputProvider) -> EntityProposalResult:
    return EntityProposalRunner(provider).run(request)