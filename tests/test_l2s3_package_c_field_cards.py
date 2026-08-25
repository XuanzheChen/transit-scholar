import json

import pytest

from transit_scholar.layer2.schema_extraction import (
    EvidenceRef, FieldDefinition, FieldResult, SchemaDefinition, SchemaInstance,
    SectionDefinition,
)
from transit_scholar.layer2.wiki import FieldCardValidationError, build_field_cards


def fixture():
    definition = SchemaDefinition(schema_id="generic", version="1", sections=[
        SectionDefinition(id="one", label="One", fields=[
            FieldDefinition(id="a", label="A", question="What?", description="D", type="object", constraints={"x": [1]}),
            FieldDefinition(id="b", label="B", question="Why?", type="string"),
        ])
    ])
    instance = SchemaInstance(paper_id="paper", schema_id="generic", schema_version="1", fields={
        "b": FieldResult(value="v", status="explicit"),
        "a": FieldResult(value={"n": [2]}, status="unclear", evidence=[EvidenceRef(block_id="block", char_start=0, char_end=1, pages=[1], section_path=["s"])]),
    })
    return definition, instance


def test_cards_are_authored_order_and_lossless():
    definition, instance = fixture()
    cards = build_field_cards(definition, instance)
    assert [card.field_id for card in cards] == ["a", "b"]
    assert cards[0].status == "unclear"
    assert cards[0].model_dump(mode="json")["constraints"] == {"x": [1]}
    assert cards[0].evidence[0].block_id == "block"


def test_default_excludes_absent_and_mismatch_is_typed():
    definition, instance = fixture()
    instance.fields["a"] = FieldResult(status="not_found")
    assert [card.field_id for card in build_field_cards(definition, instance)] == ["b"]
    bad = instance.model_copy(update={"schema_version": "2"})
    with pytest.raises(FieldCardValidationError, match="schema identity") as error:
        build_field_cards(definition, bad)
    assert error.value.code == "schema_mismatch"


def test_nested_values_are_deeply_immutable_and_sources_unchanged():
    definition, instance = fixture()
    card = build_field_cards(definition, instance)[0]
    with pytest.raises((AttributeError, TypeError)):
        card.value["n"].append(3)
    with pytest.raises(TypeError):
        card.constraints["x"] = []
    with pytest.raises((AttributeError, TypeError)):
        card.evidence[0].pages.append(2)
    assert instance.fields["a"].value == {"n": [2]}
    assert definition.sections[0].fields[0].constraints == {"x": [1]}


def test_nested_mapping_json_is_canonical_across_insertion_orders():
    definition, instance = fixture()
    first = build_field_cards(definition, instance)[0]
    instance.fields["a"] = FieldResult(value={"z": {"b": 1, "a": 2}, "a": [3]}, status="unclear")
    reordered = build_field_cards(definition, instance)[0]
    definition.sections[0].fields[0].constraints = {"z": {"b": 1, "a": 2}, "a": [3]}
    card_one = build_field_cards(definition, instance)[0]
    definition.sections[0].fields[0].constraints = {"a": [3], "z": {"a": 2, "b": 1}}
    card_two = build_field_cards(definition, instance)[0]
    assert first.model_dump_json() == first.model_dump_json()
    assert card_one.model_dump_json() == card_two.model_dump_json()
    assert json.loads(reordered.model_dump_json())["value"] == {"a": [3], "z": {"a": 2, "b": 1}}


@pytest.mark.parametrize("definition,instance", [
    (SchemaDefinition.model_construct(schema_id="generic", version="1", sections=None), SchemaInstance.model_construct(paper_id="p", schema_id="generic", schema_version="1", fields={})),
    (SchemaDefinition.model_construct(schema_id="generic", version="1", sections=[]), SchemaInstance.model_construct(paper_id="p", schema_id="generic", schema_version="1", fields=None)),
    (SchemaDefinition.model_construct(schema_id=None, version="1", sections=[]), SchemaInstance.model_construct(paper_id="p", schema_id="generic", schema_version="1", fields={})),
])
def test_constructed_malformed_schema_models_raise_typed_error(definition, instance):
    with pytest.raises(FieldCardValidationError) as error:
        build_field_cards(definition, instance)
    assert error.value.code == "invalid_input"


def test_constructed_malformed_result_raises_typed_error():
    definition, instance = fixture()
    instance.fields["a"] = FieldResult.model_construct(status=None, evidence=None)
    with pytest.raises(FieldCardValidationError) as error:
        build_field_cards(definition, instance)
    assert error.value.code == "invalid_input"


def test_unknown_and_missing_fields_are_schema_mismatch():
    definition, instance = fixture()
    instance.fields["extra"] = instance.fields.pop("a")
    with pytest.raises(FieldCardValidationError) as error:
        build_field_cards(definition, instance)
    assert error.value.code == "schema_mismatch"


def test_explicit_status_filter_preserves_requested_weak_status():
    definition, instance = fixture()
    instance.fields["b"] = FieldResult(value="v", status="conflicting")
    cards = build_field_cards(definition, instance, include_statuses=["conflicting"])
    assert [card.field_id for card in cards] == ["b"] and cards[0].status == "conflicting"

def test_output_guidance_is_deeply_immutable():
    definition, instance = fixture()
    definition.sections[0].fields[0].output_guidance = {"nested": {"items": ["x"]}}
    card = build_field_cards(definition, instance)[0]
    with pytest.raises((AttributeError, TypeError)):
        card.output_guidance["nested"]["items"].append("y")
    assert definition.sections[0].fields[0].output_guidance == {"nested": {"items": ["x"]}}
