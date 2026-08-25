"""Deterministic, schema-neutral Field Card assembly for L2S3 Package C."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from transit_scholar.layer2.schema_extraction.models import (
    FIELD_STATUSES,
    EvidenceRef,
    FieldDefinition,
    FieldResult,
    SchemaDefinition,
    SchemaInstance,
    SectionDefinition,
)


class FieldCardValidationError(ValueError):
    """Typed, stable validation failure while assembling cards."""

    def __init__(self, code: str, message: str = "invalid field card input") -> None:
        self.code = code
        self.message = message
        super().__init__(message)


FieldCardError = FieldCardValidationError


class _FrozenList(list[Any]):
    """JSON-compatible list which rejects all mutation."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("FieldCard values are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable


class _FrozenDict(dict[str, Any]):
    """JSON-compatible dictionary which rejects all mutation."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("FieldCard values are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _sort_key(value: Any) -> tuple[str, str]:
    return (type(value).__name__, repr(value))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenDict({key: _freeze(value[key]) for key in sorted(value, key=_sort_key)})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(_freeze(item) for item in sorted(value, key=_sort_key))
    return deepcopy(value)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value, key=_sort_key)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


class FrozenEvidenceRef(BaseModel):
    """Immutable, JSON-compatible snapshot of a source evidence reference."""

    model_config = ConfigDict(frozen=True)

    block_id: str
    char_start: int
    char_end: int
    pages: tuple[int, ...] = ()
    section_path: tuple[str, ...] = ()
    quote: str = ""


class FieldCard(BaseModel):
    """Lossless immutable context for one schema field and extracted result."""

    model_config = ConfigDict(frozen=True)

    paper_id: str
    schema_id: str
    schema_version: str
    section_id: str
    section_label: str
    field_id: str
    field_label: str
    question: str
    description: str
    type: str
    options: Any = None
    constraints: Any = Field(default_factory=_FrozenDict)
    evidence_required: bool = False
    allow_inference: bool = True
    output_guidance: Any = None
    value: Any = None
    status: str
    confidence: float | None = None
    notes: str | None = None
    evidence: tuple[FrozenEvidenceRef, ...] = ()

    @field_validator("constraints", "output_guidance", "value", mode="before")
    @classmethod
    def _freeze_nested(cls, value: Any) -> Any:
        return _freeze(value)

    @field_validator("options", mode="before")
    @classmethod
    def _freeze_options(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            raise ValueError("options must be a sequence")
        return _freeze(value)

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False) -> "FieldCard":
        payload = self.model_dump(mode="python")
        if update:
            payload.update(deepcopy(update))
        return type(self).model_validate(payload)

    def model_dump_json(self, **kwargs: Any) -> str:
        return json.dumps(
            _canonical(self.model_dump(mode="json")),
            ensure_ascii=kwargs.get("ensure_ascii", False),
            indent=kwargs.get("indent"),
            separators=None if kwargs.get("indent") is not None else (",", ":"),
        )


def _fail(code: str, message: str) -> FieldCardValidationError:
    return FieldCardValidationError(code, message)


def _attribute(value: Any, name: str) -> Any:
    try:
        return getattr(value, name)
    except (AttributeError, TypeError):
        raise _fail("invalid_input", f"missing or invalid {name}") from None


def _validated(model_type: type[BaseModel], value: Any, label: str) -> BaseModel:
    try:
        if not isinstance(value, model_type):
            raise ValueError("wrong model type")
        return model_type.model_validate(value.model_dump(mode="python"))
    except FieldCardValidationError:
        raise
    except Exception:
        raise _fail("invalid_input", f"malformed {label}") from None


def build_field_cards(
    definition: SchemaDefinition,
    instance: SchemaInstance,
    *,
    include_statuses: Iterable[str] | None = None,
) -> tuple[FieldCard, ...]:
    """Pair validated schema fields and results by exact id in authored order."""
    if not isinstance(definition, SchemaDefinition) or not isinstance(instance, SchemaInstance):
        raise _fail("invalid_input", "definition and instance must be schema models")

    definition_schema_id = _attribute(definition, "schema_id")
    definition_version = _attribute(definition, "version")
    instance_schema_id = _attribute(instance, "schema_id")
    instance_version = _attribute(instance, "schema_version")
    if not all(isinstance(value, str) and value for value in (
        definition_schema_id, definition_version, instance_schema_id, instance_version,
    )):
        raise _fail("invalid_input", "schema identity and version must be non-empty strings")
    if definition_schema_id != instance_schema_id or definition_version != instance_version:
        raise _fail("schema_mismatch", "schema identity or version does not match")

    paper_id = _attribute(instance, "paper_id")
    sections = _attribute(definition, "sections")
    fields = _attribute(instance, "fields")
    if not isinstance(paper_id, str) or not isinstance(sections, list) or not isinstance(fields, dict):
        raise _fail("invalid_input", "malformed schema definition or instance")

    try:
        selected = set(FIELD_STATUSES if include_statuses is None else include_statuses)
    except TypeError:
        raise _fail("invalid_input", "statuses must be iterable") from None
    if not selected.issubset(FIELD_STATUSES):
        raise _fail("invalid_input", "unknown field status")

    authored: list[tuple[SectionDefinition, FieldDefinition]] = []
    seen: set[str] = set()
    for raw_section in sections:
        section = _validated(SectionDefinition, raw_section, "section")
        for raw_field in _attribute(section, "fields"):
            field = _validated(FieldDefinition, raw_field, "field definition")
            if field.id in seen:
                raise _fail("invalid_input", "malformed or duplicate field definition")
            seen.add(field.id)
            authored.append((section, field))

    if not authored:
        raise _fail("invalid_input", "schema definition has no fields")
    if not all(isinstance(field_id, str) for field_id in fields):
        raise _fail("invalid_input", "instance field ids must be strings")
    keys = set(fields)
    if keys != seen:
        code = "schema_mismatch" if keys - seen or seen - keys else "invalid_input"
        raise _fail(code, "instance field ids do not exactly match definition")

    cards: list[FieldCard] = []
    for section, field in authored:
        result = _validated(FieldResult, fields[field.id], "field result")
        if result.status in {"not_found", "not_applicable"} and include_statuses is None:
            continue
        if result.status not in selected:
            continue
        try:
            evidence = tuple(
                FrozenEvidenceRef.model_validate(evidence_ref.model_dump(mode="python"))
                for evidence_ref in result.evidence
                if isinstance(evidence_ref, EvidenceRef)
            )
            if len(evidence) != len(result.evidence):
                raise ValueError("invalid evidence")
            cards.append(FieldCard(
                paper_id=paper_id, schema_id=definition_schema_id, schema_version=definition_version,
                section_id=section.id, section_label=section.label, field_id=field.id,
                field_label=field.label, question=field.question, description=field.description,
                type=field.type, options=field.options, constraints=field.constraints,
                evidence_required=field.evidence_required, allow_inference=field.allow_inference,
                output_guidance=field.output_guidance, value=result.value, status=result.status,
                confidence=result.confidence, notes=result.notes, evidence=evidence,
            ))
        except Exception:
            raise _fail("invalid_input", "malformed field result") from None
    return tuple(cards)