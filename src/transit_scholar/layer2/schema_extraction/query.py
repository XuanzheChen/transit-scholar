"""Field-level retrieval query construction (FR-B-003).

``build_field_query`` is a pure, deterministic function that combines the
section label, field label, field question, and field description (when
present) into a query string plus trace-friendly query metadata.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .models import FieldDefinition, SchemaDefinition, SectionDefinition


class FieldQuery(BaseModel):
    """Query string plus metadata suitable for trace recording (FR-B-003)."""

    query: str
    metadata: dict[str, Any]


def build_field_query(
    field: FieldDefinition,
    section: SectionDefinition,
    definition: SchemaDefinition,
) -> FieldQuery:
    """Build a deterministic retrieval query for one schema field.

    The query combines ``section.label``, ``field.label``, ``field.question``
    and, when present, ``field.description``. All four inputs are required
    (label/question are non-empty by Pydantic contract), so the resulting
    query is always non-empty.
    """
    parts: list[str] = []
    for text in (
        section.label,
        field.label,
        field.question,
        field.description,
    ):
        if text:
            parts.append(text.strip())
    query = " | ".join(parts)
    metadata: dict[str, Any] = {
        "schema_id": definition.schema_id,
        "schema_version": definition.version,
        "section_id": section.id,
        "section_label": section.label,
        "field_id": field.id,
        "field_label": field.label,
        "field_type": field.type,
    }
    return FieldQuery(query=query, metadata=metadata)
