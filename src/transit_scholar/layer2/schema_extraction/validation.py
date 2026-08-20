"""Structural validation of a SchemaInstance against a SchemaDefinition
(FR-A-010, extended by FR-C-002).

Pure, deterministic, offline cross-layer checks between the definition and an
instance. Model-level rejections (required fields, field types, enum options,
status vocabulary, confidence range, duplicate field ids) are enforced by
Pydantic at construction time and never reach this function.

Package A rules (fixed order, unchanged): schema id mismatch (error), schema
version mismatch (error), unknown instance field (error), missing definition
field (warning), invalid enum value (error), missing required evidence
(warning).

Package C extensions (FR-C-002 / AC-C-02) run after the Package A rules and
never modify them:

1. value-type matching (``invalid_value_type``, error): string -> str/None,
   number -> int/float but never bool, boolean -> bool, list -> list,
   object -> dict;
2. assertive value with non-assertive status
   (``assertive_value_with_non_assertive_status``, error);
3. confidence sanity on ``model_construct``-built results
   (``invalid_confidence``, error);
4. evidence char range sanity on ``model_construct``-built refs
   (``invalid_evidence_range``, error).

Explicitly out of scope for this module: canonical block existence, quote
substring integrity, semantic support, and cross-field targeted recheck
(those are Package C modules ``evidence_validation`` / ``semantic`` /
``recheck`` / ``validation_pipeline``).
"""

from __future__ import annotations

from typing import Any

from .models import (
    FieldDefinition,
    FieldResult,
    SchemaDefinition,
    SchemaInstance,
    ValidationIssue,
)


def validate_schema_instance(
    definition: SchemaDefinition,
    instance: SchemaInstance,
) -> list[ValidationIssue]:
    """Return structural validation issues between ``definition`` and ``instance``.

    Fixed rule order: schema id mismatch (error), schema version mismatch
    (error), unknown instance field (error), missing definition field
    (warning), invalid enum value (error), missing required evidence
    (warning), then the Package C checks: value-type (error), assertive
    value with non-assertive status (error), confidence (error), evidence
    range (error).
    """
    issues: list[ValidationIssue] = []

    if instance.schema_id != definition.schema_id:
        issues.append(
            ValidationIssue(
                type="schema_mismatch",
                severity="error",
                message=(
                    f"instance schema_id {instance.schema_id!r} does not match "
                    f"definition schema_id {definition.schema_id!r}"
                ),
            )
        )

    if instance.schema_version != definition.version:
        issues.append(
            ValidationIssue(
                type="schema_version_mismatch",
                severity="error",
                message=(
                    f"instance schema_version {instance.schema_version!r} does not "
                    f"match definition version {definition.version!r}"
                ),
            )
        )

    definition_field_ids = {
        field.id for section in definition.sections for field in section.fields
    }

    for field_id in instance.fields:
        if field_id not in definition_field_ids:
            issues.append(
                ValidationIssue(
                    type="unknown_field",
                    severity="error",
                    message=(
                        f"instance field {field_id!r} does not exist in schema "
                        f"{definition.schema_id!r}"
                    ),
                    fields=[field_id],
                )
            )

    for section in definition.sections:
        for field in section.fields:
            if field.id not in instance.fields:
                issues.append(
                    ValidationIssue(
                        type="missing_field",
                        severity="warning",
                        message=(
                            f"definition field {field.id!r} is missing from the "
                            f"instance (incomplete instance)"
                        ),
                        fields=[field.id],
                    )
                )

    for section in definition.sections:
        for field in section.fields:
            result = instance.fields.get(field.id)
            if result is None:
                continue
            if field.type == "enum" and result.value is not None:
                if result.value not in (field.options or []):
                    issues.append(
                        ValidationIssue(
                            type="invalid_enum_value",
                            severity="error",
                            message=(
                                f"field {field.id!r} value {result.value!r} is not a "
                                f"valid option of the enum field"
                            ),
                            fields=[field.id],
                        )
                    )

    for section in definition.sections:
        for field in section.fields:
            result = instance.fields.get(field.id)
            if result is None:
                continue
            if (
                field.evidence_required
                and result.status in ("explicit", "inferred")
                and not result.evidence
            ):
                issues.append(
                    ValidationIssue(
                        type="missing_evidence",
                        severity="warning",
                        message=(
                            f"field {field.id!r} requires evidence but has none "
                            f"while status is {result.status!r}"
                        ),
                        fields=[field.id],
                    )
                )

    # ------------------------------------------------------------------
    # Package C extensions (FR-C-002 / AC-C-02). Appended after the
    # Package A rules; the old rules are untouched above.
    # ------------------------------------------------------------------

    for section in definition.sections:
        for field in section.fields:
            result = instance.fields.get(field.id)
            if result is None:
                continue
            issues.extend(_validate_value_type(field, result))

    for section in definition.sections:
        for field in section.fields:
            result = instance.fields.get(field.id)
            if result is None:
                continue
            issues.extend(_validate_assertive_value(field, result))

    for section in definition.sections:
        for field in section.fields:
            result = instance.fields.get(field.id)
            if result is None:
                continue
            issues.extend(_validate_confidence(field, result))

    for section in definition.sections:
        for field in section.fields:
            result = instance.fields.get(field.id)
            if result is None:
                continue
            issues.extend(_validate_evidence_ranges(field, result))

    return issues


