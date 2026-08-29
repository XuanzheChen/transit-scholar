"""Layer3 Stage1 Workspace-owned Schema governance tests (T-002).

Proves:

- AC-006: the same Paper processed under the same SchemaDefinition in two
  Workspaces persists Schema runs/current pointers under different
  Workspace-specific roots; deleting one Workspace's Schema storage leaves the
  other untouched;
- AC-007: no-schema Workspace Schema reads/materialization report
  disabled and never fall back to global/foreign instances;
- AC-024: materialization/reads reuse the existing L2S2 Package D public API
  through injected storage roots, and the L2S2 API stays independently usable
  without any Workspace identifier.

All extraction runs are fully offline (fake LLM provider, offline retrieval).
"""

from __future__ import annotations

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    SchemaCurrentNotFoundError,
    extract_schema,
    get_schema,
    get_schema_definition,
)
from transit_scholar.layer3.schema import (
    SchemaDisabledError,
    SchemaMissingError,
    WorkspaceSchemaService,
)
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.workspace import (
    InvalidWorkspaceInputError,
    PaperNotMemberError,
    WorkspaceService,
)

DEFINITION = get_schema_definition("bus_control_rl")


def add_paper(session, paper_id="p1", title="Shared Paper") -> Paper:
    paper = Paper(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def create_bound_workspace(session, name="Bound", workspace_id="ws-1"):
    return WorkspaceService(session).create(
        name=name, schema_definition=DEFINITION, workspace_id=workspace_id
    ).workspace


def create_none_workspace(session, name="No Schema", workspace_id="ws-none"):
    return WorkspaceService(session).create(name=name, workspace_id=workspace_id).workspace


# ---------------------------------------------------------------------------
# AC-006: identical Paper+Schema in two Workspaces -> isolated Schema storage
# ---------------------------------------------------------------------------


def test_identical_paper_schema_in_two_workspaces_is_isolated(session, project_tmp_path):
    paper = add_paper(session)
    ws_a = create_bound_workspace(session, "Workspace A", workspace_id="ws-aa")
    ws_b = create_bound_workspace(session, "Workspace B", workspace_id="ws-bb")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    result_a = schema.materialize(
        ws_a.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    result_b = schema.materialize(
        ws_b.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )

    layout_a = workspace_layout(ws_a.workspace_id, data_root=project_tmp_path)
    layout_b = workspace_layout(ws_b.workspace_id, data_root=project_tmp_path)
    # Distinct Workspace-specific Schema roots for the same Paper+Schema.
    assert layout_a.schemas_dir != layout_b.schemas_dir
    assert (layout_a.schemas_dir / paper.id / "current.json").is_file()
    assert (layout_b.schemas_dir / paper.id / "current.json").is_file()
    assert result_a.run_id != result_b.run_id

    # Both Workspaces can read their own current instance.
    instance_a = schema.get_instance(ws_a.workspace_id, paper.id)
    instance_b = schema.get_instance(ws_b.workspace_id, paper.id)
    assert instance_a.paper_id == paper.id == instance_b.paper_id


def test_deleting_one_workspace_schema_storage_does_not_alter_other(
    session, project_tmp_path
):
    paper = add_paper(session)
    ws_a = create_bound_workspace(session, "Workspace A", workspace_id="ws-aa")
    ws_b = create_bound_workspace(session, "Workspace B", workspace_id="ws-bb")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws_a.workspace_id, paper.id, llm_client=FakeLLMProvider())
    schema.materialize(ws_b.workspace_id, paper.id, llm_client=FakeLLMProvider())

    layout_a = workspace_layout(ws_a.workspace_id, data_root=project_tmp_path)
    layout_b = workspace_layout(ws_b.workspace_id, data_root=project_tmp_path)
    layout_a.delete_schema_storage()

    # Workspace A: schema content is now missing (pointer deleted).
    with pytest.raises(SchemaMissingError) as missing_a:
        schema.get_instance(ws_a.workspace_id, paper.id)
    assert missing_a.value.code == "schema_missing"
    # Workspace B: fully intact.
    instance_b = schema.get_instance(ws_b.workspace_id, paper.id)
    assert instance_b.paper_id == paper.id
    assert (layout_b.schemas_dir / paper.id / "current.json").is_file()
    current_b = layout_b.schema_storage().read_current(paper.id)
    assert current_b.paper_id == paper.id


# ---------------------------------------------------------------------------
# AC-007: no-schema Workspace Schema access is disabled, never a fallback
# ---------------------------------------------------------------------------


def test_no_schema_workspace_schema_access_is_disabled(session, project_tmp_path):
    paper = add_paper(session)
    ws = create_none_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    assert schema.is_schema_mode_bound(ws.workspace_id) is False

    with pytest.raises(SchemaDisabledError) as storage_error:
        schema.schema_storage(ws.workspace_id)
    assert storage_error.value.code == "schema_disabled"

    with pytest.raises(SchemaDisabledError) as read_error:
        schema.get_instance(ws.workspace_id, paper.id)
    assert read_error.value.code == "schema_disabled"

    with pytest.raises(SchemaDisabledError) as field_error:
        schema.get_field(ws.workspace_id, paper.id, "research_problem.control_type")
    assert field_error.value.code == "schema_disabled"

    with pytest.raises(SchemaDisabledError) as materialize_error:
        schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    assert materialize_error.value.code == "schema_disabled"

    with pytest.raises(SchemaDisabledError) as identities_error:
        schema.current_run_identities(ws.workspace_id)
    assert identities_error.value.code == "schema_disabled"

    # No Schema storage boundary is ever materialized for the no-schema Workspace.
    assert not layout.derived_dir.exists()


def test_bound_workspace_missing_schema_reports_missing_not_fallback(
    session, project_tmp_path
):
    paper = add_paper(session)
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    with pytest.raises(SchemaMissingError) as missing:
        schema.get_instance(ws.workspace_id, paper.id)
    assert missing.value.code == "schema_missing"
    with pytest.raises(SchemaMissingError) as missing_field:
        schema.get_field(ws.workspace_id, paper.id, "research_problem.control_type")
    assert missing_field.value.code == "schema_missing"
    # The read never falls back to a global/foreign storage root.
    assert not (workspace_layout(ws.workspace_id, data_root=project_tmp_path).schemas_dir / paper.id).exists()


# ---------------------------------------------------------------------------
# membership and injection governance
# ---------------------------------------------------------------------------


def test_materialize_requires_current_member_paper(session, project_tmp_path):
    paper = add_paper(session)
    ws = create_bound_workspace(session)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)

    with pytest.raises(PaperNotMemberError) as non_member:
        schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    assert non_member.value.code == "paper_not_member"
    # No run files were written for the non-member Paper.
    assert not (workspace_layout(ws.workspace_id, data_root=project_tmp_path).derived_dir).exists()


