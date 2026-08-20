"""L2S2 Package A deterministic tests: schema core models, hashing, and
structural validation (requirements.md section 7 / AC-L2S2A-01..07, 12, 13).
"""

from __future__ import annotations

import datetime
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from transit_scholar.layer2.schema_extraction import (
    EvidenceRef,
    FieldDefinition,
    FieldResult,
    SchemaDefinition,
    SchemaInstance,
    SectionDefinition,
    ValidationIssue,
    compute_schema_hash,
    validate_schema_instance,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

ALL_STATUSES = (
    "explicit",
    "inferred",
    "unclear",
    "not_found",
    "not_applicable",
    "conflicting",
)


def _field(field_id: str, ftype: str = "string", **kwargs) -> FieldDefinition:
    values = dict(
        label=f"Label {field_id}",
        question=f"What is {field_id}?",
        type=ftype,
    )
    values.update(kwargs)
    return FieldDefinition(id=field_id, **values)


def _definition(**kwargs) -> SchemaDefinition:
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


# ---------------------------------------------------------------------------
# AC-L2S2A-01 public contract / import isolation
# ---------------------------------------------------------------------------


def test_public_contract_importable():
    from transit_scholar.layer2.schema_extraction import (
        SchemaDefinition as D,
        SectionDefinition as S,
        FieldDefinition as F,
        FieldResult as R,
        EvidenceRef as E,
        SchemaInstance as I,
        ValidationIssue as V,
        get_schema_definition,
        list_schema_plugins,
        validate_schema_instance,
        compute_schema_hash,
    )

    assert all(
        issubclass(cls, __import__("pydantic").BaseModel)
        for cls in (D, S, F, R, E, I, V)
    )
    assert callable(get_schema_definition)
    assert callable(list_schema_plugins)
    assert callable(validate_schema_instance)
    assert callable(compute_schema_hash)


def test_import_isolation_in_fresh_subprocess():
    """Importing schema_extraction must not pull in the L2S1 stack (the
    acceptance command runs in a fresh interpreter; conftest already imports
    config/db in the test process, so we mirror the command in a subprocess)."""
    code = (
        "import sys; "
        "from transit_scholar.layer2 import schema_extraction; "
        "from transit_scholar.layer2.schema_extraction import SchemaDefinition, "
        "SectionDefinition, FieldDefinition, FieldResult, EvidenceRef, "
        "SchemaInstance, ValidationIssue, list_schema_plugins, "
        "get_schema_definition, validate_schema_instance, compute_schema_hash; "
        "forbidden = ['transit_scholar.config', 'transit_scholar.db', "
        "'transit_scholar.layer2.schema', 'transit_scholar.layer2.retrieval', "
        "'transit_scholar.layer2.parser', 'transit_scholar.layer2.chunker', "
        "'transit_scholar.layer2.pipeline', "
        "'transit_scholar.layer2.normalizer']; "
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


# ---------------------------------------------------------------------------
# AC-L2S2A-02 definition models, type system, serialization
# ---------------------------------------------------------------------------


def test_schema_definition_minimal_roundtrip():
    definition = _definition()
    rebuilt = SchemaDefinition.model_validate_json(definition.model_dump_json())
    assert rebuilt.model_dump() == definition.model_dump()


def test_schema_definition_missing_required_rejected():
    with pytest.raises(ValidationError):
        SchemaDefinition(version="1.0", sections=[_definition().sections[0]])
    with pytest.raises(ValidationError):
        SchemaDefinition(schema_id="x", sections=[_definition().sections[0]])
    with pytest.raises(ValidationError):
        SchemaDefinition(schema_id="x", version="1.0")


def test_schema_definition_empty_sections_rejected():
    with pytest.raises(ValidationError):
        SchemaDefinition(schema_id="x", version="1.0", sections=[])


def test_section_required_fields_rejected():
    with pytest.raises(ValidationError):
        SectionDefinition(label="S", fields=[_field("f1")])
    with pytest.raises(ValidationError):
        SectionDefinition(id="s", fields=[_field("f1")])
    with pytest.raises(ValidationError):
        SectionDefinition(id="s", label="S")


def test_section_empty_fields_rejected():
    with pytest.raises(ValidationError):
        SectionDefinition(id="s", label="S", fields=[])


def test_field_required_fields_rejected():
    with pytest.raises(ValidationError):
        FieldDefinition(label="L", question="Q?", type="string")
    with pytest.raises(ValidationError):
        FieldDefinition(id="f", question="Q?", type="string")
    with pytest.raises(ValidationError):
        FieldDefinition(id="f", label="L", type="string")


def test_field_invalid_type_rejected():
    with pytest.raises(ValidationError):
        _field("bad", ftype="foo")


def test_all_valid_field_types_constructible():
    for ftype in ("string", "number", "boolean", "enum", "list", "object"):
        kwargs = {"options": ["a", "b"]} if ftype == "enum" else {}
        field = _field(f"f_{ftype}", ftype=ftype, **kwargs)
        assert field.type == ftype


def test_enum_requires_non_empty_options():
    with pytest.raises(ValidationError):
        _field("e", ftype="enum")
    with pytest.raises(ValidationError):
        _field("e", ftype="enum", options=[])


def test_enum_duplicate_options_rejected():
    with pytest.raises(ValidationError):
        _field("e", ftype="enum", options=["a", "b", "a"])


def test_non_enum_options_not_required():
    field = _field("s", ftype="string", options=None)
    assert field.options is None
    field2 = _field("s2", ftype="string", options=["x"])
    assert field2.options == ["x"]


def test_global_duplicate_field_id_rejected():
    sections = [
        SectionDefinition(id="s1", label="S1", fields=[_field("dup")]),
        SectionDefinition(id="s2", label="S2", fields=[_field("dup")]),
    ]
    with pytest.raises(ValidationError):
        SchemaDefinition(schema_id="x", version="1.0", sections=sections)


def test_sections_and_fields_keep_list_order():
    definition = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("a"), _field("b"), _field("c")],
            ),
            SectionDefinition(
                id="s2",
                label="S2",
                fields=[_field("d")],
            ),
        ]
    )
    assert [s.id for s in definition.sections] == ["s1", "s2"]
    assert [f.id for f in definition.sections[0].fields] == ["a", "b", "c"]


