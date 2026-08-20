"""L2S2 Package E gold data model, loading and validation (AC-E-09..16).

The gold file is a single JSON document whose top level is an object with a
non-empty ``papers`` list (plus optional benchmark metadata). Every paper
entry and field item is parsed leniently into Pydantic models so that
``validate_schema_gold`` can report precise issues instead of hard-crashing
on missing keys; only top-level structural failures (file missing, invalid
JSON, missing/empty ``papers``) raise ``GoldLoadError``.

Safety contract (AC-E-10/16/54): ``pdf_relative_path`` is metadata only.
Loading, validation, evaluation and reporting never open, stat or existence
-check the PDF path. Validation only reads the gold content and the local
schema plugin definition (``get_schema_definition``); it never touches
network, storage, or ``data/**``.

This module imports only stdlib, pydantic and the stable public exports of
``transit_scholar.layer2.schema_extraction``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from transit_scholar.layer2.schema_extraction import (
    FIELD_STATUSES,
    InvalidSchemaDefinitionError,
    SchemaPluginNotFoundError,
    ValidationIssue,
    get_schema_definition,
)

# ---------------------------------------------------------------------------
# vocabularies and markers
# ---------------------------------------------------------------------------

#: Gold ``value_judgement`` vocabulary (requirements 4.3 / AC-E-11).
VALUE_JUDGEMENTS: frozenset[str] = frozenset(
    {"correct", "partially_correct", "incorrect", "not_evaluated"}
)

#: Gold ``evidence_expectation`` vocabulary (requirements 4.3 / AC-E-11).
EVIDENCE_EXPECTATIONS: frozenset[str] = frozenset(
    {"required", "optional", "not_required"}
)

#: Fixed template markers that must appear in template field notes
#: (AC-E-15/48; kept as constants so the Evaluator can grep them).
TEMPLATE_MARKER = "TEMPLATE"
HUMAN_REVIEW_MARKER = "human_review_required"

#: Entry-level gold statuses (``gold_status``; AC-E-15, FR-005).
GOLD_STATUS_EVALUATED = "evaluated"
GOLD_STATUS_TEMPLATE = "template"
GOLD_STATUS_DRAFT = "draft"

#: Per-field draft markers (FR-005). A draft field carries all five and must
#: never carry a human conclusion.
DRAFT_VALUE_JUDGEMENT = "not_evaluated"
DRAFT_EVIDENCE_JUDGEMENT = "not_evaluated"
DRAFT_REVIEW_STATUS = "requires_human_review"
DRAFT_GOLD_SOURCE = "llm_assisted_draft"


class GoldLoadError(Exception):
    """Gold file could not be loaded (stable error code ``gold_load_failed``).

    Raised for: missing file, unreadable file, invalid JSON, non-object top
    level, missing ``papers`` key, empty ``papers`` list, or structurally
    malformed entries. Never raised for per-entry/per-field semantic issues,
    which belong to ``validate_schema_gold``.
    """

    def __init__(self, message: str = "", *, error_code: str = "gold_load_failed"):
        super().__init__(message or error_code)
        self.error_code = error_code


# ---------------------------------------------------------------------------
# models (lenient on purpose: validation reports issues, not exceptions)
# ---------------------------------------------------------------------------


class GoldField(BaseModel):
    """One gold field item (requirements 4.3 / AC-E-11, FR-005).

    Parsed leniently: missing/invalid values stay ``None`` so that
    ``validate_schema_gold`` can emit precise per-field issues. Draft
    markers (``evidence_judgement`` / ``review_status`` / ``gold_source`` /
    ``not_human_gold``) are explicit fields so the draft-vs-human-gold
    distinction is machine-testable.
    """

    model_config = ConfigDict(extra="allow")

    field_id: str | None = None
    expected_status: str | None = None
    acceptable_values: Any = None
    value_judgement: str | None = None
    evidence_judgement: str | None = None
    review_status: str | None = None
    gold_source: str | None = None
    not_human_gold: bool | None = None
    evidence_expectation: str | None = None
    notes: str = ""


class GoldPaper(BaseModel):
    """One paper gold entry (requirements 4.2 / AC-E-09/10/15)."""

    model_config = ConfigDict(extra="allow")

    paper_id: str | None = None
    title: str | None = None
    pdf_relative_path: str | None = None
    schema_id: str | None = None
    schema_version: str | None = None
    selected_for_v1: Any = None
    notes: str = ""
    fields: list[GoldField] = Field(default_factory=list)
    gold_status: str = GOLD_STATUS_EVALUATED


class GoldBenchmark(BaseModel):
    """Top-level gold document (AC-E-09)."""

    model_config = ConfigDict(extra="allow")

    benchmark_id: str | None = None
    description: str | None = None
    gold_version: str | None = None
    papers: list[GoldPaper]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_schema_gold(path: str | Path) -> GoldBenchmark:
    """Load and parse a gold JSON file (AC-E-12).

    Failures are explicit ``GoldLoadError``s with the stable error code
    ``gold_load_failed``; a valid file never silently yields an empty gold.
    """
    gold_path = Path(path)
    try:
        raw = gold_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GoldLoadError(
            f"gold file {str(gold_path)!r} could not be read: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GoldLoadError(
            f"gold file {str(gold_path)!r} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise GoldLoadError(
            f"gold file {str(gold_path)!r}: top level must be a JSON object"
        )
    papers = data.get("papers")
    if not isinstance(papers, list) or not papers:
        raise GoldLoadError(
            f"gold file {str(gold_path)!r}: top level must contain a "
            f"non-empty 'papers' list"
        )
    try:
        return GoldBenchmark.model_validate(data)
    except ValidationError as exc:
        raise GoldLoadError(
            f"gold file {str(gold_path)!r} is structurally malformed: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _issue(
    type_: str,
    severity: str,
    message: str,
    *,
    fields: list[str] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        type=type_,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        fields=fields or [],
    )


def _is_relative_pdf_path(value: str | None) -> bool:
    """True when ``value`` looks like a safe repo-relative path (AC-E-10)."""
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or value.startswith("\\"):
        return False
    p = Path(value)
    if p.is_absolute():
        return False
    if ".." in p.parts:
        return False
    if len(value) >= 2 and value[1] == ":":
        return False
    return True


def _definition_for(schema_id: str | None):
    """Load the schema definition, translating loader errors to ``None``."""
    if not isinstance(schema_id, str) or not schema_id:
        return None
    try:
        return get_schema_definition(schema_id)
    except (SchemaPluginNotFoundError, InvalidSchemaDefinitionError):
        return None


def _definition_field_ids(definition) -> set[str]:
    if definition is None:
        return set()
    return {field.id for section in definition.sections for field in section.fields}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate_schema_gold(gold: GoldBenchmark) -> list[ValidationIssue]:
    """Validate a loaded gold benchmark (AC-E-13..16, FR-005/FR-006).

    Returns a list of ``ValidationIssue`` objects (empty list = valid). The
    checks cover the full AC-E-14 matrix plus the AC-E-15 template-entry
    rules and the FR-005 draft-entry rules (five mandatory draft markers,
    no human judgement, no two-way mislabelling between draft and
    evaluated/template entries). No network, no PDF IO, no storage access:
    only gold content and local schema plugin definitions are consulted.
    """
    issues: list[ValidationIssue] = []

    seen_paper_ids: dict[str, int] = {}
    for paper_index, paper in enumerate(gold.papers):
        prefix = f"paper #{paper_index}"
        is_template = paper.gold_status == GOLD_STATUS_TEMPLATE
        is_draft = paper.gold_status == GOLD_STATUS_DRAFT

        # -- paper entry required keys (AC-E-14.1) ---------------------------
        for key in ("paper_id", "title", "pdf_relative_path", "schema_id", "schema_version"):
            value = getattr(paper, key)
            if not isinstance(value, str) or not value:
                issues.append(
                    _issue(
                        "paper_missing_required_key",
                        "error",
                        f"{prefix}: missing or invalid required key {key!r}",
                    )
                )
        if not isinstance(paper.selected_for_v1, bool):
            issues.append(
                _issue(
                    "paper_missing_required_key",
                    "error",
                    f"{prefix}: missing or invalid required key "
                    f"'selected_for_v1' (expected bool)",
                )
            )
        if not paper.fields:
            issues.append(
                _issue(
                    "paper_missing_required_key",
                    "error",
                    f"{prefix}: 'fields' must be a non-empty list",
                )
            )

        # -- duplicate paper ids (deterministic report keying guard) --------
        if isinstance(paper.paper_id, str) and paper.paper_id:
            if paper.paper_id in seen_paper_ids:
                issues.append(
                    _issue(
                        "paper_id_duplicate",
                        "error",
                        f"{prefix}: duplicate paper_id {paper.paper_id!r} "
                        f"(first seen at paper #{seen_paper_ids[paper.paper_id]})",
                        fields=[paper.paper_id],
                    )
                )
            else:
                seen_paper_ids[paper.paper_id] = paper_index

        # -- pdf_relative_path safety (AC-E-10/14.6) -------------------------
        if isinstance(paper.pdf_relative_path, str) and paper.pdf_relative_path:
            if not _is_relative_pdf_path(paper.pdf_relative_path):
                issues.append(
                    _issue(
                        "pdf_path_invalid",
                        "error",
                        f"{prefix}: pdf_relative_path "
                        f"{paper.pdf_relative_path!r} must be a repo-relative "
                        f"path (no absolute path, no drive letter, no '..')",
                    )
                )

        # -- schema id resolvability (AC-E-14.7) -----------------------------
        definition = _definition_for(paper.schema_id)
        definition_field_ids = _definition_field_ids(definition)
        if isinstance(paper.schema_id, str) and paper.schema_id and definition is None:
            issues.append(
                _issue(
                    "gold_schema_not_found",
                    "error",
                    f"{prefix}: schema {paper.schema_id!r} is not available "
                    f"as a schema plugin",
                )
            )

        # -- field items -----------------------------------------------------
        seen_field_ids: set[str] = set()
        for field_index, field in enumerate(paper.fields):
            fprefix = f"{prefix} field #{field_index}"

            for key in ("field_id", "value_judgement", "evidence_expectation"):
                value = getattr(field, key)
                if not isinstance(value, str) or not value:
                    issues.append(
                        _issue(
                            "field_missing_required_key",
                            "error",
                            f"{fprefix}: missing or invalid required key {key!r}",
                        )
                    )

            # expected_status: only template / draft entries may carry null
            # (AC-E-15, FR-005: drafts may leave fields unevaluated)
            if field.expected_status is None:
                if not (is_template or is_draft):
                    issues.append(
                        _issue(
                            "field_expected_status_null",
                            "error",
                            f"{fprefix}: expected_status is null but the paper "
                            f"entry is not marked as a template or draft",
                        )
                    )
            elif field.expected_status not in FIELD_STATUSES:
                issues.append(
                    _issue(
                        "field_invalid_expected_status",
                        "error",
                        f"{fprefix}: invalid expected_status "
                        f"{field.expected_status!r}; must be one of "
                        f"{sorted(FIELD_STATUSES)}",
                    )
                )

            if field.value_judgement is not None and field.value_judgement not in VALUE_JUDGEMENTS:
                issues.append(
                    _issue(
                        "field_invalid_value_judgement",
                        "error",
                        f"{fprefix}: invalid value_judgement "
                        f"{field.value_judgement!r}; must be one of "
                        f"{sorted(VALUE_JUDGEMENTS)}",
                    )
                )
            if (
                field.evidence_expectation is not None
                and field.evidence_expectation not in EVIDENCE_EXPECTATIONS
            ):
                issues.append(
                    _issue(
                        "field_invalid_evidence_expectation",
                        "error",
                        f"{fprefix}: invalid evidence_expectation "
                        f"{field.evidence_expectation!r}; must be one of "
                        f"{sorted(EVIDENCE_EXPECTATIONS)}",
                    )
                )

            # template entries must stay fully un-judged (AC-E-15/48)
            if is_template:
                if field.value_judgement != "not_evaluated":
                    issues.append(
                        _issue(
                            "template_field_invalid",
                            "error",
                            f"{fprefix}: template field must have "
                            f"value_judgement 'not_evaluated' (got "
                            f"{field.value_judgement!r}); a template must "
                            f"never carry human conclusions",
                        )
                    )
                if field.expected_status is not None:
                    issues.append(
                        _issue(
                            "template_field_invalid",
                            "error",
                            f"{fprefix}: template field must have "
                            f"expected_status null (got "
                            f"{field.expected_status!r})",
                        )
                    )
                if field.acceptable_values is not None:
                    issues.append(
                        _issue(
                            "template_field_invalid",
                            "error",
                            f"{fprefix}: template field must have "
                            f"acceptable_values null (got "
                            f"{field.acceptable_values!r})",
                        )
                    )
                if (
                    TEMPLATE_MARKER not in field.notes
                    or HUMAN_REVIEW_MARKER not in field.notes
                ):
                    issues.append(
                        _issue(
                            "template_field_invalid",
                            "error",
                            f"{fprefix}: template field notes must contain "
                            f"the fixed markers {TEMPLATE_MARKER!r} and "
                            f"{HUMAN_REVIEW_MARKER!r}",
                        )
                    )

            # draft entries: five mandatory markers, never a human conclusion
            # (FR-005)
            if is_draft:
                if field.value_judgement != DRAFT_VALUE_JUDGEMENT:
                    issues.append(
                        _issue(
                            "draft_field_human_judgement",
                            "error",
                            f"{fprefix}: draft field must have "
                            f"value_judgement {DRAFT_VALUE_JUDGEMENT!r} "
                            f"(got {field.value_judgement!r}); a draft must "
                            f"never carry human conclusions",
                        )
                    )
                if field.evidence_judgement != DRAFT_EVIDENCE_JUDGEMENT:
                    issues.append(
                        _issue(
                            "draft_field_human_judgement",
                            "error",
                            f"{fprefix}: draft field must have "
                            f"evidence_judgement {DRAFT_EVIDENCE_JUDGEMENT!r} "
                            f"(got {field.evidence_judgement!r}); a draft must "
                            f"never carry human conclusions",
                        )
                    )
                if field.review_status != DRAFT_REVIEW_STATUS:
                    issues.append(
                        _issue(
                            "draft_field_marker_missing",
                            "error",
                            f"{fprefix}: draft field must carry "
                            f"review_status {DRAFT_REVIEW_STATUS!r} (got "
                            f"{field.review_status!r})",
                        )
                    )
                if field.gold_source != DRAFT_GOLD_SOURCE:
                    issues.append(
                        _issue(
                            "draft_field_marker_missing",
                            "error",
                            f"{fprefix}: draft field must carry "
                            f"gold_source {DRAFT_GOLD_SOURCE!r} (got "
                            f"{field.gold_source!r})",
                        )
                    )
                if field.not_human_gold is not True:
                    issues.append(
                        _issue(
                            "draft_field_marker_missing",
                            "error",
                            f"{fprefix}: draft field must carry "
                            f"not_human_gold true (got "
                            f"{field.not_human_gold!r})",
                        )
                    )
            elif (
                field.not_human_gold is True
                or field.gold_source == DRAFT_GOLD_SOURCE
            ):
                # two-way mislabel forbidden (FR-006): non-draft entries must
                # not carry draft markers
                entry_kind = "template" if is_template else "evaluated"
                issues.append(
                    _issue(
                        "draft_marker_on_non_draft",
                        "error",
                        f"{fprefix}: {entry_kind} field must not carry draft "
                        f"markers (not_human_gold: true / "
                        f"gold_source: {DRAFT_GOLD_SOURCE!r})",
                    )
                )

            # duplicate field_id within one paper entry (AC-E-14.8)
            if isinstance(field.field_id, str) and field.field_id:
                if field.field_id in seen_field_ids:
                    issues.append(
                        _issue(
                            "field_id_duplicate",
                            "error",
                            f"{fprefix}: duplicate field_id {field.field_id!r} "
                            f"within this paper entry",
                            fields=[field.field_id],
                        )
                    )
                seen_field_ids.add(field.field_id)

            # field_id must exist in the schema definition (AC-E-14.3)
            if (
                isinstance(field.field_id, str)
                and field.field_id
                and definition is not None
                and field.field_id not in definition_field_ids
            ):
                issues.append(
                    _issue(
                        "field_id_not_in_schema",
                        "error",
                        f"{fprefix}: field_id {field.field_id!r} does not "
                        f"exist in schema {paper.schema_id!r} definition",
                        fields=[field.field_id],
                    )
                )

    return issues
