import pytest

from transit_scholar.layer2.wiki import (
    EntityProposal, EntityProposalRunner, build_entity_proposal_prompt,
    build_proposal_request,
)
from test_l2s3_package_c_field_cards import fixture
from transit_scholar.layer2.wiki import build_field_cards


def request():
    definition, instance = fixture()
    return build_proposal_request(build_field_cards(definition, instance))


def record(name="Entity", source="a"):
    return {"canonical_name": name, "aliases": [" entity ", " A ", "a"], "description": "d", "source_field_id": source, "confidence": 1}


def test_aliases_normalize_and_provider_called_once():
    calls = []
    def provider(value):
        calls.append(value.to_json())
        return {"proposals": [record()]}
    proposal_request = request()
    result = EntityProposalRunner(provider).run(proposal_request)
    assert result.status == "success" and len(calls) == 1
    assert result.proposals[0].canonical_name == "Entity"
    assert result.proposals[0].aliases == ("A",)
    assert build_entity_proposal_prompt(proposal_request) == calls[0]


def test_provider_non_success_outcomes_are_explicit():
    cases = [
        (None, "missing_output"), ("text", "malformed_output"), (b"", "malformed_output"),
        ([], "malformed_output"), ({}, "malformed_output"), ({"proposals": None}, "malformed_output"),
        ({"proposals": [{"canonical_name": "x"}]}, "invalid_output"),
    ]
    for output, error_code in cases:
        result = EntityProposalRunner(lambda _: output).run(request())
        assert result.error_code == error_code
        assert result.status != "success_empty" and len(result.cards) == 2


def test_validated_empty_envelope_is_success_empty():
    result = EntityProposalRunner(lambda _: {"proposals": []}).run(request())
    assert result.status == "success_empty" and result.error_code is None and result.proposals == ()


def test_proposals_have_deterministic_trace_name_order():
    result = EntityProposalRunner(lambda _: {"proposals": [record("Zulu", "b"), record("alpha", "a")]}).run(request())
    assert [proposal.canonical_name for proposal in result.proposals] == ["alpha", "Zulu"]


def test_entity_proposal_aliases_are_immutable():
    proposal = EntityProposal.model_validate(record())
    with pytest.raises((AttributeError, TypeError)):
        proposal.aliases.append("new")
    assert proposal.aliases == ("A",)


def test_request_isolated_from_cards_and_json_is_repeatable():
    definition, instance = fixture()
    cards = build_field_cards(definition, instance)
    proposal_request = build_proposal_request(cards)
    with pytest.raises((AttributeError, TypeError)):
        proposal_request.cards[0].value["n"].append(7)
    assert proposal_request.to_json() == proposal_request.to_json()
    assert instance.fields["a"].value == {"n": [2]}


def test_provider_exception_is_sanitized_failure():
    result = EntityProposalRunner(lambda _: (_ for _ in ()).throw(RuntimeError("secret endpoint"))).run(request())
    assert result.status == "provider_failure" and result.error_code == "provider_failure"