def test_field_defaults_explicit():
    field = _field("f", ftype="list")
    assert field.evidence_required is False
    assert field.allow_inference is True
    assert field.description == ""
    assert field.constraints == {}


def test_seven_models_json_roundtrip():
    definition = _definition()
    instance = SchemaInstance(
        paper_id="p1",
        schema_id="test_schema",
        schema_version="1.0",
        fields={
            "f1": FieldResult(value="x", status="explicit"),
            "f2": FieldResult(
                value=1.5,
                status="inferred",
                evidence=[
                    EvidenceRef(
                        block_id="blk_1",
                        char_start=10,
                        char_end=40,
                        pages=[2],
                        section_path=["2 Method"],
                        quote="some quote",
                    )
                ],
                confidence=0.8,
            ),
        },
    )
    issue = ValidationIssue(
        type="missing_field",
        severity="warning",
        message="field f3 missing",
        fields=["f3"],
        action="recheck",
    )
    for model, data in (
        (definition, definition.model_dump()),
        (instance, instance.model_dump()),
        (issue, issue.model_dump()),
        (instance.fields["f2"], instance.fields["f2"].model_dump()),
        (instance.fields["f2"].evidence[0], instance.fields["f2"].evidence[0].model_dump()),
    ):
        rebuilt = type(model).model_validate_json(model.model_dump_json())
        assert rebuilt.model_dump() == data


# ---------------------------------------------------------------------------
# AC-L2S2A-03 FieldResult status / confidence
# ---------------------------------------------------------------------------


def test_all_six_statuses_constructible_and_distinct():
    results = [FieldResult(value=None, status=s) for s in ALL_STATUSES]
    assert {r.status for r in results} == set(ALL_STATUSES)
    not_found = FieldResult(status="not_found")
    not_applicable = FieldResult(status="not_applicable")
    assert not_found.status != not_applicable.status
    assert not_found.model_dump() != not_applicable.model_dump()


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        FieldResult(value=None, status="found")


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        FieldResult(value=None, status="explicit", confidence=-0.1)
    with pytest.raises(ValidationError):
        FieldResult(value=None, status="explicit", confidence=1.1)


