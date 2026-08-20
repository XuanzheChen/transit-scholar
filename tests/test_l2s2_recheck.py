"""L2S2 Package C deterministic tests: targeted recheck (FR-C-006 /
AC-C-06).

Covers: at-most-once per field, only targeted fields updated, success
replacement, explicit failure traces (never not_found), unclear/conflicting
retention, missing fields, and JSON round-trip. All callables are injected
fakes; no network or disk.
"""

from __future__ import annotations

import copy

import pytest

from transit_scholar.layer2.schema_extraction import (
    FieldDefinition,
    FieldResult,
    RecheckTrace,
    SchemaDefinition,
    SchemaInstance,
    SectionDefinition,
    ValidationIssue,
    run_targeted_recheck,
)


def _field(field_id: str, ftype: str = "string") -> FieldDefinition:
    return FieldDefinition(
        id=field_id,
        label=f"Label {field_id}",
        question=f"What is {field_id}?",
        type=ftype,
    )


def _definition() -> SchemaDefinition:
    return SchemaDefinition(
        schema_id="test_schema",
        version="1.0",
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("f1"), _field("f2", "number"), _field("f3")],
            ),
        ],
    )


def _instance(definition: SchemaDefinition | None = None) -> SchemaInstance:
    definition = definition or _definition()
    return SchemaInstance(
        paper_id="p1",
        schema_id=definition.schema_id,
        schema_version=definition.version,
        fields={
            "f1": FieldResult(value="old1", status="explicit"),
            "f2": FieldResult(value=2, status="explicit"),
            "f3": FieldResult(value="old3", status="explicit"),
        },
    )


class CountingRecheck:
    def __init__(self, results=None, *, raise_for=None, garbage_for=None):
        self.results = results or {}
        self.raise_for = raise_for or {}
        self.garbage_for = garbage_for or {}
        self.calls: list[str] = []

    def __call__(self, definition, field, paper_id):
        self.calls.append(field.id)
        if field.id in self.raise_for:
            raise self.raise_for[field.id]
        if field.id in self.garbage_for:
            return self.garbage_for[field.id]
        return self.results.get(field.id)


def _issue(field_ids: list[str]) -> ValidationIssue:
    return ValidationIssue(
        type="semantic_unsupported",
        severity="warning",
        message=f"recheck requested for {field_ids}",
        fields=list(field_ids),
        action="recheck",
    )


# ---------------------------------------------------------------------------
# at-most-once and ordering
# ---------------------------------------------------------------------------


def test_each_field_rechecked_at_most_once():
    recheck = CountingRecheck(
        results={"f1": FieldResult(value="new1", status="explicit")}
    )
    issues = [_issue(["f1"]), _issue(["f1"]), _issue(["f1", "f1"])]
    run_targeted_recheck(_definition(), _instance(), ["f1"], recheck, issues=issues)
    assert recheck.calls == ["f1"]


def test_call_order_follows_target_order():
    recheck = CountingRecheck(
        results={
            "f2": FieldResult(value=3, status="explicit"),
            "f1": FieldResult(value="new1", status="explicit"),
        }
    )
    run_targeted_recheck(_definition(), _instance(), ["f2", "f1"], recheck)
    assert recheck.calls == ["f2", "f1"]


def test_duplicate_targets_are_deduped():
    recheck = CountingRecheck(
        results={"f1": FieldResult(value="new1", status="explicit")}
    )
    run_targeted_recheck(_definition(), _instance(), ["f1", "f1", "f1"], recheck)
    assert recheck.calls == ["f1"]


# ---------------------------------------------------------------------------
# only targeted fields are updated
# ---------------------------------------------------------------------------


def test_only_targeted_fields_are_updated():
    recheck = CountingRecheck(
        results={"f1": FieldResult(value="new1", status="inferred")}
    )
    definition = _definition()
    instance = _instance(definition)
    before = copy.deepcopy(instance)
    trace = run_targeted_recheck(definition, instance, ["f1"], recheck)
    assert instance.fields["f1"].value == "new1"
    assert instance.fields["f1"].status == "inferred"
    assert instance.fields["f2"].model_dump() == before.fields["f2"].model_dump()
    assert instance.fields["f3"].model_dump() == before.fields["f3"].model_dump()
    assert len(trace.entries) == 1


# ---------------------------------------------------------------------------
# success replacement
# ---------------------------------------------------------------------------


def test_success_replaces_result_and_traces_it():
    recheck = CountingRecheck(
        results={"f1": FieldResult(value="new1", status="explicit", confidence=0.9)}
    )
    definition = _definition()
    instance = _instance(definition)
    trace = run_targeted_recheck(
        definition, instance, ["f1"], recheck, issues=[_issue(["f1"])]
    )
    entry = trace.entries[0]
    assert entry.field_id == "f1"
    assert entry.original_status == "explicit"
    assert entry.new_status == "explicit"
    assert entry.updated is True
    assert entry.error_code is None
    assert entry.error_message is None
    assert "recheck requested" in entry.reason
    assert instance.fields["f1"].value == "new1"


def test_dict_return_is_validated_and_replaced():
    def dict_recheck(definition, field, paper_id):
        return {"value": "from dict", "status": "explicit"}

    definition = _definition()
    instance = _instance(definition)
    trace = run_targeted_recheck(definition, instance, ["f1"], dict_recheck)
    assert trace.entries[0].updated is True
    assert instance.fields["f1"].value == "from dict"


