import socket

import pytest

from transit_scholar.layer2.wiki import EntityProposalRunner, build_proposal_request, build_field_cards
from test_l2s3_package_c_field_cards import fixture


def test_package_c_provider_is_injected_and_offline(monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", "1")
    definition, instance = fixture()
    proposal_request = build_proposal_request(build_field_cards(definition, instance))
    result = EntityProposalRunner(lambda _: {"proposals": []}).run(proposal_request)
    assert result.status == "success_empty"
    assert not hasattr(result, "search")
    assert socket.socket is not None


def test_failure_cards_remain_deeply_immutable():
    definition, instance = fixture()
    result = EntityProposalRunner(lambda _: []).run(build_proposal_request(build_field_cards(definition, instance)))
    with pytest.raises((AttributeError, TypeError)):
        result.cards[0].evidence[0].section_path.append("changed")
    assert instance.fields["a"].evidence[0].section_path == ["s"]


def test_generic_schema_has_no_domain_specific_assumptions():
    definition, instance = fixture()
    definition.schema_id = "chemistry"
    instance.schema_id = "chemistry"
    cards = build_field_cards(definition, instance)
    assert [card.schema_id for card in cards] == ["chemistry", "chemistry"]


def test_request_rejects_non_cards():
    with pytest.raises(ValueError, match="cards"):
        build_proposal_request([object()])


def test_invalid_status_filter_is_typed():
    definition, instance = fixture()
    from transit_scholar.layer2.wiki import FieldCardValidationError
    with pytest.raises(FieldCardValidationError) as error:
        build_field_cards(definition, instance, include_statuses=["made_up"])
    assert error.value.code == "invalid_input"