"""Layer3 Stage1 gateway Workspace-Schema isolation tests (T-005 / AC-020).

Proves through the bound gateway that Workspace Schema reads resolve the
Workspace-specific Schema storage root only:

- AC-020: a bound-schema Workspace read returns only that Workspace's
  current SchemaInstance/field content — never a global or another
  Workspace's instance (AC-006/AC-007 semantics at the gateway boundary);
- deleting one Workspace's Schema storage leaves the other's content intact
  and readable through its own gateway;
- the membership gate fires before any Schema read (AC-018 for the Schema
  path): content stranded in a Workspace's root never re-authorizes a
  removed Paper;
- no-schema Workspaces report ``schema_disabled`` through the gateway with no
  fallback read (AC-007).

Extraction runs are fully offline (fake LLM provider through the real L2S2
public API, mirroring the T-004 suite).
"""

from __future__ import annotations

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    get_schema_definition,
)
from transit_scholar.layer3.knowledge import WorkspaceKnowledgeGateway
from transit_scholar.layer3.schema import SchemaDisabledError, SchemaMissingError
from transit_scholar.layer3.schema.service import WorkspaceSchemaService
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.workspace import (
    PaperNotMemberError,
    WorkspaceService,
)

DEFINITION = get_schema_definition("bus_control_rl")


class RecordingSchema:
    """Recording Schema seam; proves the gateway membership gate fires first."""

    def __init__(self) -> None:
        self.instance_calls: list[str] = []
        self.field_calls: list[tuple[str, str]] = []

    def get_instance(self, workspace_id, paper_id, *, run_id=None):
        self.instance_calls.append(paper_id)
        raise AssertionError("schema read must not run for a non-member (AC-018)")

    def get_field(self, workspace_id, paper_id, field_id, *, run_id=None):
        self.field_calls.append((paper_id, field_id))
        raise AssertionError("schema read must not run for a non-member (AC-018)")

    def current_run_identities(self, workspace_id, paper_ids=None):
        return {paper_id: None for paper_id in (paper_ids or [])}


def add_paper(session, paper_id="gs-p1", title="Schema Shared Paper") -> Paper:
    paper = Paper(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def create_bound_workspace(session, name, workspace_id):
    return WorkspaceService(session).create(
        name=name, schema_definition=DEFINITION, workspace_id=workspace_id
    ).workspace


def create_none_workspace(session, name="No Schema", workspace_id="gw-none"):
    return WorkspaceService(session).create(name=name, workspace_id=workspace_id).workspace


def schema_service(session, project_tmp_path):
    return WorkspaceSchemaService(session, data_root=project_tmp_path)


def gateway_for(session, project_tmp_path, workspace_id, *, schemas=None):
    return WorkspaceKnowledgeGateway(
        session, workspace_id=workspace_id, data_root=project_tmp_path, schemas=schemas
    )


# ---------------------------------------------------------------------------
# AC-020: schema reads resolve the bound Workspace storage root only
# ---------------------------------------------------------------------------


def test_workspace_schema_reads_never_use_another_workspaces_root(
    session, project_tmp_path
):
    paper = add_paper(session)
    ws_a = create_bound_workspace(session, "A", "gws-schema-a")
    ws_b = create_bound_workspace(session, "B", "gws-schema-b")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)

    schema = schema_service(session, project_tmp_path)
    # Only Workspace A has Workspace-owned Schema content for the Paper.
    result_a = schema.materialize(
        ws_a.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )

    gateway_a = gateway_for(session, project_tmp_path, ws_a.workspace_id)
    gateway_b = gateway_for(session, project_tmp_path, ws_b.workspace_id)

    # A reads its own current instance.
    instance_a = gateway_a.get_schema_instance(paper.id)
    assert instance_a.paper_id == paper.id
    assert instance_a.schema_id == DEFINITION.schema_id

    # B must NOT fall back to A's root: explicit schema_missing (AC-020).
    with pytest.raises(SchemaMissingError) as missing_b:
        gateway_b.get_schema_instance(paper.id)
    assert missing_b.value.code == "schema_missing"
    with pytest.raises(SchemaMissingError):
        gateway_b.get_schema_field(paper.id, "research_problem.control_type")
    assert not workspace_layout(
        ws_b.workspace_id, data_root=project_tmp_path
    ).schemas_dir.exists()
    assert result_a.run_id


