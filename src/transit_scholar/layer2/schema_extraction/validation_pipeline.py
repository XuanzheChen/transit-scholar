"""In-memory validation pipeline for L2S2 Package C (FR-C-007 / AC-C-07).

Fixed stage order:

    structural -> evidence integrity -> semantic -> cross-field
    -> optional targeted recheck -> ValidationReport

Every stage is injectable:

- structural: ``validate_schema_instance`` (Package A + C);
- evidence: ``validate_evidence_integrity`` with an injectable canonical
  reader (a missing reader is an explicit ``canonical_read_failed`` error);
- semantic: ``verify_field_semantics`` with an injectable verifier
  (default: deterministic offline ``FakeSemanticVerifier`` returning
  ``supported``);
- cross-field: a list of read-only validator callables returning
  ``ValidationIssue`` lists (default: empty; the core never imports any
  domain plugin module);
- recheck: optional, only when ``enable_recheck=True`` and a recheck
  callable is provided; target fields are the union of ``fields`` of all
  issues whose ``action == "recheck"``.

The single return value is a ``ValidationReport``. The instance is only
mutated by the optional recheck stage and only for the targeted fields.
"""

from __future__ import annotations

import datetime
from typing import Callable

from .evidence_validation import (
    CanonicalReader,
    validate_evidence_integrity,
)
from .hashing import compute_schema_hash
from .models import SchemaDefinition, SchemaInstance, ValidationIssue
from .recheck import (
    RecheckCallable,
    RecheckTrace,
    run_targeted_recheck,
)
from .semantic import (
    FakeSemanticVerifier,
    SemanticVerifier,
    verify_field_semantics,
)
from .validation import validate_schema_instance
from .validation_report import (
    ValidationReport,
    derive_report_status,
)

#: A read-only cross-field validator: instance -> issues.
CrossFieldValidator = Callable[[SchemaInstance], list[ValidationIssue]]


def validate_schema_instance_in_memory(
    definition: SchemaDefinition,
    instance: SchemaInstance,
    *,
    canonical_reader: CanonicalReader | None = None,
    verifier: SemanticVerifier | None = None,
    cross_field_validators: list[CrossFieldValidator] | None = None,
    recheck_callable: RecheckCallable | None = None,
    enable_recheck: bool = True,
) -> ValidationReport:
    """Run the full validation pipeline in memory and return the report."""
    structural_issues = validate_schema_instance(definition, instance)

    evidence_issues = validate_evidence_integrity(
        instance, canonical_reader, paper_id=instance.paper_id
    )

    semantic_verifier = verifier or FakeSemanticVerifier(
        default_response={
            "decision": "supported",
            "confidence": None,
            "notes": "offline default fake verifier",
        }
    )
    semantic_issues: list[ValidationIssue] = []
    for section in definition.sections:
        for field in section.fields:
            result = instance.fields.get(field.id)
            if result is None:
                continue
            semantic_issues.extend(
                verify_field_semantics(field, result, semantic_verifier)
            )

    cross_field_issues: list[ValidationIssue] = []
    for validator in cross_field_validators or []:
        cross_field_issues.extend(list(validator(instance) or []))

    all_issues = (
        structural_issues
        + evidence_issues
        + semantic_issues
        + cross_field_issues
    )

    recheck_trace = RecheckTrace()
    if enable_recheck and recheck_callable is not None:
        recheck_issues = [i for i in all_issues if i.action == "recheck"]
        target_fields: list[str] = []
        for issue in recheck_issues:
            for field_id in issue.fields:
                if field_id not in target_fields:
                    target_fields.append(field_id)
        if target_fields:
            recheck_trace = run_targeted_recheck(
                definition,
                instance,
                target_fields,
                recheck_callable,
                issues=recheck_issues,
            )

    return ValidationReport(
        paper_id=instance.paper_id,
        schema_id=instance.schema_id,
        schema_version=instance.schema_version,
        schema_hash=compute_schema_hash(definition),
        status=derive_report_status(all_issues, recheck_trace),
        issues=all_issues,
        structural_issues=structural_issues,
        evidence_issues=evidence_issues,
        semantic_issues=semantic_issues,
        cross_field_issues=cross_field_issues,
        recheck_trace=recheck_trace,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


#: Alias kept for FR-C-007 naming flexibility.
run_validation_pipeline_in_memory = validate_schema_instance_in_memory
