"""Targeted recheck mechanism for L2S2 Package C (FR-C-006 / AC-C-06).

``run_targeted_recheck`` re-extracts a bounded set of fields through an
injectable callable:

    recheck(definition, field, paper_id) -> FieldResult

Rules (fixed by the G plan, decision point 5):

- each target field is rechecked at most once, even when several issues
  point at it;
- only the targeted fields are updated; every other field stays untouched;
- a successful callable result (a valid ``FieldResult``) replaces the field
  and records ``updated=True``;
- a callable exception records ``error_code="recheck_failed"`` and keeps the
  original result; an invalid return records ``"recheck_invalid_result"``;
  failures are never masqueraded as ``not_found``;
- a legal result with status ``unclear``/``conflicting``/``not_found`` is a
  normal recheck conclusion and replaces the field as-is;
- a target field absent from the instance (or unknown to the definition)
  records ``"recheck_field_missing"`` without calling the callable.

Everything is in-memory and JSON serializable via ``model_dump_json()``.
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from .models import (
    FieldDefinition,
    FieldResult,
    SchemaDefinition,
    SchemaInstance,
    ValidationIssue,
)

#: Injectable recheck callable (G plan decision point 5).
RecheckCallable = Callable[[SchemaDefinition, FieldDefinition, str], FieldResult]


class RecheckError(Exception):
    """A targeted recheck could not complete (system failure)."""

    def __init__(self, message: str = "", *, error_code: str = "recheck_failed"):
        super().__init__(message or error_code)
        self.error_code = error_code


class RecheckTraceEntry(BaseModel):
    """Per-field recheck trace (FR-C-006)."""

    field_id: str
    reason: str
    original_status: str
    new_status: str
    updated: bool
    error_code: str | None = None
    error_message: str | None = None


class RecheckTrace(BaseModel):
    """Recheck trace of one targeted recheck run (FR-C-006)."""

    entries: list[RecheckTraceEntry] = Field(default_factory=list)


def run_targeted_recheck(
    definition: SchemaDefinition,
    instance: SchemaInstance,
    field_ids: list[str],
    recheck_callable: RecheckCallable,
    *,
    issues: list[ValidationIssue] | None = None,
) -> RecheckTrace:
    """Recheck the given fields at most once each and update the instance.

    The instance is updated in place, but only for the targeted fields. The
    returned ``RecheckTrace`` explains every field-level decision.
    """
    recheck_issues = [
        issue for issue in (issues or []) if issue.action == "recheck"
    ]
    reasons_by_field: dict[str, list[str]] = {}
    for issue in recheck_issues:
        for field_id in issue.fields:
            reasons = reasons_by_field.setdefault(field_id, [])
            if issue.message not in reasons:
                reasons.append(issue.message)

    definition_fields = {
        field.id: field
        for section in definition.sections
        for field in section.fields
    }

    entries: list[RecheckTraceEntry] = []
    for field_id in _dedupe(field_ids):
        reason = "; ".join(reasons_by_field.get(field_id, []))
        if not reason:
            reason = "manual recheck request"
        field = definition_fields.get(field_id)
        original = instance.fields.get(field_id)

        if field is None or original is None:
            absent_message = (
                f"field {field_id!r} does not exist in schema "
                f"{definition.schema_id!r}"
                if field is None
                else f"field {field_id!r} is missing from the instance"
            )
            entries.append(
                RecheckTraceEntry(
                    field_id=field_id,
                    reason=reason,
                    original_status="absent",
                    new_status="absent",
                    updated=False,
                    error_code="recheck_field_missing",
                    error_message=absent_message,
                )
            )
            continue

        try:
            new_result = recheck_callable(definition, field, instance.paper_id)
        except Exception as exc:  # noqa: BLE001 - explicit trace entry
            entries.append(
                RecheckTraceEntry(
                    field_id=field_id,
                    reason=reason,
                    original_status=original.status,
                    new_status=original.status,
                    updated=False,
                    error_code="recheck_failed",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        try:
            validated = FieldResult.model_validate(new_result)
        except Exception as exc:  # noqa: BLE001 - explicit trace entry
            entries.append(
                RecheckTraceEntry(
                    field_id=field_id,
                    reason=reason,
                    original_status=original.status,
                    new_status=original.status,
                    updated=False,
                    error_code="recheck_invalid_result",
                    error_message=(
                        f"recheck callable returned a non-valid FieldResult "
                        f"({type(exc).__name__})"
                    ),
                )
            )
            continue

        instance.fields[field_id] = validated
        entries.append(
            RecheckTraceEntry(
                field_id=field_id,
                reason=reason,
                original_status=original.status,
                new_status=validated.status,
                updated=True,
            )
        )

    return RecheckTrace(entries=entries)


def _dedupe(field_ids: list[str]) -> list[str]:
    seen: list[str] = []
    for field_id in field_ids:
        if field_id not in seen:
            seen.append(field_id)
    return seen
