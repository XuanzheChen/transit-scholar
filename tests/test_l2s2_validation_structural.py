"""L2S2 Package C deterministic tests: enhanced structural validation
(FR-C-002 / AC-C-02).

Covers the value-type matrix, bool-as-number rejection, assertive values on
non-assertive statuses, ``model_construct`` out-of-range inputs, and Package
A old-rule compatibility. All runs are offline and deterministic.
"""

from __future__ import annotations

import pytest

from transit_scholar.layer2.schema_extraction import (
    EvidenceRef,
    FieldDefinition,
    FieldResult,
    SchemaDefinition,
    SchemaInstance,
    SectionDefinition,
    validate_schema_instance,
)


def _field(field_id: str, ftype: str = "string", **kwargs) -> FieldDefinition:
    values = dict(
        label=f"Label {field_id}",
        question=f"What is {field_id}?",
        type=ftype,
    )
    values.update(kwargs)
    return FieldDefinition(id=field_id, **values)


def _single_field_definition(
    field_id: str, ftype: str, **kwargs
) -> SchemaDefinition:
    return SchemaDefinition(
        schema_id="test_schema",
        version="1.0",
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field(field_id, ftype, **kwargs)],
            ),
        ],
    )


def _instance(definition: SchemaDefinition, result: FieldResult) -> SchemaInstance:
    return SchemaInstance(
        paper_id="p1",
        schema_id=definition.schema_id,
        schema_version=definition.version,
        fields={"f": result},
    )


def _issues_for(
    definition: SchemaDefinition, result: FieldResult
) -> list[str]:
    return [
        issue.type
        for issue in validate_schema_instance(
            definition, _instance(definition, result)
        )
    ]


# ---------------------------------------------------------------------------
# AC-C-02.2 value-type matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ftype,value,expect_error",
    [
        # string
        ("string", "x", False),
        ("string", None, False),
        ("string", 1, True),
        ("string", True, True),
        ("string", [], True),
        # number: bool must be rejected even though bool is an int subclass
        ("number", 1, False),
        ("number", 1.5, False),
        ("number", None, False),
        ("number", True, True),
        ("number", False, True),
        ("number", "3", True),
        # boolean
        ("boolean", True, False),
        ("boolean", False, False),
        ("boolean", None, False),
        ("boolean", 1, True),
        ("boolean", "yes", True),
        # list
        ("list", [], False),
        ("list", ["a"], False),
        ("list", None, False),
        ("list", "x", True),
        ("list", {}, True),
        # object
        ("object", {}, False),
        ("object", {"a": 1}, False),
        ("object", None, False),
        ("object", [], True),
        ("object", "x", True),
    ],
)
def test_value_type_matrix(ftype, value, expect_error):
    definition = _single_field_definition("f", ftype)
    result = FieldResult(value=value, status="explicit")
    types = _issues_for(definition, result)
    if expect_error:
        assert "invalid_value_type" in types
    else:
        assert "invalid_value_type" not in types


def test_invalid_value_type_is_error_with_field():
    definition = _single_field_definition("f", "number")
    issues = validate_schema_instance(
        definition, _instance(definition, FieldResult(value=True, status="explicit"))
    )
    invalid = [i for i in issues if i.type == "invalid_value_type"]
    assert len(invalid) == 1
    assert invalid[0].severity == "error"
    assert invalid[0].fields == ["f"]


def test_enum_bad_value_keeps_old_rule():
    definition = _single_field_definition("f", "enum", options=["a", "b"])
    for bad in ("c", 42):
        issues = validate_schema_instance(
            definition, _instance(definition, FieldResult(value=bad, status="explicit"))
        )
        enum_issues = [i for i in issues if i.type == "invalid_enum_value"]
        assert len(enum_issues) == 1
        assert enum_issues[0].severity == "error"
        assert "invalid_value_type" not in [i.type for i in issues]