def test_confidence_boundaries_accepted():
    for value in (0.0, 0.5, 1.0, None):
        result = FieldResult(value=None, status="explicit", confidence=value)
        assert result.confidence == value


def test_field_result_roundtrip_with_all_fields():
    result = FieldResult(
        value={"k": "v"},
        status="conflicting",
        evidence=[
            EvidenceRef(
                block_id="b",
                char_start=0,
                char_end=5,
                pages=[1, 3],
                section_path=["s"],
                quote="q",
            )
        ],
        confidence=0.42,
        notes="some note",
    )
    rebuilt = FieldResult.model_validate_json(result.model_dump_json())
    assert rebuilt.model_dump() == result.model_dump()


# ---------------------------------------------------------------------------
# AC-L2S2A-04 EvidenceRef
# ---------------------------------------------------------------------------


def test_evidence_ref_all_six_fields_present():
    ref = EvidenceRef(
        block_id="blk_127",
        char_start=32,
        char_end=186,
        pages=[6],
        section_path=["3 Method", "3.2 Reward"],
        quote="reward is defined as",
    )
    assert ref.block_id == "blk_127"
    assert ref.char_start == 32
    assert ref.char_end == 186
    assert ref.pages == [6]
    assert ref.section_path == ["3 Method", "3.2 Reward"]
    assert ref.quote == "reward is defined as"
    rebuilt = EvidenceRef.model_validate_json(ref.model_dump_json())
    assert rebuilt.model_dump() == ref.model_dump()


def test_evidence_ref_char_range_validation():
    with pytest.raises(ValidationError):
        EvidenceRef(block_id="b", char_start=10, char_end=5)
    with pytest.raises(ValidationError):
        EvidenceRef(block_id="b", char_start=-1, char_end=5)
    with pytest.raises(ValidationError):
        EvidenceRef(block_id="b", char_start=0, char_end=-2)
    EvidenceRef(block_id="b", char_start=5, char_end=5)  # equal is valid


def test_evidence_ref_empty_block_id_rejected():
    with pytest.raises(ValidationError):
        EvidenceRef(block_id="", char_start=0, char_end=1)


# ---------------------------------------------------------------------------
# AC-L2S2A-05 SchemaInstance
# ---------------------------------------------------------------------------


def test_schema_instance_map_semantics_and_roundtrip():
    instance = SchemaInstance(
        paper_id="paper_001",
        schema_id="test_schema",
        schema_version="1.0",
        fields={
            "f1": FieldResult(value="a", status="explicit"),
            "f2": FieldResult(value=2, status="not_found"),
        },
    )
    rebuilt = SchemaInstance.model_validate_json(instance.model_dump_json())
    assert rebuilt.model_dump() == instance.model_dump()
    assert set(rebuilt.fields) == {"f1", "f2"}


def test_schema_instance_required_fields_rejected():
    with pytest.raises(ValidationError):
        SchemaInstance(schema_id="x", schema_version="1.0", fields={})
    with pytest.raises(ValidationError):
        SchemaInstance(paper_id="p", schema_version="1.0", fields={})
    with pytest.raises(ValidationError):
        SchemaInstance(paper_id="p", schema_id="x", fields={})


# ---------------------------------------------------------------------------
# AC-L2S2A-06 ValidationIssue
# ---------------------------------------------------------------------------


def test_validation_issue_roundtrip_and_defaults():
    issue = ValidationIssue(
        type="unknown_field", severity="error", message="boom"
    )
    assert issue.fields == []
    assert issue.action is None
    rebuilt = ValidationIssue.model_validate_json(issue.model_dump_json())
    assert rebuilt.model_dump() == issue.model_dump()


def test_validation_issue_invalid_severity_rejected():
    with pytest.raises(ValidationError):
        ValidationIssue(type="t", severity="fatal", message="m")
    with pytest.raises(ValidationError):
        ValidationIssue(type="t", severity="error", message="")


