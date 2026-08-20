"""Validation report model for L2S2 Package C (FR-C-001 / AC-C-01).

``ValidationReport`` is the single in-memory aggregation of the validation
pipeline: four issue buckets (structural / evidence / semantic / cross-field),
a flat ``issues`` view preserving stage order, an optional ``RecheckTrace``,
and a derived ``status``.

The report is pure Pydantic: JSON serializable, never written to disk. The
status derivation rule is fixed and deterministic:

1. any ``severity="error"`` issue -> ``failed``;
2. otherwise a non-empty recheck trace -> ``needs_recheck``;
3. otherwise any ``severity="warning"`` issue -> ``warning``;
4. otherwise ``passed``.

This module imports only stdlib, pydantic, and the Package C recheck model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .models import ValidationIssue
from .recheck import RecheckTrace

#: Fixed validation report status vocabulary (FR-C-001).
ReportStatus = Literal["passed", "warning", "failed", "needs_recheck"]


class ValidationReport(BaseModel):
    """In-memory validation result for one SchemaInstance (FR-C-001)."""

    paper_id: str
    schema_id: str
    schema_version: str
    schema_hash: str | None = None
    status: ReportStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    structural_issues: list[ValidationIssue] = Field(default_factory=list)
    evidence_issues: list[ValidationIssue] = Field(default_factory=list)
    semantic_issues: list[ValidationIssue] = Field(default_factory=list)
    cross_field_issues: list[ValidationIssue] = Field(default_factory=list)
    recheck_trace: RecheckTrace = Field(default_factory=RecheckTrace)
    created_at: str = ""


def derive_report_status(
    issues: list[ValidationIssue],
    recheck_trace: RecheckTrace,
) -> ReportStatus:
    """Derive the report status with the fixed deterministic rule (AC-C-01.2)."""
    if any(issue.severity == "error" for issue in issues):
        return "failed"
    if recheck_trace.entries:
        return "needs_recheck"
    if any(issue.severity == "warning" for issue in issues):
        return "warning"
    return "passed"
