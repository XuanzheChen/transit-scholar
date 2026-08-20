"""L2S2 Package E metric aggregation (AC-E-32..35).

Defines the fixed metric vocabulary (names match requirements section 6 and
AC-E-32 verbatim) and the three aggregation layers: overall, per paper, per
field id. All computations are pure and deterministic: counts follow the
gold field order, rates are rounded to four decimals, and division by zero
yields ``None``.

The locked counting convention (Planner-approved):

- ``missing_prediction`` fields count ONLY in ``missing_prediction_count``
  (plus ``field_count`` and ``issue_count``); they enter no value bucket and
  no value/status accuracy denominator;
- hence the invariant
  ``value_correct + value_partial + value_incorrect + value_not_evaluated ==
  field_count - missing_prediction_count`` always holds (AC-E-33).

This module imports only stdlib, pydantic and the sibling Package E
evaluation models.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .evaluate import AcceptanceIssue, FieldEvaluation, PaperEvaluation

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class AggregateMetrics(BaseModel):
    """The fixed L2S2 Package E metric vocabulary (AC-E-32)."""

    model_config = ConfigDict(extra="allow")

    paper_count: int = 0
    field_count: int = 0
    missing_prediction_count: int = 0
    value_not_evaluated_count: int = 0
    evaluated_field_count: int = 0
    value_correct_count: int = 0
    value_partial_count: int = 0
    value_incorrect_count: int = 0
    value_accuracy: float | None = None
    status_accuracy: float | None = None
    evidence_required_count: int = 0
    evidence_present_rate: float | None = None
    weak_traceability_rate: float | None = None
    strict_traceability_rate: float | None = None
    not_found_correctness: float | None = None
    issue_count: int = 0


class FieldAggregate(BaseModel):
    """Per-field-id aggregation (AC-E-34)."""

    model_config = ConfigDict(extra="allow")

    field_id: str
    correct: int = 0
    partial: int = 0
    incorrect: int = 0
    not_evaluated: int = 0
    missing: int = 0
    status_mismatch: int = 0
    issue_count: int = 0
    issues: list[AcceptanceIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# computation
# ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def compute_metrics_for_fields(
    fields: list[FieldEvaluation],
    *,
    paper_count: int,
    extra_issue_count: int = 0,
) -> AggregateMetrics:
    """Compute the full metric block for one set of field evaluations."""
    field_count = len(fields)
    missing = sum(1 for f in fields if f.prediction_missing)
    not_evaluated = sum(
        1
        for f in fields
        if not f.prediction_missing and f.effective_judgement == "not_evaluated"
    )
    correct = sum(
        1
        for f in fields
        if not f.prediction_missing and f.effective_judgement == "correct"
    )
    partial = sum(
        1
        for f in fields
        if not f.prediction_missing and f.effective_judgement == "partially_correct"
    )
    incorrect = sum(
        1
        for f in fields
        if not f.prediction_missing and f.effective_judgement == "incorrect"
    )
    evaluated = field_count - missing - not_evaluated

    status_den = sum(
        1
        for f in fields
        if not f.prediction_missing and f.expected_status is not None
    )
    status_num = sum(
        1
        for f in fields
        if not f.prediction_missing
        and f.expected_status is not None
        and f.status_match is True
    )

    required = sum(1 for f in fields if f.evidence_expectation == "required")
    present = sum(
        1
        for f in fields
        if f.evidence_expectation == "required" and f.evidence_present
    )

    weak_ok = sum(f.weak_ok_refs for f in fields)
    weak_fail = sum(f.weak_fail_refs for f in fields)
    weak_total = weak_ok + weak_fail

    strict_ok = sum(f.strict_ok_refs or 0 for f in fields)
    strict_fail = sum(f.strict_fail_refs or 0 for f in fields)
    strict_total = strict_ok + strict_fail

    absent = [f for f in fields if f.expected_status in ("not_found", "not_applicable")]
    absent_correct = sum(1 for f in absent if f.absent_correct is True)

    field_issue_count = sum(len(f.issues) for f in fields)

    return AggregateMetrics(
        paper_count=paper_count,
        field_count=field_count,
        missing_prediction_count=missing,
        value_not_evaluated_count=not_evaluated,
        evaluated_field_count=evaluated,
        value_correct_count=correct,
        value_partial_count=partial,
        value_incorrect_count=incorrect,
        value_accuracy=_rate(correct, evaluated),
        status_accuracy=_rate(status_num, status_den),
        evidence_required_count=required,
        evidence_present_rate=_rate(present, required),
        weak_traceability_rate=_rate(weak_ok, weak_total),
        strict_traceability_rate=_rate(strict_ok, strict_total) if strict_total > 0 else None,
        not_found_correctness=_rate(absent_correct, len(absent)),
        issue_count=field_issue_count + extra_issue_count,
    )


def compute_paper_metrics(paper: PaperEvaluation) -> AggregateMetrics:
    """Per-paper metric block (AC-E-34)."""
    return compute_metrics_for_fields(
        paper.fields,
        paper_count=1,
        extra_issue_count=len(paper.issues),
    )


def compute_overall_metrics(
    papers: list[PaperEvaluation],
    extra_issue_count: int = 0,
) -> AggregateMetrics:
    """Overall metric block across all evaluated papers (AC-E-34)."""
    all_fields: list[FieldEvaluation] = []
    paper_issue_count = 0
    for paper in papers:
        paper_issue_count += len(paper.issues)
        all_fields.extend(paper.fields)
    return compute_metrics_for_fields(
        all_fields,
        paper_count=len(papers),
        extra_issue_count=paper_issue_count + extra_issue_count,
    )


def compute_per_field(papers: list[PaperEvaluation]) -> dict[str, FieldAggregate]:
    """Per-field-id aggregation across all papers (AC-E-34).

    Iteration order is deterministic (papers in gold order, fields in gold
    order), so the resulting dict preserves first-seen field order.
    """
    aggregates: dict[str, FieldAggregate] = {}
    for paper in papers:
        for field_eval in paper.fields:
            agg = aggregates.get(field_eval.field_id)
            if agg is None:
                agg = FieldAggregate(field_id=field_eval.field_id)
                aggregates[field_eval.field_id] = agg
            if field_eval.prediction_missing:
                agg.missing += 1
            elif field_eval.effective_judgement == "correct":
                agg.correct += 1
            elif field_eval.effective_judgement == "partially_correct":
                agg.partial += 1
            elif field_eval.effective_judgement == "incorrect":
                agg.incorrect += 1
            elif field_eval.effective_judgement == "not_evaluated":
                agg.not_evaluated += 1
            if (
                not field_eval.prediction_missing
                and field_eval.expected_status is not None
                and field_eval.status_match is False
            ):
                agg.status_mismatch += 1
            agg.issues.extend(field_eval.issues)
    for agg in aggregates.values():
        agg.issue_count = len(agg.issues)
    return aggregates
