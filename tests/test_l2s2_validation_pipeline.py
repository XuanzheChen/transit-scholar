"""L2S2 Package C deterministic tests: validation pipeline end-to-end
(FR-C-007 / AC-C-07) plus report status derivation (AC-C-01), error
semantics (AC-C-08), and import isolation.

All stages use injected fakes (fake reader, fake verifier, neutral fake
cross-field validator, fake recheck callable). Offline and deterministic.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from transit_scholar.layer2.schema_extraction import (
    EvidenceRef,
    FieldDefinition,
    FieldResult,
    FakeSemanticVerifier,
    RecheckTrace,
    SchemaDefinition,
    SchemaInstance,
    SectionDefinition,
    ValidationIssue,
    ValidationReport,
    compute_schema_hash,
    derive_report_status,
    validate_schema_instance_in_memory,
    run_validation_pipeline_in_memory,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

PAPER_ID = "paper_001"
BLOCK_TEXT = "The bus holding control problem is studied with RL methods."


def _field(field_id: str, ftype: str = "string", **kwargs) -> FieldDefinition:
    values = dict(
        label=f"Label {field_id}",
        question=f"What is {field_id}?",
        type=ftype,
    )
    values.update(kwargs)
    return FieldDefinition(id=field_id, **values)


def _definition() -> SchemaDefinition:
    return SchemaDefinition(
        schema_id="test_schema",
        version="1.0",
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("f1"), _field("f2", "number")],
            ),
        ],
    )


def _block(block_id: str = "blk_1") -> dict:
    return {
        "block_id": block_id,
        "paper_id": PAPER_ID,
        "block_type": "body",
        "section_id": "Method",
        "order": 0,
        "text": BLOCK_TEXT,
        "pages": [2],
        "provenance": [],
        "source_items": [],
        "relations": {},
        "content": {},
    }


def _reader(block_map: dict):
    def reader(paper_id, block_ids):
        return {bid: block_map[bid] for bid in block_ids if bid in block_map}

    return reader


def _instance(
    f1: FieldResult | None = None,
    f2: FieldResult | None = None,
) -> SchemaInstance:
    return SchemaInstance(
        paper_id=PAPER_ID,
        schema_id="test_schema",
        schema_version="1.0",
        fields={
            "f1": f1 if f1 is not None else FieldResult(value="v", status="explicit"),
            "f2": f2 if f2 is not None else FieldResult(value=3, status="explicit"),
        },
    )


def _fake_cross_field(issue: ValidationIssue | None):
    def validator(instance):
        return [issue] if issue is not None else []

    return validator


class CountingRecheck:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls: list[str] = []

    def __call__(self, definition, field, paper_id):
        self.calls.append(field.id)
        return self.results[field.id]


# ---------------------------------------------------------------------------
# AC-C-01 report status derivation
# ---------------------------------------------------------------------------


def _issue(severity: str) -> ValidationIssue:
    return ValidationIssue(type="t", severity=severity, message="m")


def test_derive_status_error_wins():
    assert (
        derive_report_status(
            [_issue("warning"), _issue("error")], RecheckTrace()
        )
        == "failed"
    )


def test_derive_status_recheck_wins_over_warning():
    trace = RecheckTrace(
        entries=[
            {
                "field_id": "f1",
                "reason": "r",
                "original_status": "explicit",
                "new_status": "explicit",
                "updated": True,
            }
        ]
    )
    assert derive_report_status([_issue("warning")], trace) == "needs_recheck"


def test_derive_status_warning():
    assert (
        derive_report_status([_issue("warning")], RecheckTrace()) == "warning"
    )


def test_derive_status_passed():
    assert derive_report_status([], RecheckTrace()) == "passed"


# ---------------------------------------------------------------------------
# AC-C-07 full pipeline
# ---------------------------------------------------------------------------


def test_pipeline_full_chain_produces_report():
    definition = _definition()
    instance = _instance(
        f1=FieldResult(
            value="v",
            status="explicit",
            evidence=[
                EvidenceRef(
                    block_id="blk_1",
                    char_start=4,
                    char_end=23,
                    pages=[2],
                    section_path=["Method"],
                    quote="bus holding control",
                )
            ],
        ),
        f2=FieldResult(value=3, status="explicit"),
    )
    verifier = FakeSemanticVerifier(
        default_response={"decision": "supported", "confidence": None}
    )
    cross_field_issue = ValidationIssue(
        type="cross_field_consistency",
        severity="warning",
        message="neutral cross-field warning",
        fields=["f2"],
        action="recheck",
    )
    recheck = CountingRecheck(
        results={"f2": FieldResult(value=4, status="explicit")}
    )

    report = validate_schema_instance_in_memory(
        definition,
        instance,
        canonical_reader=_reader({"blk_1": _block()}),
        verifier=verifier,
        cross_field_validators=[_fake_cross_field(cross_field_issue)],
        recheck_callable=recheck,
        enable_recheck=True,
    )

    assert isinstance(report, ValidationReport)
    assert report.paper_id == PAPER_ID
    assert report.schema_id == "test_schema"
    assert report.schema_version == "1.0"
    assert report.schema_hash == compute_schema_hash(definition)
    assert report.structural_issues == []
    assert report.evidence_issues == []
    assert report.semantic_issues == []
    assert [i.model_dump() for i in report.cross_field_issues] == [
        cross_field_issue.model_dump()
    ]
    assert report.issues == report.cross_field_issues
    assert len(report.recheck_trace.entries) == 1
    assert report.recheck_trace.entries[0].field_id == "f2"
    assert report.recheck_trace.entries[0].updated is True
    assert report.status == "needs_recheck"
    assert instance.fields["f2"].value == 4
    assert recheck.calls == ["f2"]
    assert report.created_at


def test_pipeline_report_is_json_serializable():
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(),
        canonical_reader=_reader({}),
    )
    json.dumps(report.model_dump())
    rebuilt = ValidationReport.model_validate_json(report.model_dump_json())
    assert rebuilt.model_dump() == report.model_dump()


def test_pipeline_alias_works():
    assert run_validation_pipeline_in_memory is validate_schema_instance_in_memory


# ---------------------------------------------------------------------------
# error semantics: system failures are explicit
# ---------------------------------------------------------------------------


def test_pipeline_without_reader_reports_canonical_read_failed():
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(
            f1=FieldResult(
                value="v",
                status="explicit",
                evidence=[EvidenceRef(block_id="blk_1", char_start=0, char_end=4)],
            )
        ),
        canonical_reader=None,
    )
    assert [i.type for i in report.evidence_issues] == ["canonical_read_failed"]
    assert report.status == "failed"


def test_pipeline_default_verifier_is_offline_fake():
    report = validate_schema_instance_in_memory(
        _definition(), _instance(), canonical_reader=_reader({})
    )
    assert report.semantic_issues == []


def test_pipeline_verifier_unavailable_becomes_error_status():
    verifier = FakeSemanticVerifier(
        responses={},
        default_response={"decision": "supported", "confidence": None},
        unavailable_keys=["f1"],
    )
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(),
        canonical_reader=_reader({}),
        verifier=verifier,
    )
    assert [i.type for i in report.semantic_issues] == ["verifier_unavailable"]
    assert report.status == "failed"


def test_pipeline_error_issue_yields_failed_status():
    error_issue = ValidationIssue(
        type="cross_field_consistency",
        severity="error",
        message="neutral cross-field error",
        fields=["f1"],
    )
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(),
        canonical_reader=_reader({}),
        cross_field_validators=[_fake_cross_field(error_issue)],
    )
    assert report.status == "failed"


def test_pipeline_only_warning_yields_warning_status():
    warning_issue = ValidationIssue(
        type="cross_field_consistency",
        severity="warning",
        message="neutral warning",
        fields=["f1"],
    )
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(),
        canonical_reader=_reader({}),
        cross_field_validators=[_fake_cross_field(warning_issue)],
    )
    assert report.status == "warning"


def test_pipeline_clean_instance_yields_passed():
    report = validate_schema_instance_in_memory(
        _definition(), _instance(), canonical_reader=_reader({})
    )
    assert report.status == "passed"
    assert report.issues == []


# ---------------------------------------------------------------------------
# recheck switch semantics
# ---------------------------------------------------------------------------


def test_disable_recheck_skips_callable():
    recheck = CountingRecheck(
        results={"f2": FieldResult(value=4, status="explicit")}
    )
    cross_field_issue = ValidationIssue(
        type="cross_field_consistency",
        severity="warning",
        message="neutral warning",
        fields=["f2"],
        action="recheck",
    )
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(),
        canonical_reader=_reader({}),
        cross_field_validators=[_fake_cross_field(cross_field_issue)],
        recheck_callable=recheck,
        enable_recheck=False,
    )
    assert recheck.calls == []
    assert report.recheck_trace.entries == []
    assert report.status == "warning"


def test_no_recheck_callable_yields_empty_trace():
    cross_field_issue = ValidationIssue(
        type="cross_field_consistency",
        severity="warning",
        message="neutral warning",
        fields=["f2"],
        action="recheck",
    )
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(),
        canonical_reader=_reader({}),
        cross_field_validators=[_fake_cross_field(cross_field_issue)],
        recheck_callable=None,
        enable_recheck=True,
    )
    assert report.recheck_trace.entries == []
    assert report.status == "warning"


def test_recheck_targets_come_from_action_recheck_issues():
    recheck = CountingRecheck(
        results={"f1": FieldResult(value="new", status="explicit")}
    )
    semantic = FakeSemanticVerifier(
        responses={"f1": {"decision": "unsupported", "confidence": 0.1}},
        default_response={"decision": "supported", "confidence": None},
    )
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(),
        canonical_reader=_reader({}),
        verifier=semantic,
        recheck_callable=recheck,
        enable_recheck=True,
    )
    assert recheck.calls == ["f1"]
    assert report.recheck_trace.entries[0].field_id == "f1"
    assert report.recheck_trace.entries[0].reason
    assert report.status == "needs_recheck"


def test_recheck_failure_keeps_warning_status_with_trace():
    def failing_recheck(definition, field, paper_id):
        raise RuntimeError("down")

    semantic = FakeSemanticVerifier(
        responses={"f1": {"decision": "unsupported", "confidence": 0.1}},
        default_response={"decision": "supported", "confidence": None},
    )
    report = validate_schema_instance_in_memory(
        _definition(),
        _instance(),
        canonical_reader=_reader({}),
        verifier=semantic,
        recheck_callable=failing_recheck,
        enable_recheck=True,
    )
    entry = report.recheck_trace.entries[0]
    assert entry.updated is False
    assert entry.error_code == "recheck_failed"
    assert report.status == "needs_recheck"


# ---------------------------------------------------------------------------
# cross-field validators are injected and read-only
# ---------------------------------------------------------------------------


def test_cross_field_validators_receive_instance_read_only():
    calls = []

    def spy_validator(instance):
        calls.append(instance.model_dump())
        return []

    definition = _definition()
    instance = _instance()
    before = instance.model_dump()
    report = validate_schema_instance_in_memory(
        definition,
        instance,
        canonical_reader=_reader({}),
        cross_field_validators=[spy_validator],
    )
    assert instance.model_dump() == before
    assert len(calls) == 1
    assert report.cross_field_issues == []


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_pipeline_is_deterministic_excluding_created_at():
    def run():
        report = validate_schema_instance_in_memory(
            _definition(),
            _instance(),
            canonical_reader=_reader({}),
        )
        data = report.model_dump()
        data.pop("created_at")
        return data

    assert run() == run()


# ---------------------------------------------------------------------------
# import isolation in a fresh subprocess
# ---------------------------------------------------------------------------


def test_import_isolation_in_fresh_subprocess():
    """Importing schema_extraction must not pull in schema_plugins, the L2S1
    stack, db, or config (fresh interpreter, mirrors acceptance command)."""
    code = (
        "import sys; "
        "from transit_scholar.layer2 import schema_extraction; "
        "from transit_scholar.layer2.schema_extraction import "
        "ValidationReport, RecheckTrace, validate_evidence_integrity, "
        "verify_field_semantics, run_targeted_recheck, "
        "validate_schema_instance_in_memory; "
        "forbidden = ['transit_scholar.config', 'transit_scholar.db', "
        "'transit_scholar.layer2.schema', 'transit_scholar.layer2.retrieval', "
        "'transit_scholar.layer2.parser', 'transit_scholar.layer2.chunker', "
        "'transit_scholar.layer2.pipeline', "
        "'transit_scholar.layer2.normalizer', "
        "'transit_scholar.layer2.schema_plugins']; "
        "bad = [n for n in forbidden if n in sys.modules]; "
        "assert not bad, bad; print('isolated-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "isolated-ok" in result.stdout