# ---------------------------------------------------------------------------
# failures are explicit, never not_found
# ---------------------------------------------------------------------------


def test_callable_exception_keeps_original_and_traces():
    recheck = CountingRecheck(raise_for={"f1": RuntimeError("llm down")})
    definition = _definition()
    instance = _instance(definition)
    original = instance.fields["f1"].model_dump()
    trace = run_targeted_recheck(definition, instance, ["f1"], recheck)
    entry = trace.entries[0]
    assert entry.updated is False
    assert entry.error_code == "recheck_failed"
    assert "RuntimeError" in entry.error_message
    assert entry.original_status == "explicit"
    assert entry.new_status == "explicit"
    assert instance.fields["f1"].model_dump() == original


def test_invalid_return_traces_recheck_invalid_result():
    recheck = CountingRecheck(garbage_for={"f1": "not a result"})
    definition = _definition()
    instance = _instance(definition)
    original = instance.fields["f1"].model_dump()
    trace = run_targeted_recheck(definition, instance, ["f1"], recheck)
    entry = trace.entries[0]
    assert entry.updated is False
    assert entry.error_code == "recheck_invalid_result"
    assert instance.fields["f1"].model_dump() == original


def test_invalid_field_result_return_is_rejected():
    def bad_result_recheck(definition, field, paper_id):
        return {"value": "x", "status": "totally_found"}

    definition = _definition()
    instance = _instance(definition)
    trace = run_targeted_recheck(definition, instance, ["f1"], bad_result_recheck)
    entry = trace.entries[0]
    assert entry.updated is False
    assert entry.error_code == "recheck_invalid_result"


def test_failure_keeps_existing_unclear_status():
    definition = _definition()
    instance = _instance(definition)
    instance.fields["f1"] = FieldResult(value=None, status="unclear")
    recheck = CountingRecheck(raise_for={"f1": ValueError("boom")})
    trace = run_targeted_recheck(definition, instance, ["f1"], recheck)
    entry = trace.entries[0]
    assert entry.updated is False
    assert entry.error_code == "recheck_failed"
    assert instance.fields["f1"].status == "unclear"


# ---------------------------------------------------------------------------
# legal unclear / conflicting / not_found returns are normal conclusions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["unclear", "conflicting", "not_found"])
def test_legal_non_assertive_results_replace_field(status):
    recheck = CountingRecheck(
        results={"f1": FieldResult(value=None, status=status)}
    )
    definition = _definition()
    instance = _instance(definition)
    trace = run_targeted_recheck(definition, instance, ["f1"], recheck)
    entry = trace.entries[0]
    assert entry.updated is True
    assert entry.new_status == status
    assert instance.fields["f1"].status == status


# ---------------------------------------------------------------------------
# missing fields
# ---------------------------------------------------------------------------


def test_field_missing_from_instance_is_traced():
    recheck = CountingRecheck(
        results={"f2": FieldResult(value=3, status="explicit")}
    )
    definition = _definition()
    instance = _instance(definition)
    del instance.fields["f2"]
    trace = run_targeted_recheck(definition, instance, ["f2"], recheck)
    entry = trace.entries[0]
    assert entry.updated is False
    assert entry.error_code == "recheck_field_missing"
    assert entry.original_status == "absent"
    assert entry.new_status == "absent"
    assert recheck.calls == []


def test_field_unknown_to_definition_is_traced():
    recheck = CountingRecheck()
    definition = _definition()
    instance = _instance(definition)
    trace = run_targeted_recheck(definition, instance, ["ghost"], recheck)
    entry = trace.entries[0]
    assert entry.updated is False
    assert entry.error_code == "recheck_field_missing"
    assert recheck.calls == []


# ---------------------------------------------------------------------------
# reasons and JSON round-trip
# ---------------------------------------------------------------------------


def test_reason_merges_issue_messages():
    recheck = CountingRecheck(
        results={"f1": FieldResult(value="new1", status="explicit")}
    )
    issues = [
        ValidationIssue(
            type="semantic_unsupported",
            severity="warning",
            message="first reason",
            fields=["f1"],
            action="recheck",
        ),
        ValidationIssue(
            type="semantic_conflicting",
            severity="warning",
            message="second reason",
            fields=["f1"],
            action="recheck",
        ),
    ]
    trace = run_targeted_recheck(
        _definition(), _instance(), ["f1"], recheck, issues=issues
    )
    assert trace.entries[0].reason == "first reason; second reason"


def test_reason_defaults_to_manual_request():
    recheck = CountingRecheck(
        results={"f1": FieldResult(value="new1", status="explicit")}
    )
    trace = run_targeted_recheck(_definition(), _instance(), ["f1"], recheck)
    assert trace.entries[0].reason == "manual recheck request"


def test_recheck_trace_json_roundtrip():
    recheck = CountingRecheck(
        results={"f1": FieldResult(value="new1", status="explicit")}
    )
    trace = run_targeted_recheck(
        _definition(), _instance(), ["f1"], recheck, issues=[_issue(["f1"])]
    )
    rebuilt = RecheckTrace.model_validate_json(trace.model_dump_json())
    assert rebuilt.model_dump() == trace.model_dump()