def test_enum_valid_value_no_issue():
    definition = _single_field_definition("f", "enum", options=["a", "b"])
    assert validate_schema_instance(
        definition, _instance(definition, FieldResult(value="a", status="explicit"))
    ) == []


# ---------------------------------------------------------------------------
# AC-C-02.3 assertive value with non-assertive status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,value,expect_error",
    [
        ("not_found", "x", True),
        ("not_found", 5, True),
        ("not_found", 0, True),
        ("not_found", False, True),
        ("not_found", None, False),
        ("not_found", "", False),
        ("not_found", [], False),
        ("not_found", {}, False),
        ("not_applicable", "x", True),
        ("not_applicable", 5, True),
        ("not_applicable", None, False),
        ("not_applicable", [], False),
    ],
)
def test_assertive_value_with_non_assertive_status(status, value, expect_error):
    definition = _single_field_definition("f", "string")
    result = FieldResult(value=value, status=status)
    types = _issues_for(definition, result)
    if expect_error:
        assert "assertive_value_with_non_assertive_status" in types
        issues = validate_schema_instance(definition, _instance(definition, result))
        match = [
            i
            for i in issues
            if i.type == "assertive_value_with_non_assertive_status"
        ]
        assert match[0].severity == "error"
    else:
        assert "assertive_value_with_non_assertive_status" not in types


# ---------------------------------------------------------------------------
# AC-C-02.4 model_construct out-of-range inputs are carried, not raised
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confidence", [1.5, -0.1, "high", True])
def test_model_construct_invalid_confidence_becomes_issue(confidence):
    definition = _single_field_definition("f", "string")
    result = FieldResult.model_construct(
        value="v", status="explicit", confidence=confidence
    )
    issues = validate_schema_instance(definition, _instance(definition, result))
    invalid = [i for i in issues if i.type == "invalid_confidence"]
    assert len(invalid) == 1
    assert invalid[0].severity == "error"
    assert invalid[0].fields == ["f"]


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0, None])
def test_model_construct_valid_confidence_no_issue(confidence):
    definition = _single_field_definition("f", "string")
    result = FieldResult.model_construct(
        value="v", status="explicit", confidence=confidence
    )
    assert "invalid_confidence" not in _issues_for(definition, result)


def test_model_construct_inverted_evidence_range_becomes_issue():
    definition = _single_field_definition("f", "string")
    ref = EvidenceRef.model_construct(block_id="b", char_start=10, char_end=5)
    result = FieldResult.model_construct(
        value="v", status="explicit", evidence=[ref]
    )
    issues = validate_schema_instance(definition, _instance(definition, result))
    invalid = [i for i in issues if i.type == "invalid_evidence_range"]
    assert len(invalid) == 1
    assert invalid[0].severity == "error"
    assert invalid[0].fields == ["f"]


def test_model_construct_valid_evidence_range_no_issue():
    definition = _single_field_definition("f", "string")
    ref = EvidenceRef(block_id="b", char_start=0, char_end=5)
    result = FieldResult(value="v", status="explicit", evidence=[ref])
    assert validate_schema_instance(definition, _instance(definition, result)) == []


# ---------------------------------------------------------------------------
# AC-C-02.1 old Package A rules stay compatible
# ---------------------------------------------------------------------------


def _old_definition(**kwargs) -> SchemaDefinition:
    values = dict(
        schema_id="test_schema",
        version="1.0",
        sections=[
            SectionDefinition(
                id="s1",
                label="Section One",
                fields=[_field("f1"), _field("f2", "number")],
            ),
        ],
    )
    values.update(kwargs)
    return SchemaDefinition(**values)


def _old_instance(definition, **field_values) -> SchemaInstance:
    fields = {
        field_id: FieldResult(value=value, status="explicit")
        for field_id, value in field_values.items()
    }
    return SchemaInstance(
        paper_id="p1",
        schema_id=definition.schema_id,
        schema_version=definition.version,
        fields=fields,
    )