def _validate_value_type(
    field: FieldDefinition,
    result: FieldResult,
) -> list[ValidationIssue]:
    if field.type == "enum":
        return []
    value = result.value
    valid: bool
    expected: str
    if field.type == "string":
        valid = value is None or isinstance(value, str)
        expected = "str or None"
    elif field.type == "number":
        valid = value is None or (
            isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        expected = "int/float (bool is not accepted) or None"
    elif field.type == "boolean":
        valid = value is None or isinstance(value, bool)
        expected = "bool or None"
    elif field.type == "list":
        valid = value is None or isinstance(value, list)
        expected = "list or None"
    else:  # object
        valid = value is None or isinstance(value, dict)
        expected = "dict or None"
    if valid:
        return []
    return [
        ValidationIssue(
            type="invalid_value_type",
            severity="error",
            message=(
                f"field {field.id!r} value {value!r} does not match type "
                f"{field.type!r} (expected {expected})"
            ),
            fields=[field.id],
        )
    ]


def _is_assertive(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


def _validate_assertive_value(
    field: FieldDefinition,
    result: FieldResult,
) -> list[ValidationIssue]:
    if result.status not in ("not_found", "not_applicable"):
        return []
    if not _is_assertive(result.value):
        return []
    return [
        ValidationIssue(
            type="assertive_value_with_non_assertive_status",
            severity="error",
            message=(
                f"field {field.id!r} has status {result.status!r} but an "
                f"assertive value {result.value!r}"
            ),
            fields=[field.id],
        )
    ]


def _validate_confidence(
    field: FieldDefinition,
    result: FieldResult,
) -> list[ValidationIssue]:
    confidence = result.confidence
    if confidence is None:
        return []
    invalid = (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not (0 <= confidence <= 1)
    )
    if not invalid:
        return []
    return [
        ValidationIssue(
            type="invalid_confidence",
            severity="error",
            message=(
                f"field {field.id!r} confidence {confidence!r} is outside "
                f"the valid range [0, 1]"
            ),
            fields=[field.id],
        )
    ]


def _validate_evidence_ranges(
    field: FieldDefinition,
    result: FieldResult,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, ref in enumerate(result.evidence):
        if ref.char_end < ref.char_start or ref.char_start < 0:
            issues.append(
                ValidationIssue(
                    type="invalid_evidence_range",
                    severity="error",
                    message=(
                        f"field {field.id!r} evidence #{index} char range "
                        f"[{ref.char_start}, {ref.char_end}) is invalid "
                        f"(char_end < char_start)"
                    ),
                    fields=[field.id],
                )
            )
    return issues
