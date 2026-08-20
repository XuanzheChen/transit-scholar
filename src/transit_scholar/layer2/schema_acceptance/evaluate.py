"""L2S2 Package E evaluation engine (AC-E-17..31).

Evaluates one ``SchemaInstance`` against one gold paper entry
(``evaluate_schema_instance``) or evaluates a whole gold benchmark against
Package D persisted schema runs (``evaluate_schema_gold``).

Design rules locked by the frozen acceptance criteria:

- ``missing_prediction`` fields are counted ONLY in ``missing_prediction_count``
  (and ``issue_count``); they never enter the value buckets, so the invariant
  ``value_correct + value_partial + value_incorrect + value_not_evaluated ==
  field_count - missing_prediction_count`` always holds.
- Simple-typed fields get an automatic ``value_match``; list/object fields
  never get an automatic semantic judgement (``value_match = null``) and the
  gold ``value_judgement`` is authoritative.
- Strict traceability reuses Package C ``validate_evidence_integrity``; it is
  never re-implemented here and is never invoked when no canonical reader is
  provided (mode ``weak_only`` vs ``strict`` vs ``strict_failed``).
- No LLM, verifier, network, PDF or storage write ever happens here; storage
  is read-only through Package D ``get_schema``.

This module imports only stdlib, pydantic and the stable public exports of
``transit_scholar.layer2.schema_extraction``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer2.schema_extraction import (
    EvidenceRef,
    SchemaIdMismatchError,
    SchemaInstance,
    SchemaRunStorage,
    SchemaStorageError,
    ValidationIssue,
    ValidationReport,
    get_schema,
    get_schema_definition,
    validate_evidence_integrity,
)

from .gold import (
    GOLD_STATUS_DRAFT,
    GOLD_STATUS_TEMPLATE,
    GoldBenchmark,
    GoldField,
    GoldPaper,
    validate_schema_gold,
)

# ---------------------------------------------------------------------------
# vocabularies
# ---------------------------------------------------------------------------

SIMPLE_FIELD_TYPES: frozenset[str] = frozenset(
    {"string", "number", "boolean", "enum"}
)
ABSENT_STATUSES: frozenset[str] = frozenset({"not_found", "not_applicable"})

RunKind = Literal["current", "historical", "in_memory"]
StrictMode = Literal["strict", "weak_only", "strict_failed"]
IssueSeverity = Literal["error", "warning"]


# ---------------------------------------------------------------------------
# issue model
# ---------------------------------------------------------------------------


class AcceptanceIssue(BaseModel):
    """A single Package E issue with an explicit provenance source.

    Mirrors the Package A ``ValidationIssue`` shape (``type``/``severity``/
    ``message``/``fields``/``action``) and adds ``source`` so merged
    validation-report / evidence-integrity / gold-validation issues stay
    traceable to their origin (AC-E-06). The optional ``explanation`` carries
    the machine-readable status 口径 rationale for explainable status
    mismatches (AC-T001-S3).
    """

    model_config = ConfigDict(extra="allow")

    type: str
    severity: IssueSeverity
    message: str = Field(min_length=1)
    fields: list[str] = Field(default_factory=list)
    action: str | None = None
    source: str = "evaluation"
    explanation: str | None = None

    @classmethod
    def from_validation_issue(
        cls, issue: ValidationIssue, source: str
    ) -> "AcceptanceIssue":
        return cls(
            type=issue.type,
            severity=issue.severity,
            message=issue.message,
            fields=list(issue.fields),
            action=issue.action,
            source=source,
        )


# ---------------------------------------------------------------------------
# result models
# ---------------------------------------------------------------------------


class FieldEvaluation(BaseModel):
    """Field-level evaluation result (AC-E-38 keys plus helpers)."""

    model_config = ConfigDict(extra="allow")

    field_id: str
    expected_status: str | None = None
    predicted_status: str | None = None
    value_judgement: str | None = None
    effective_judgement: str | None = None
    value_match: bool | None = None
    status_match: bool | None = None
    status_explanation: str | None = None
    gold_review: bool = False
    evidence_expectation: str | None = None
    evidence_present: bool = False
    evidence_support: bool | None = None
    weak_ok_refs: int = 0
    weak_fail_refs: int = 0
    strict_mode: StrictMode = "weak_only"
    strict_ok_refs: int | None = None
    strict_fail_refs: int | None = None
    absent_correct: bool | None = None
    prediction_missing: bool = False
    issues: list[AcceptanceIssue] = Field(default_factory=list)


class PaperEvaluation(BaseModel):
    """Per-paper evaluation result (AC-E-17, FR-006)."""

    model_config = ConfigDict(extra="allow")

    paper_id: str
    title: str | None = None
    pdf_relative_path: str | None = None
    schema_id: str | None = None
    schema_version: str | None = None
    selected_for_v1: bool | None = None
    template: bool = False
    draft: bool = False
    run_kind: RunKind = "in_memory"
    run_id: str | None = None
    paper_error: str | None = None
    fields: list[FieldEvaluation] = Field(default_factory=list)
    issues: list[AcceptanceIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _eissue(
    type_: str,
    severity: str,
    message: str,
    *,
    fields: list[str] | None = None,
    source: str = "evaluation",
) -> AcceptanceIssue:
    return AcceptanceIssue(
        type=type_,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        fields=fields or [],
        source=source,
    )


def _is_empty_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


#: Notes markers suggesting the gold itself records the field as absent
#: (used by the gold-status-review detection, AC-T001-S4).
_ABSENCE_NOTE_MARKERS: tuple[str, ...] = (
    "not found",
    "not_found",
    "not applicable",
    "absent",
    "missing",
    "does not exist",
    "not present",
    "不存在",
    "未出现",
    "未被提及",
    "无此",
)


def _notes_indicate_absence(notes: str | None) -> bool:
    if not isinstance(notes, str) or not notes:
        return False
    lowered = notes.lower()
    return any(marker in lowered for marker in _ABSENCE_NOTE_MARKERS)


def _is_gold_status_review_candidate(gold_field: GoldField) -> str | None:
    """AC-T001-S4: detect when the gold ``expected_status`` contradicts the
    gold's own content per the frozen 口径.

    - expected absent but ``acceptable_values`` non-empty; or
    - expected assertive (explicit/inferred) but ``acceptable_values``
      null/empty with ``evidence_expectation == "not_required"`` and notes
      indicating absence.

    Returns a human-readable rationale string or ``None``. Never modifies gold
    (F-01): these candidates are surfaced for user confirmation only.
    """
    expected = gold_field.expected_status
    av = gold_field.acceptable_values
    if expected in ABSENT_STATUSES and not _is_empty_value(av):
        return (
            "gold expected_status is absent (not_found/not_applicable) but "
            "the gold carries a non-empty acceptable_values; per the frozen "
            "status 口径 (explicit = direct statement in the source) an "
            "absent status and concrete acceptable values contradict each "
            "other — please confirm the gold status"
        )
    if expected in ("explicit", "inferred"):
        if (
            _is_empty_value(av)
            and gold_field.evidence_expectation == "not_required"
            and _notes_indicate_absence(gold_field.notes)
        ):
            return (
                "gold expected_status is assertive (explicit/inferred) but "
                "the gold field has no acceptable_values, evidence is not "
                "required and the notes describe the field as absent; per the "
                "frozen status 口径 the gold status may be wrong — please "
                "confirm or amend the gold status"
            )
    return None


def _classify_status_mismatch(
    expected_status: str,
    predicted_status: str,
    predicted_value: Any,
    gold_field: GoldField,
) -> tuple[str, str, str | None]:
    """Deterministic status-mismatch classification (AC-T001-S3).

    Returns ``(issue_type, severity, explanation)``. Exactly one bucket per
    field:
    - blocking error: absent-confusion, expected-absent vs assertive value,
      expected-assertive vs absent-with-fact, expected-assertive vs
      unclear/conflicting;
    - non-blocking explainable warning: explicit<->inferred swap,
      unclear/conflicting-vs-anything on non-assertive expected statuses.

    Gold-status-review candidates are handled separately (AC-T001-S4) and are
    never emitted by this classifier.
    """
    if expected_status in ABSENT_STATUSES:
        if predicted_status in ABSENT_STATUSES:
            return (
                "not_found_not_applicable_confused",
                "error",
                "expected an absent status (not_found/not_applicable) but "
                "predicted a different absent status; absent classes are "
                "deliberately distinct per the status 口径",
            )
        if predicted_status in ("explicit", "inferred") and not _is_empty_value(
            predicted_value
        ):
            return (
                "not_found_status_mismatch",
                "error",
                "expected an absent status but predicted an assertive status "
                "with a non-empty value; the value appears hallucinated",
            )
        return (
            "status_mismatch",
            "warning",
            "expected an absent status but predicted a non-assertive status "
            "with no fact asserted; explainable status 口径 mismatch, "
            "non-blocking diagnostic",
        )

    if expected_status in ("explicit", "inferred"):
        if predicted_status in ABSENT_STATUSES:
            if not _is_empty_value(gold_field.acceptable_values):
                return (
                    "status_mismatch",
                    "error",
                    "expected an assertive status (explicit/inferred) but "
                    "predicted absent while gold carries a non-empty "
                    "acceptable_values; the fact demonstrably exists",
                )
            return (
                "status_mismatch",
                "warning",
                "expected an assertive status but predicted absent, and the "
                "gold carries no acceptable_values to confirm the fact; "
                "explainable status 口径 mismatch, non-blocking diagnostic",
            )
        if predicted_status in ("unclear", "conflicting"):
            return (
                "status_mismatch",
                "error",
                "expected an assertive status (explicit/inferred) but "
                "predicted unclear/conflicting; the system could not resolve "
                "a real fact",
            )
        return (
            "status_mismatch",
            "warning",
            "explicit/inferred 口径: explicit = 原文存在直接陈述/直接等价术语/"
            "表格图注实验设置直接事实, inferred = 需要跨句归纳/领域分类映射/"
            "多事实综合推出; predicted and gold disagree but both are "
            "assertive — explainable, non-blocking diagnostic",
        )

    if expected_status in ("unclear", "conflicting"):
        return (
            "status_mismatch",
            "warning",
            "expected unclear/conflicting but predicted a different status; "
            "per the frozen status 口径 this is an explainable non-blocking "
            "status mismatch",
        )

    return (
        "status_mismatch",
        "warning",
        "unclassified status mismatch (expected/predicted not covered by the "
        "frozen status classification)",
    )


def _definition_field_types(schema_id: str) -> dict[str, str]:
    try:
        definition = get_schema_definition(schema_id)
    except Exception:  # noqa: BLE001 - caller already validated the schema id
        return {}
    return {
        field.id: field.type
        for section in definition.sections
        for field in section.fields
    }


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (list, dict))


def _compute_value_match(
    field_type: str | None,
    acceptable_values: Any,
    predicted_value: Any,
) -> bool | None:
    """AC-E-20/21: automatic match only for simple scalar cases."""
    if field_type not in SIMPLE_FIELD_TYPES:
        return None
    av = acceptable_values
    if av is None:
        return None
    if isinstance(av, bool) or isinstance(av, (int, float, str)):
        return predicted_value == av
    if isinstance(av, list):
        if not av:
            return None
        if all(_is_scalar(item) for item in av):
            return predicted_value in av
        return None
    if isinstance(av, dict):
        return None
    return None


def _effective_judgement(value_judgement: str | None) -> str:
    if value_judgement is None or value_judgement == "not_evaluated":
        return "not_evaluated"
    return value_judgement


def _judgement_conflict(
    field_type: str | None, value_match: bool | None, judgement: str | None
) -> bool:
    if field_type not in SIMPLE_FIELD_TYPES:
        return False
    if value_match is None or judgement not in ("correct", "incorrect"):
        return False
    return (value_match is True and judgement == "incorrect") or (
        value_match is False and judgement == "correct"
    )


def _weak_ref_problems(ref: EvidenceRef) -> list[str]:
    problems: list[str] = []
    if not isinstance(ref.block_id, str) or not ref.block_id:
        problems.append("block_id missing or empty")
    if (
        not isinstance(ref.char_start, int)
        or not isinstance(ref.char_end, int)
        or ref.char_start < 0
        or ref.char_end < ref.char_start
    ):
        problems.append(f"invalid char range [{ref.char_start!r}, {ref.char_end!r}]")
    if not isinstance(ref.pages, list):
        problems.append(f"pages is {type(ref.pages).__name__!r}, expected list")
    if not isinstance(ref.quote, str) or not ref.quote:
        problems.append("quote missing or empty")
    if not isinstance(ref.section_path, list):
        problems.append(
            f"section_path is {type(ref.section_path).__name__!r}, expected list"
        )
    return problems


def _ref_identity(ref: EvidenceRef, index: int) -> str:
    block = ref.block_id if isinstance(ref.block_id, str) else repr(ref.block_id)
    return f"evidence ref #{index} (block_id={block!r})"


# ---------------------------------------------------------------------------
# template / paper-error shortcuts
# ---------------------------------------------------------------------------


def _template_paper_evaluation(entry: GoldPaper) -> PaperEvaluation:
    return PaperEvaluation(
        paper_id=entry.paper_id or "",
        title=entry.title,
        pdf_relative_path=entry.pdf_relative_path,
        schema_id=entry.schema_id,
        schema_version=entry.schema_version,
        selected_for_v1=(
            entry.selected_for_v1 if isinstance(entry.selected_for_v1, bool) else None
        ),
        template=True,
        issues=[
            _eissue(
                "template_entry_skipped",
                "warning",
                f"paper {entry.paper_id!r} gold entry is a template awaiting "
                f"human review; its fields were skipped and are excluded from "
                f"every quality denominator",
            )
        ],
    )


def _draft_paper_evaluation(entry: GoldPaper) -> PaperEvaluation:
    return PaperEvaluation(
        paper_id=entry.paper_id or "",
        title=entry.title,
        pdf_relative_path=entry.pdf_relative_path,
        schema_id=entry.schema_id,
        schema_version=entry.schema_version,
        selected_for_v1=(
            entry.selected_for_v1 if isinstance(entry.selected_for_v1, bool) else None
        ),
        draft=True,
        issues=[
            _eissue(
                "draft_entry_skipped",
                "warning",
                f"paper {entry.paper_id!r} gold entry is an LLM-assisted "
                f"draft (non-human gold) awaiting human review; its fields "
                f"were skipped and are excluded from every quality "
                f"denominator. A draft must never be used to declare a "
                f"freeze.",
            )
        ],
    )


# ---------------------------------------------------------------------------
# field-level evaluation
# ---------------------------------------------------------------------------


def evaluate_schema_instance(
    instance: SchemaInstance,
    gold_entry: GoldPaper,
    *,
    validation_report: ValidationReport | None = None,
    canonical_reader: Any = None,
) -> PaperEvaluation:
    """Evaluate one ``SchemaInstance`` against one gold paper entry (AC-E-17..31).

    Returns a ``PaperEvaluation`` with per-field results, paper-level issues
    and provenance. Purely deterministic and offline: it never calls any LLM,
    verifier, network service or PDF reader. A passed ``validation_report``
    has its issues merged (source ``validation_report``); without one, no
    semantic judgement is invented.
    """
    paper = PaperEvaluation(
        paper_id=gold_entry.paper_id or "",
        title=gold_entry.title,
        pdf_relative_path=gold_entry.pdf_relative_path,
        schema_id=gold_entry.schema_id,
        schema_version=gold_entry.schema_version,
        selected_for_v1=(
            gold_entry.selected_for_v1 if isinstance(gold_entry.selected_for_v1, bool) else None
        ),
        template=(gold_entry.gold_status == GOLD_STATUS_TEMPLATE),
        draft=(gold_entry.gold_status == GOLD_STATUS_DRAFT),
    )
    if paper.template:
        return _template_paper_evaluation(gold_entry)
    if paper.draft:
        return _draft_paper_evaluation(gold_entry)

    # -- pre-consistency (AC-E-18) ------------------------------------------
    if (
        instance.schema_id != gold_entry.schema_id
        or instance.paper_id != gold_entry.paper_id
    ):
        paper.paper_error = "schema_mismatch"
        paper.issues.append(
            _eissue(
                "schema_mismatch",
                "error",
                f"paper {paper.paper_id!r}: instance identity "
                f"(paper_id={instance.paper_id!r}, schema_id="
                f"{instance.schema_id!r}) does not match gold "
                f"(paper_id={gold_entry.paper_id!r}, schema_id="
                f"{gold_entry.schema_id!r})",
            )
        )
        return paper

    # -- merged validation report issues (AC-E-06) --------------------------
    if validation_report is not None:
        for issue in validation_report.issues:
            paper.issues.append(
                AcceptanceIssue.from_validation_issue(issue, "validation_report")
            )

    field_types = _definition_field_types(instance.schema_id)

    # -- strict traceability (AC-E-27/28) -----------------------------------
    strict_mode: StrictMode = "weak_only"
    integrity_issues: list[ValidationIssue] = []
    if canonical_reader is not None:
        integrity_issues = validate_evidence_integrity(instance, canonical_reader)
        if any(i.type == "canonical_read_failed" for i in integrity_issues):
            strict_mode = "strict_failed"
            for issue in integrity_issues:
                if issue.type == "canonical_read_failed":
                    paper.issues.append(
                        AcceptanceIssue.from_validation_issue(issue, "evidence_integrity")
                    )
        else:
            strict_mode = "strict"

    # -- per gold field (deterministic gold order) --------------------------
    for gold_field in gold_entry.fields:
        field_eval = _evaluate_one_field(
            instance,
            gold_field,
            field_types,
            strict_mode,
            integrity_issues,
        )
        paper.fields.append(field_eval)

    return paper


def _evaluate_one_field(
    instance: SchemaInstance,
    gold_field: GoldField,
    field_types: dict[str, str],
    strict_mode: StrictMode,
    integrity_issues: list[ValidationIssue],
) -> FieldEvaluation:
    field_id = gold_field.field_id or ""
    expected_status = gold_field.expected_status
    field_eval = FieldEvaluation(
        field_id=field_id,
        expected_status=expected_status,
        value_judgement=gold_field.value_judgement,
        evidence_expectation=gold_field.evidence_expectation,
        strict_mode=strict_mode,
    )

    predicted = instance.fields.get(field_id)
    if predicted is None:
        # missing prediction (AC-E-19): counted only in missing_prediction
        field_eval.prediction_missing = True
        field_eval.issues.append(
            _eissue(
                "missing_prediction",
                "error",
                f"field {field_id!r}: no prediction exists in the schema "
                f"instance",
                fields=[field_id],
            )
        )
        return field_eval

    field_eval.predicted_status = predicted.status

    # -- value match (AC-E-20/21) -------------------------------------------
    value_match = _compute_value_match(
        field_types.get(field_id), gold_field.acceptable_values, predicted.value
    )
    field_eval.value_match = value_match
    if value_match is False:
        # AC-T001-F18: exact-string mismatch is a diagnostic (warning), not a
        # freeze-blocking error; the automatic value_match is still recorded.
        field_eval.issues.append(
            _eissue(
                "value_mismatch",
                "warning",
                f"field {field_id!r}: predicted value "
                f"{predicted.value!r} does not match gold acceptable_values "
                f"{gold_field.acceptable_values!r} (diagnostic only; human "
                f"value_judgement stays authoritative)",
                fields=[field_id],
            )
        )

    # -- effective judgement (AC-E-22) --------------------------------------
    effective = _effective_judgement(gold_field.value_judgement)
    field_eval.effective_judgement = effective
    if _judgement_conflict(
        field_types.get(field_id), value_match, gold_field.value_judgement
    ):
        field_eval.issues.append(
            _eissue(
                "judgement_conflict",
                "warning",
                f"field {field_id!r}: human value_judgement "
                f"{gold_field.value_judgement!r} conflicts with automatic "
                f"value_match {value_match!r}; statistics follow the human "
                f"judgement",
                fields=[field_id],
            )
        )

    # -- status / absent-class checks (AC-E-23/30/31 + AC-T001-S3/S4) -------
    field_eval.status_match = predicted.status == expected_status
    gold_review_rationale = _is_gold_status_review_candidate(gold_field)
    if gold_review_rationale is not None:
        # AC-T001-S4/S6: gold expected_status contradicts the gold's own
        # content; surfaced for user confirmation, never modifies gold.
        field_eval.gold_review = True
        field_eval.status_explanation = gold_review_rationale
        review_issue = _eissue(
            "gold_status_review",
            "warning",
            f"field {field_id!r}: gold expected_status {expected_status!r} "
            f"contradicts the gold content per the frozen status 口径; "
            f"{gold_review_rationale}",
            fields=[field_id],
            source="gold_validation",
        )
        review_issue.explanation = gold_review_rationale
        field_eval.issues.append(review_issue)
    if field_eval.status_match:
        if expected_status in ABSENT_STATUSES:
            if _is_empty_value(predicted.value):
                field_eval.absent_correct = True
            else:
                field_eval.absent_correct = False
                field_eval.issues.append(
                    _eissue(
                        "hallucinated_value_for_absent_field",
                        "error",
                        f"field {field_id!r}: predicted status "
                        f"{expected_status!r} but a non-empty value was "
                        f"hallucinated: {predicted.value!r}",
                        fields=[field_id],
                    )
                )
    else:
        if expected_status in ABSENT_STATUSES:
            field_eval.absent_correct = False
        issue_type, severity, explanation = _classify_status_mismatch(
            expected_status or "",
            predicted.status,
            predicted.value,
            gold_field,
        )
        field_eval.status_explanation = field_eval.status_explanation or explanation
        message = (
            f"field {field_id!r}: expected status {expected_status!r} "
            f"but predicted status {predicted.status!r}"
        )
        if explanation:
            message += f"; {explanation}"
        issue = _eissue(issue_type, severity, message, fields=[field_id])
        issue.explanation = explanation
        field_eval.issues.append(issue)

    # -- evidence structure (AC-E-25/29) ------------------------------------
    field_eval.evidence_present = bool(predicted.evidence)
    if gold_field.evidence_expectation == "required" and not field_eval.evidence_present:
        field_eval.issues.append(
            _eissue(
                "evidence_missing",
                "error",
                f"field {field_id!r}: gold requires evidence but the "
                f"prediction carries none",
                fields=[field_id],
            )
        )
    if (
        gold_field.value_judgement in ("correct", "partially_correct")
        and gold_field.evidence_expectation == "required"
    ):
        field_eval.evidence_support = field_eval.evidence_present
    else:
        field_eval.evidence_support = None

    # -- weak traceability (AC-E-26) ----------------------------------------
    for index, ref in enumerate(predicted.evidence):
        problems = _weak_ref_problems(ref)
        if problems:
            field_eval.weak_fail_refs += 1
            field_eval.issues.append(
                _eissue(
                    "evidence_ref_invalid",
                    "error",
                    f"field {field_id!r}: {_ref_identity(ref, index)} failed "
                    f"weak traceability checks: {'; '.join(problems)}",
                    fields=[field_id],
                )
            )
        else:
            field_eval.weak_ok_refs += 1

    # -- strict per-ref classification (AC-E-27/28) -------------------------
    if strict_mode == "strict":
        strict_ok = 0
        strict_fail = 0
        for ref in predicted.evidence:
            if _ref_strict_failed(field_id, ref, integrity_issues):
                strict_fail += 1
            else:
                strict_ok += 1
        field_eval.strict_ok_refs = strict_ok
        field_eval.strict_fail_refs = strict_fail
        for issue in integrity_issues:
            if field_id in issue.fields and any(
                ref.block_id in issue.message for ref in predicted.evidence
            ):
                field_eval.issues.append(
                    AcceptanceIssue.from_validation_issue(issue, "evidence_integrity")
                )

    return field_eval


def _ref_strict_failed(
    field_id: str,
    ref: EvidenceRef,
    integrity_issues: list[ValidationIssue],
) -> bool:
    for issue in integrity_issues:
        if issue.severity != "error":
            continue
        if field_id not in issue.fields:
            continue
        if ref.block_id in issue.message:
            return True
    return False


# ---------------------------------------------------------------------------
# benchmark-level evaluation (storage-backed)
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def evaluate_schema_gold(
    gold: GoldBenchmark,
    *,
    storage_root: Path | str | None = None,
    schema_id: str = "bus_control_rl",
    run_id: str | None = None,
    canonical_reader: Any = None,
):
    """Evaluate a whole gold benchmark against Package D persisted runs
    (AC-E-05, FR-006).

    For each non-template, non-draft paper entry the run is read through the
    Package D public API ``get_schema(paper_id, schema_id, run_id,
    storage_root=...)`` (no new storage logic is written here); the persisted
    validation report of that run is passed through as
    ``validation_report``. Template and draft entries are skipped with a
    warning and excluded from every quality denominator. With ``run_id``
    omitted the current run is used (``run_kind="current"``); with an
    explicit ``run_id`` a historical run is read (``run_kind="historical"``),
    and both kinds are recorded explicitly in the results.

    Gold validation issues are merged into the report (source
    ``gold_validation``). A storage read failure becomes a ``paper_error``
    of type ``schema_read_failed`` and never crashes the whole evaluation.

    Returns an ``AcceptanceReport`` (see ``report.py``).
    """
    from .report import build_acceptance_report

    gold_issues = validate_schema_gold(gold)

    papers: list[PaperEvaluation] = []
    for entry in gold.papers:
        if entry.gold_status == GOLD_STATUS_TEMPLATE:
            papers.append(_template_paper_evaluation(entry))
            continue
        if entry.gold_status == GOLD_STATUS_DRAFT:
            papers.append(_draft_paper_evaluation(entry))
            continue
        if not entry.paper_id or not entry.schema_id:
            paper = PaperEvaluation(
                paper_id=entry.paper_id or "",
                title=entry.title,
                schema_id=entry.schema_id,
                schema_version=entry.schema_version,
                selected_for_v1=(
                    entry.selected_for_v1 if isinstance(entry.selected_for_v1, bool) else None
                ),
                paper_error="gold_invalid",
                issues=[
                    _eissue(
                        "gold_invalid",
                        "error",
                        f"paper entry {entry.paper_id!r} lacks paper_id or "
                        f"schema_id and cannot be evaluated",
                    )
                ],
            )
            papers.append(paper)
            continue
        target_schema_id = entry.schema_id or schema_id
        try:
            instance = get_schema(
                entry.paper_id,
                target_schema_id,
                run_id=run_id,
                storage_root=storage_root,
            )
            storage = SchemaRunStorage(
                storage_root=Path(storage_root) if storage_root is not None else None
            )
            if run_id is None:
                pointer = storage.read_current(entry.paper_id)
                actual_run_id = pointer.run_id
                kind: RunKind = "current"
            else:
                actual_run_id = run_id
                kind = "historical"
            stored_report: ValidationReport | None = None
            try:
                stored_report = storage.read_run(entry.paper_id, actual_run_id).report
            except SchemaStorageError:
                stored_report = None
            paper = evaluate_schema_instance(
                instance,
                entry,
                validation_report=stored_report,
                canonical_reader=canonical_reader,
            )
            paper.run_kind = kind
            paper.run_id = actual_run_id
            papers.append(paper)
        except (SchemaStorageError, SchemaIdMismatchError) as exc:
            paper = PaperEvaluation(
                paper_id=entry.paper_id,
                title=entry.title,
                pdf_relative_path=entry.pdf_relative_path,
                schema_id=entry.schema_id,
                schema_version=entry.schema_version,
                selected_for_v1=(
                    entry.selected_for_v1 if isinstance(entry.selected_for_v1, bool) else None
                ),
                run_kind="historical" if run_id else "current",
                run_id=run_id,
                paper_error="schema_read_failed",
                issues=[
                    _eissue(
                        "schema_read_failed",
                        "error",
                        f"paper {entry.paper_id!r}: could not read schema run "
                        f"(schema_id={target_schema_id!r}, "
                        f"run_id={run_id!r}): {type(exc).__name__}: {exc}",
                    )
                ],
            )
            papers.append(paper)

    return build_acceptance_report(
        gold,
        papers,
        gold_issues,
        canonical_reader=canonical_reader,
        gold_path=None,
        generated_at=_utc_now_iso(),
    )