def test_old_rule_benign_instance_returns_empty_list():
    definition = _old_definition()
    instance = _old_instance(definition, f1="hello", f2=3)
    assert validate_schema_instance(definition, instance) == []


def test_old_rule_schema_id_mismatch():
    definition = _old_definition()
    instance = _old_instance(definition, f1="hello", f2=3)
    instance.schema_id = "other_schema"
    issues = validate_schema_instance(definition, instance)
    match = [i for i in issues if i.type == "schema_mismatch"]
    assert len(match) == 1
    assert match[0].severity == "error"
    assert "other_schema" in match[0].message


def test_old_rule_schema_version_mismatch():
    definition = _old_definition()
    instance = _old_instance(definition, f1="hello", f2=3)
    instance.schema_version = "9.9"
    issues = validate_schema_instance(definition, instance)
    match = [i for i in issues if i.type == "schema_version_mismatch"]
    assert len(match) == 1
    assert match[0].severity == "error"


def test_old_rule_unknown_field():
    definition = _old_definition()
    instance = _old_instance(definition, f1="hello", f2=3, ghost="x")
    issues = validate_schema_instance(definition, instance)
    unknown = [i for i in issues if i.type == "unknown_field"]
    assert len(unknown) == 1
    assert unknown[0].severity == "error"
    assert unknown[0].fields == ["ghost"]


def test_old_rule_missing_field_warning():
    definition = _old_definition()
    instance = _old_instance(definition, f1="hello")
    issues = validate_schema_instance(definition, instance)
    missing = [i for i in issues if i.type == "missing_field"]
    assert len(missing) == 1
    assert missing[0].severity == "warning"
    assert missing[0].fields == ["f2"]


def test_old_rule_enum_none_value_ok():
    definition = _single_field_definition("choice", "enum", options=["a", "b"])
    instance = SchemaInstance(
        paper_id="p1",
        schema_id=definition.schema_id,
        schema_version=definition.version,
        fields={"choice": FieldResult(value=None, status="not_found")},
    )
    assert validate_schema_instance(definition, instance) == []


def test_old_rule_missing_evidence_warning_for_assertive_statuses():
    definition = _single_field_definition("f1", "string", evidence_required=True)
    for status in ("explicit", "inferred"):
        instance = SchemaInstance(
            paper_id="p1",
            schema_id=definition.schema_id,
            schema_version=definition.version,
            fields={"f1": FieldResult(value="v", status=status)},
        )
        issues = validate_schema_instance(definition, instance)
        missing = [i for i in issues if i.type == "missing_evidence"]
        assert len(missing) == 1
        assert missing[0].severity == "warning"


def test_old_rule_non_assertive_statuses_allow_empty_evidence():
    definition = _single_field_definition("f1", "string", evidence_required=True)
    for status in ("unclear", "not_found", "not_applicable", "conflicting"):
        instance = SchemaInstance(
            paper_id="p1",
            schema_id=definition.schema_id,
            schema_version=definition.version,
            fields={"f1": FieldResult(value=None, status=status)},
        )
        assert validate_schema_instance(definition, instance) == []


def test_old_rule_evidence_present_satisfies_requirement():
    definition = _single_field_definition("f1", "string", evidence_required=True)
    instance = SchemaInstance(
        paper_id="p1",
        schema_id=definition.schema_id,
        schema_version=definition.version,
        fields={
            "f1": FieldResult(
                value="v",
                status="inferred",
                evidence=[EvidenceRef(block_id="b", char_start=0, char_end=4)],
            ),
        },
    )
    assert validate_schema_instance(definition, instance) == []


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_structural_validation_is_deterministic():
    definition = _single_field_definition("f", "number")
    result = FieldResult.model_construct(
        value=True, status="explicit", confidence=1.5
    )
    instance = _instance(definition, result)
    first = [i.model_dump() for i in validate_schema_instance(definition, instance)]
    second = [i.model_dump() for i in validate_schema_instance(definition, instance)]
    assert first == second