# ---------------------------------------------------------------------------
# AC-L2S2A-07 schema hash / version
# ---------------------------------------------------------------------------


def test_hash_stable_across_independent_construction():
    definition_a = _definition()
    definition_b = _definition()
    rebuilt = SchemaDefinition.model_validate_json(definition_a.model_dump_json())
    assert compute_schema_hash(definition_a) == compute_schema_hash(definition_b)
    assert compute_schema_hash(definition_a) == compute_schema_hash(rebuilt)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: setattr(d, "version", "1.1"),
        lambda d: setattr(d, "name", "other name"),
        lambda d: setattr(d, "description", "other description"),
        lambda d: setattr(d.sections[0], "label", "Changed"),
        lambda d: setattr(d.sections[0].fields[0], "label", "Changed"),
        lambda d: setattr(d.sections[0].fields[0], "description", "Changed"),
        lambda d: setattr(d.sections[0].fields[0], "question", "Changed?"),
        lambda d: setattr(d.sections[0].fields[0], "type", "boolean"),
        lambda d: setattr(d.sections[0].fields[0], "evidence_required", True),
        lambda d: setattr(d.sections[0].fields[0], "allow_inference", False),
        lambda d: setattr(d.sections[0].fields[0], "constraints", {"min": 0}),
    ],
)
def test_hash_changes_on_content_change(mutate):
    base = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[
                    _field("f1", "enum", options=["a", "b"]),
                    _field("f2", "number"),
                ],
            ),
        ]
    )
    changed = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[
                    _field("f1", "enum", options=["a", "b"]),
                    _field("f2", "number"),
                ],
            ),
        ]
    )
    mutate(changed)
    assert compute_schema_hash(base) != compute_schema_hash(changed)


def test_hash_changes_when_enum_options_change():
    base = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("f1", "enum", options=["a", "b"])],
            ),
        ]
    )
    changed = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("f1", "enum", options=["a", "c"])],
            ),
        ]
    )
    assert compute_schema_hash(base) != compute_schema_hash(changed)


def test_hash_changes_on_field_order_change():
    fields = [_field("a"), _field("b")]
    base = _definition(
        sections=[SectionDefinition(id="s1", label="S1", fields=fields)]
    )
    changed = _definition(
        sections=[SectionDefinition(id="s1", label="S1", fields=list(reversed(fields)))]
    )
    assert compute_schema_hash(base) != compute_schema_hash(changed)


def test_hash_changes_on_section_order_change():
    first = _definition(
        sections=[
            SectionDefinition(id="s1", label="S1", fields=[_field("a")]),
            SectionDefinition(id="s2", label="S2", fields=[_field("b")]),
        ]
    )
    second = _definition(
        sections=[
            SectionDefinition(id="s2", label="S2", fields=[_field("b")]),
            SectionDefinition(id="s1", label="S1", fields=[_field("a")]),
        ]
    )
    assert compute_schema_hash(first) != compute_schema_hash(second)


def test_hash_excludes_environment_info():
    definition = _definition()
    digest = compute_schema_hash(definition)
    repo_root = str(REPO_ROOT).lower()
    today = datetime.date.today().isoformat()
    assert repo_root not in digest.lower()
    assert today not in digest


# ---------------------------------------------------------------------------
# AC-L2S2A-12 structural validation
# ---------------------------------------------------------------------------


def _instance(definition, **field_values) -> SchemaInstance:
    fields = {}
    for field_id, value in field_values.items():
        fields[field_id] = FieldResult(value=value, status="explicit")
    return SchemaInstance(
        paper_id="p1",
        schema_id=definition.schema_id,
        schema_version=definition.version,
        fields=fields,
    )


def test_validate_benign_instance_returns_empty_list():
    definition = _definition()
    instance = _instance(definition, f1="hello", f2=3)
    assert validate_schema_instance(definition, instance) == []


def test_validate_schema_id_mismatch():
    definition = _definition()
    instance = _instance(definition, f1="hello", f2=3)
    instance.schema_id = "other_schema"
    issues = validate_schema_instance(definition, instance)
    assert any(i.type == "schema_mismatch" and i.severity == "error" for i in issues)