def test_identical_paper_two_workspaces_read_independent_instances(
    session, project_tmp_path
):
    paper = add_paper(session)
    ws_a = create_bound_workspace(session, "A", "gws-iso-a")
    ws_b = create_bound_workspace(session, "B", "gws-iso-b")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)

    schema = schema_service(session, project_tmp_path)
    result_a = schema.materialize(
        ws_a.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    result_b = schema.materialize(
        ws_b.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )

    gateway_a = gateway_for(session, project_tmp_path, ws_a.workspace_id)
    gateway_b = gateway_for(session, project_tmp_path, ws_b.workspace_id)
    instance_a = gateway_a.get_schema_instance(paper.id)
    instance_b = gateway_b.get_schema_instance(paper.id)
    assert instance_a.paper_id == instance_b.paper_id == paper.id
    # Independent Workspace-owned runs for the same global Paper+Schema.
    assert result_a.run_id != result_b.run_id

    # Field reads return each Workspace's own persisted content.
    field_a = gateway_a.get_schema_field(paper.id, "research_problem.control_type")
    field_b = gateway_b.get_schema_field(paper.id, "research_problem.control_type")
    assert field_a == schema.get_field(
        ws_a.workspace_id, paper.id, "research_problem.control_type"
    )
    assert field_b == schema.get_field(
        ws_b.workspace_id, paper.id, "research_problem.control_type"
    )


def test_deleting_one_workspace_schema_storage_leaves_other_gateway_intact(
    session, project_tmp_path
):
    paper = add_paper(session)
    ws_a = create_bound_workspace(session, "A", "gws-del-a")
    ws_b = create_bound_workspace(session, "B", "gws-del-b")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)

    schema = schema_service(session, project_tmp_path)
    schema.materialize(ws_a.workspace_id, paper.id, llm_client=FakeLLMProvider())
    schema.materialize(ws_b.workspace_id, paper.id, llm_client=FakeLLMProvider())

    layout_a = workspace_layout(ws_a.workspace_id, data_root=project_tmp_path)
    layout_b = workspace_layout(ws_b.workspace_id, data_root=project_tmp_path)
    assert layout_a.schemas_dir != layout_b.schemas_dir
    layout_a.delete_schema_storage()

    gateway_a = gateway_for(session, project_tmp_path, ws_a.workspace_id)
    gateway_b = gateway_for(session, project_tmp_path, ws_b.workspace_id)
    with pytest.raises(SchemaMissingError) as missing_a:
        gateway_a.get_schema_instance(paper.id)
    assert missing_a.value.code == "schema_missing"
    # Workspace B's own root is untouched: full reads still work (AC-006) and
    # the gateway returns exactly B's persisted content (no fallback).
    instance_b = gateway_b.get_schema_instance(paper.id)
    assert instance_b.paper_id == paper.id
    field_b = gateway_b.get_schema_field(paper.id, "research_problem.control_type")
    assert field_b == schema.get_field(
        ws_b.workspace_id, paper.id, "research_problem.control_type"
    )


# ---------------------------------------------------------------------------
# AC-018 schema path: membership gate fires before any Schema read
# ---------------------------------------------------------------------------


def test_removed_paper_schema_stranded_files_never_reauthorize(
    session, project_tmp_path
):
    paper = add_paper(session)
    ws = create_bound_workspace(session, "Stranded", "gws-stranded")
    service = WorkspaceService(session)
    service.add_paper(ws.workspace_id, paper.id)
    schema = schema_service(session, project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())

    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    current_pointer = layout.schemas_dir / paper.id / "current.json"
    assert current_pointer.is_file()

    # Revoke membership; the Workspace-owned files intentionally stay behind.
    service.remove_paper(ws.workspace_id, paper.id)
    assert current_pointer.is_file()

    gateway = gateway_for(session, project_tmp_path, ws.workspace_id)
    with pytest.raises(PaperNotMemberError) as exc_info:
        gateway.get_schema_instance(paper.id)
    assert exc_info.value.code == "paper_not_member"
    with pytest.raises(PaperNotMemberError):
        gateway.get_schema_field(paper.id, "research_problem.control_type")


def test_gateway_checks_membership_before_schema_delegation(session, project_tmp_path):
    paper = add_paper(session)
    ws = create_bound_workspace(session, "Gate", "gws-gate")
    recording = RecordingSchema()
    gateway = gateway_for(session, project_tmp_path, ws.workspace_id, schemas=recording)

    with pytest.raises(PaperNotMemberError):
        gateway.get_schema_instance(paper.id)
    with pytest.raises(PaperNotMemberError):
        gateway.get_schema_field(paper.id, "controller_type")
    assert recording.instance_calls == []
    assert recording.field_calls == []


# ---------------------------------------------------------------------------
# AC-007: no-schema Workspace schema access is disabled through the gateway
# ---------------------------------------------------------------------------


def test_no_schema_workspace_gateway_schema_is_disabled(session, project_tmp_path):
    paper = add_paper(session)
    ws = create_none_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)

    gateway = gateway_for(session, project_tmp_path, ws.workspace_id)
    assert gateway.get_paper(paper.id).schema_status == "disabled"
    with pytest.raises(SchemaDisabledError) as instance_error:
        gateway.get_schema_instance(paper.id)
    assert instance_error.value.code == "schema_disabled"
    with pytest.raises(SchemaDisabledError):
        gateway.get_schema_field(paper.id, "research_problem.control_type")
    # No Schema storage boundary is ever materialized for the no-schema WS.
    assert not workspace_layout(ws.workspace_id, data_root=project_tmp_path).derived_dir.exists()