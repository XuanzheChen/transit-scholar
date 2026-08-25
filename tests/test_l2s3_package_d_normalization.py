import pytest

from transit_scholar.layer2.wiki import WikiEntity, entity_id_for, normalize_entity_name


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  SIGNAL\u00a0Priority  ", "signal priority"),
        ("Signal \u00b7 Priority", "signal priority"),
        ("Route\u2011V2", "route-v2"),
    ],
)
def test_generic_normalization_is_idempotent(value, expected):
    assert normalize_entity_name(value) == expected
    assert normalize_entity_name(normalize_entity_name(value)) == expected


def test_identity_and_aliases_share_normalization_rules():
    assert entity_id_for("ws", "Signal \u00b7 Priority") == entity_id_for("ws", "signal priority")
    entity = WikiEntity(entity_id=entity_id_for("ws", "Signal Priority"), workspace_id="ws", canonical_name="Signal Priority", aliases=[" signal \u00b7 priority ", "S-P", "s-p"])
    assert entity.aliases == ["S-P"]


@pytest.mark.parametrize("value", ["", "  ", "...", "\u2022\u2022"])
def test_invalid_names_cannot_create_identity(value):
    assert normalize_entity_name(value) == ""
    with pytest.raises(ValueError):
        entity_id_for("ws", value)


def test_non_string_name_is_rejected():
    with pytest.raises(ValueError):
        normalize_entity_name(None)  # type: ignore[arg-type]