def test_validate_schema_version_mismatch():
    definition = _definition()
    instance = _instance(definition, f1="hello", f2=3)
    instance.schema_version = "9.9"
    issues = validate_schema_instance(definition, instance)
    assert any(
        i.type == "schema_version_mismatch" and i.severity == "error" for i in issues
    )


def test_validate_unknown_instance_field():
    definition = _definition()
    instance = _instance(definition, f1="hello", f2=3, ghost="x")
    issues = validate_schema_instance(definition, instance)
    unknown = [i for i in issues if i.type == "unknown_field"]
    assert len(unknown) == 1
    assert unknown[0].severity == "error"
    assert unknown[0].fields == ["ghost"]


def test_validate_missing_definition_field_warning():
    definition = _definition()
    instance = _instance(definition, f1="hello")
    issues = validate_schema_instance(definition, instance)
    missing = [i for i in issues if i.type == "missing_field"]
    assert len(missing) == 1
    assert missing[0].severity == "warning"
    assert missing[0].fields == ["f2"]


def test_validate_enum_invalid_value():
    definition = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("choice", "enum", options=["a", "b"])],
            ),
        ]
    )
    instance = _instance(definition, choice="c")
    issues = validate_schema_instance(definition, instance)
    enum_issues = [i for i in issues if i.type == "invalid_enum_value"]
    assert len(enum_issues) == 1
    assert enum_issues[0].severity == "error"
    assert enum_issues[0].fields == ["choice"]


def test_validate_enum_none_value_ok():
    definition = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("choice", "enum", options=["a", "b"])],
            ),
        ]
    )
    instance = SchemaInstance(
        paper_id="p1",
        schema_id=definition.schema_id,
        schema_version=definition.version,
        fields={"choice": FieldResult(value=None, status="not_found")},
    )
    assert validate_schema_instance(definition, instance) == []


def test_validate_enum_valid_value_ok():
    definition = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("choice", "enum", options=["a", "b"])],
            ),
        ]
    )
    instance = _instance(definition, choice="a")
    assert validate_schema_instance(definition, instance) == []


def test_validate_missing_evidence_warning_for_assertive_statuses():
    definition = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("f1", evidence_required=True)],
            ),
        ]
    )
    for status in ("explicit", "inferred"):
        instance = SchemaInstance(
            paper_id="p1",
            schema_id=definition.schema_id,
            schema_version=definition.version,
            fields={"f1": FieldResult(value="v", status=status)},
        )
        issues = validate_schema_instance(definition, instance)
        missing_evidence = [i for i in issues if i.type == "missing_evidence"]
        assert len(missing_evidence) == 1
        assert missing_evidence[0].severity == "warning"
        assert missing_evidence[0].fields == ["f1"]


def test_validate_non_assertive_statuses_allow_empty_evidence():
    definition = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("f1", evidence_required=True)],
            ),
        ]
    )
    for status in ("unclear", "not_found", "not_applicable", "conflicting"):
        instance = SchemaInstance(
            paper_id="p1",
            schema_id=definition.schema_id,
            schema_version=definition.version,
            fields={"f1": FieldResult(value=None, status=status)},
        )
        assert validate_schema_instance(definition, instance) == []


def test_validate_evidence_present_satisfies_requirement():
    definition = _definition(
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("f1", evidence_required=True)],
            ),
        ]
    )
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


def test_validate_evidence_not_required_no_issue():
    definition = _definition()  # default evidence_required=False
    instance = SchemaInstance(
        paper_id="p1",
        schema_id=definition.schema_id,
        schema_version=definition.version,
        fields={
            "f1": FieldResult(value="v", status="explicit"),
            "f2": FieldResult(value=1, status="explicit"),
        },
    )
    assert validate_schema_instance(definition, instance) == []


# ---------------------------------------------------------------------------
# AC-L2S2A-13 dependencies
# ---------------------------------------------------------------------------


def test_pyproject_declares_pydantic_and_pyyaml():
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    dependencies = data["project"]["dependencies"]
    names = [d.split(">")[0].split("=")[0].strip().lower() for d in dependencies]
    assert "pydantic" in names
    assert "pyyaml" in names
