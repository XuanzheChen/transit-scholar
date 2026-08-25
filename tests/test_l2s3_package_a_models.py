from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from transit_scholar.layer2.wiki import (
    PageEntityLink,
    PaperMetadata,
    WikiEntity,
    WikiManifest,
    WikiPage,
    WorkspaceContext,
    entity_id_for,
    link_id_for,
    page_id_for,
)


def test_context_metadata_and_identifier_validation():
    context = WorkspaceContext(workspace_id="ws", schema_id="schema", schema_version="1", paper_ids=["p1", "p2", "p1"])
    assert context.paper_ids == ["p1", "p2"]
    assert PaperMetadata(paper_id="p1", title="A title", authors=["First", "Second"], year=2024).authors == ["First", "Second"]
    with pytest.raises(ValidationError):
        WorkspaceContext(workspace_id="../ws", schema_id="schema", schema_version="1", paper_ids=["p1"])
    with pytest.raises(ValidationError):
        PaperMetadata(paper_id="p1", title=" ")


def test_core_models_round_trip_and_validation():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    page = WikiPage(page_id=page_id_for("ws", "p1"), workspace_id="ws", paper_id="p1", title="Title", schema_id="schema", schema_version="1", created_at=timestamp, updated_at=timestamp)
    entity = WikiEntity(entity_id=entity_id_for("ws", "Signal Priority"), workspace_id="ws", canonical_name="Signal Priority", aliases=[" signal   priority ", "SP", "sp", ""], created_at=timestamp, updated_at=timestamp)
    link = PageEntityLink(link_id=link_id_for("ws", page.page_id, entity.entity_id, "field", "explicit", "schema", "1"), workspace_id="ws", page_id=page.page_id, entity_id=entity.entity_id, paper_id="p1", schema_id="schema", schema_version="1", source_field_id="field", source_status="explicit", confidence=0.4, created_at=timestamp)
    manifest = WikiManifest(workspace_id="ws", schema_id="schema", schema_version="1", paper_ids=["p1", "p1"], builder_version="v1", created_at=timestamp, updated_at=timestamp)
    assert entity.aliases == ["SP"]
    assert link.relation == "associated_with"
    assert manifest.paper_ids == ["p1"]
    for model in (page, entity, link, manifest):
        assert type(model).model_validate_json(model.model_dump_json()) == model
    with pytest.raises(ValidationError):
        WikiPage(page_id="x", workspace_id="ws", paper_id="p1", title="T", schema_id="schema", schema_version="1", build_status="bad")
    with pytest.raises(ValidationError):
        PageEntityLink(link_id="l", workspace_id="ws", page_id="p", entity_id="e", paper_id="p1", schema_id="schema", schema_version="1", source_field_id="f", source_status="explicit", confidence=1.1)


def test_deterministic_ids_are_workspace_scoped():
    assert page_id_for("ws", "p") == page_id_for("ws", "p")
    assert entity_id_for("ws", "  Name ") == entity_id_for("ws", "name")
    assert page_id_for("ws", "p") != page_id_for("other", "p")
    assert entity_id_for("ws", "name") != entity_id_for("other", "name")