def test_materialize_rejects_storage_redirection_injection(session, project_tmp_path):
    paper = add_paper(session)
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)

    with pytest.raises(InvalidWorkspaceInputError):
        schema.materialize(
            ws.workspace_id, paper.id, storage_root=project_tmp_path
        )
    with pytest.raises(InvalidWorkspaceInputError):
        schema.materialize(
            ws.workspace_id,
            paper.id,
            storage=workspace_layout(ws.workspace_id, data_root=project_tmp_path)
            .schema_storage(),
        )


def test_materialize_writes_only_workspace_root_and_reads_match(
    session, project_tmp_path
):
    paper = add_paper(session)
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)

    result = schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())

    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    storage = layout.schema_storage()
    pointer = storage.read_current(paper.id)
    assert pointer.run_id == result.run_id
    stored = storage.read_run(paper.id, result.run_id)
    assert stored.instance.schema_id == "bus_control_rl"

    # read APIs agree with the persisted Workspace-owned run.
    instance = schema.get_instance(ws.workspace_id, paper.id)
    assert instance.schema_id == "bus_control_rl"
    identities = schema.current_run_identities(ws.workspace_id)
    assert identities[paper.id]["run_id"] == result.run_id


# ---------------------------------------------------------------------------
# AC-024: the L2S2 public API remains usable without Layer3
# ---------------------------------------------------------------------------


def test_l2s2_public_api_usable_independently_without_workspace_id(
    project_tmp_path,
):
    paper_id = "standalone_paper"
    result = extract_schema(
        paper_id, "bus_control_rl", storage_root=project_tmp_path / "plain",
        llm_client=FakeLLMProvider(),
    )
    assert result.paper_id == paper_id
    instance = get_schema(
        paper_id, "bus_control_rl", storage_root=project_tmp_path / "plain"
    )
    assert instance.paper_id == paper_id
    # The API accepts no workspace identifier at all: signature stays global.
    import inspect

    assert "workspace_id" not in inspect.signature(extract_schema).parameters


def test_raw_l2s2_missing_pointer_is_explicit_and_distinct(project_tmp_path):
    """Raw L2S2 storage reports missing explicitly; Layer3 maps it to the
    stable ``schema_missing`` code instead of swallowing it."""
    storage = workspace_layout("ws-x", data_root=project_tmp_path).schema_storage()
    with pytest.raises(SchemaCurrentNotFoundError):
        storage.read_current("unknown_paper")
    assert SchemaMissingError.code == "schema_missing"