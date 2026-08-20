"""L2S2 Schema core data model (Package A, FR-A-001..004).

Definition layer uses ``list`` structures to preserve reading/display order;
the instance layer uses a ``field_id -> FieldResult`` map. All models are
Pydantic v2 ``BaseModel`` subclasses with JSON round-trip support. This module
imports only stdlib + pydantic: no database, no LLM, no network, and no L2S1
dependency.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

#: V1 fixed field type vocabulary (FR-A-002).
FieldType = Literal["string", "number", "boolean", "enum", "list", "object"]

#: V1 fixed field status vocabulary (FR-A-003). ``not_found`` and
#: ``not_applicable`` are deliberately distinct members.
FieldStatus = Literal[
    "explicit",
    "inferred",
    "unclear",
    "not_found",
    "not_applicable",
    "conflicting",
]

#: Validation issue severity vocabulary (FR-A-010 / validation layer).
ValidationSeverity = Literal["error", "warning"]

FIELD_TYPES: frozenset[str] = frozenset(
    {"string", "number", "boolean", "enum", "list", "object"}
)
FIELD_STATUSES: frozenset[str] = frozenset(
    {"explicit", "inferred", "unclear", "not_found", "not_applicable", "conflicting"}
)


class FieldDefinition(BaseModel):
    """Definition of a single schema field (FR-A-002).

    ``output_guidance`` is additive structural / object-skeleton guidance used
    by the extraction prompt (e.g. a stable minimal object skeleton for
    complex ``object`` fields). It never changes the field's type, options,
    question or description semantics.
    """

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    question: str = Field(min_length=1)
    description: str = ""
    type: FieldType
    options: list[str] | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    evidence_required: bool = False
    allow_inference: bool = True
    output_guidance: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_enum_options(self) -> "FieldDefinition":
        if self.type == "enum":
            if not self.options:
                raise ValueError(
                    f"enum field {self.id!r} must define a non-empty options list"
                )
            if len(self.options) != len(set(self.options)):
                raise ValueError(
                    f"enum field {self.id!r} options must be unique"
                )
        return self


class SectionDefinition(BaseModel):
    """Definition of a schema section; ``fields`` keeps display order."""

    id: str = Field(min_length=1)
    label: str
    fields: list[FieldDefinition] = Field(min_length=1)


class SchemaDefinition(BaseModel):
    """Root schema definition loaded from a plugin ``schema.yaml``.

    ``status_semantics`` is an additive documentation block carrying the
    frozen status 口径 (``explicit`` / ``inferred`` / ``not_found`` /
    ``not_applicable`` / ``unclear`` / ``conflicting``). It is never used by
    the extraction runtime to alter a predicted status; it only documents the
    semantics for prompts and reports.
    """

    schema_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sections: list[SectionDefinition] = Field(min_length=1)
    name: str | None = None
    description: str | None = None
    status_semantics: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_globally_unique_field_ids(self) -> "SchemaDefinition":
        seen: dict[str, str] = {}
        for section in self.sections:
            for field in section.fields:
                if field.id in seen:
                    raise ValueError(
                        f"duplicate field id {field.id!r} in sections "
                        f"{seen[field.id]!r} and {section.id!r}"
                    )
                seen[field.id] = section.id
        return self


class EvidenceRef(BaseModel):
    """Evidence pointer into the L2S1 canonical layer (FR-A-004).

    Package A only defines the structure and basic range checks; canonical
    block existence and quote-substring integrity validation are Package C.
    """

    block_id: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    pages: list[int] = Field(default_factory=list)
    section_path: list[str] = Field(default_factory=list)
    quote: str = ""

    @model_validator(mode="after")
    def _check_char_range(self) -> "EvidenceRef":
        if self.char_end < self.char_start:
            raise ValueError(
                f"char_end ({self.char_end}) must be >= char_start ({self.char_start})"
            )
        return self


class FieldResult(BaseModel):
    """Extracted result for one field (FR-A-003)."""

    value: Any = None
    status: FieldStatus
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str | None = None


class SchemaInstance(BaseModel):
    """Extraction result for one paper under one schema (FR-A-001)."""

    paper_id: str
    schema_id: str
    schema_version: str
    fields: dict[str, FieldResult]


class ValidationIssue(BaseModel):
    """A single validation finding (structural or domain cross-field)."""

    type: str
    severity: ValidationSeverity
    message: str = Field(min_length=1)
    fields: list[str] = Field(default_factory=list)
    action: str | None = None


def value_matches_field_type(
    field_type: str,
    value: Any,
    options: list[str] | None = None,
) -> bool:
    """Pure predicate: does ``value`` match ``field_type`` structurally?

    Single source of truth shared by the engine (write-time enforcement,
    FR-002 / AC-T001-F07) and the validation backstop. ``None`` always
    matches (absent values are legal). Rules:

    - ``string``  -> ``str``
    - ``number``  -> ``int``/``float`` but never ``bool``
    - ``boolean`` -> ``bool``
    - ``enum``    -> one of ``options``
    - ``list``    -> ``list``
    - ``object``  -> ``dict``
    """
    if value is None:
        return True
    if field_type == "string":
        return isinstance(value, str)
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "enum":
        return value in (options or [])
    if field_type == "list":
        return isinstance(value, list)
    if field_type == "object":
        return isinstance(value, dict)
    return False
