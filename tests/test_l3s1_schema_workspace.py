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
  without any Workspace identifier;
- REQ-003 / AC-009..AC-010: materialization verifies the CURRENT
  SchemaDefinition triple against the persisted Workspace binding before any
  L2S2 extraction/persistence and writes nothing on mismatch;
- REQ-004 / AC-012..AC-016: read paths validate the persisted run itself
  (readable AND binding-compatible) instead of relying on ``current.json``
  existence, surfacing the stable ``schema_binding_mismatch`` code;
- AC-015: compatible runs keep working through every read surface.

All extraction runs are fully offline (fake LLM provider, offline retrieval).
"""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    SchemaCurrentNotFoundError,
    extract_schema,
    get_schema,
    get_schema_definition,
)
from transit_scholar.layer2.schema_extraction.persistence import (
    RUN_MANIFEST_FILE,
    SCHEMA_INSTANCE_FILE,
)
from transit_scholar.layer3.schema import (
    SchemaBindingMismatchError,
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


def rewrite_pointer(layout, paper_id, changes):
    """Rewrite ``current.json`` with the given field changes (tamper helper)."""
    path = layout.schema_storage().current_path(paper_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def rewrite_run_manifest(layout, paper_id, run_id, changes):
    """Rewrite ``run_manifest.json`` with the given field changes.

    The run manifest is not digest-protected by L2S2 (only instance, manifest
    and report are), so a changed identity stays a READABLE run — exactly the
    AC-013/AC-014 binding-incompatibility case.
    """
    path = layout.schemas_dir / paper_id / "runs" / run_id / RUN_MANIFEST_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def retarget_run_schema_id(layout, paper_id, run_id, new_schema_id):
    """Consistently retarget a run to another schema_id (all files + digests).

    Rewrites instance/manifest/report schema ids and recomputes the recorded
    digests, so L2S2 read-back still succeeds: the run is READABLE but its
    Schema identity is incompatible with the Workspace binding (AC-013).
    """
    run_dir = layout.schemas_dir / paper_id / "runs" / run_id
    digested = (
        SCHEMA_INSTANCE_FILE,
        "extraction_manifest.json",
        "validation_report.json",
    )
    for name in digested:
        path = run_dir / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["schema_id"] = new_schema_id
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads((run_dir / RUN_MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest["schema_id"] = new_schema_id
    manifest["file_digests"] = [
        {
            "path": name,
            "sha256": hashlib.sha256((run_dir / name).read_bytes()).hexdigest(),
            "size": (run_dir / name).stat().st_size,
        }
        for name in digested
    ]
    (run_dir / RUN_MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


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


# ---------------------------------------------------------------------------
# REQ-003: materialization validates the CURRENT SchemaDefinition triple
# ---------------------------------------------------------------------------


def _tampered_definition(*, version="9.9.9", description="tampered content"):
    """A current ``SchemaDefinition`` that differs from the real plugin."""
    return DEFINITION.model_copy(update={"version": version, "description": description})


def test_materialize_rejects_same_id_version_with_changed_hash(
    monkeypatch, session, project_tmp_path
):
    """AC-010: same schema_id/version but a different deterministic content
    hash -> materialize fails BEFORE extraction/persistence with the stable
    ``schema_binding_mismatch`` code and writes nothing."""
    paper = add_paper(session)
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)

    import transit_scholar.layer3.schema.service as schema_service_module

    tampered = _tampered_definition(version=DEFINITION.version)
    assert tampered.schema_id == DEFINITION.schema_id
    assert tampered.version == DEFINITION.version
    from transit_scholar.layer3.workspace import compute_schema_hash

    assert compute_schema_hash(tampered) != compute_schema_hash(DEFINITION)
    monkeypatch.setattr(
        schema_service_module, "get_schema_definition", lambda _schema_id: tampered
    )

    with pytest.raises(SchemaBindingMismatchError) as exc_info:
        schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    assert exc_info.value.code == "schema_binding_mismatch"
    # Nothing was persisted: no current.json, no run files (AC-010).
    assert not layout.derived_dir.exists()


def test_materialize_rejects_changed_schema_version_without_updating_state(
    monkeypatch, session, project_tmp_path
):
    """AC-009: a changed schema_version fails before extraction/persistence
    with the stable code; an existing compatible run and its current pointer
    are left untouched (no new run, no pointer update)."""
    paper = add_paper(session)
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)

    # The exact binding materializes normally first.
    first = schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    pointer_before = layout.schema_storage().read_current(paper.id)

    import transit_scholar.layer3.schema.service as schema_service_module

    tampered = _tampered_definition(version="9.9.9")
    assert tampered.version != DEFINITION.version
    monkeypatch.setattr(
        schema_service_module, "get_schema_definition", lambda _schema_id: tampered
    )

    with pytest.raises(SchemaBindingMismatchError) as exc_info:
        schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    assert exc_info.value.code == "schema_binding_mismatch"
    # No new run was written and current.json was NOT updated (AC-009).
    runs_dir = layout.schemas_dir / paper.id / "runs"
    assert sorted(path.name for path in runs_dir.iterdir()) == [first.run_id]
    assert layout.schema_storage().read_current(paper.id).run_id == pointer_before.run_id


def test_materialize_unresolvable_current_definition_fails_explicitly(
    monkeypatch, session, project_tmp_path
):
    """REQ-003: when the current SchemaDefinition for the bound schema_id
    cannot be resolved at all, materialization fails explicitly."""
    paper = add_paper(session)
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)

    import transit_scholar.layer3.schema.service as schema_service_module

    def missing_definition(schema_id):
        from transit_scholar.layer2.schema_extraction import (
            SchemaPluginNotFoundError,
        )

        raise SchemaPluginNotFoundError(f"schema plugin {schema_id!r} not found")

    monkeypatch.setattr(schema_service_module, "get_schema_definition", missing_definition)
    with pytest.raises(SchemaBindingMismatchError) as exc_info:
        schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    assert exc_info.value.code == "schema_binding_mismatch"


# ---------------------------------------------------------------------------
# REQ-004: read paths validate the persisted run, not current.json existence
# ---------------------------------------------------------------------------


def _materialized_bound_workspace(session, project_tmp_path, *, workspace_id):
    """A bound Workspace with one materialized compatible Schema run."""
    paper = add_paper(session, paper_id="hp1")
    ws = create_bound_workspace(session, workspace_id=workspace_id)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    return paper.id, ws.workspace_id, layout, schema


def test_pointer_identity_mismatch_rejected_with_stable_code(
    session, project_tmp_path
):
    """AC-013 (pointer): a current pointer whose schema_version disagrees with
    the binding makes the run unusable -> explicit ``schema_binding_mismatch``
    on every schema read surface; the pointer no longer yields a current-run
    identity."""
    paper_id, workspace_id, layout, schema = _materialized_bound_workspace(
        session, project_tmp_path, workspace_id="ws-ptrver"
    )
    rewrite_pointer(layout, paper_id, {"schema_version": "0.0-forged"})

    with pytest.raises(SchemaBindingMismatchError) as version_error:
        schema.get_instance(workspace_id, paper_id)
    assert version_error.value.code == "schema_binding_mismatch"
    with pytest.raises(SchemaBindingMismatchError):
        schema.get_field(workspace_id, paper_id, "research_problem.control_type")
    # The incompatible pointer is not a usable current-run identity.
    assert schema.current_run_identities(workspace_id, [paper_id]) == {paper_id: None}


def test_pointer_schema_hash_mismatch_rejected_with_stable_code(
    session, project_tmp_path
):
    """AC-014: the normal L2S2 current pointer exposes schema_hash; a hash
    incompatible with the binding rejects the run explicitly."""
    paper_id, workspace_id, layout, schema = _materialized_bound_workspace(
        session, project_tmp_path, workspace_id="ws-ptrhash"
    )
    rewrite_pointer(layout, paper_id, {"schema_hash": "f" * 64})

    with pytest.raises(SchemaBindingMismatchError) as hash_error:
        schema.get_instance(workspace_id, paper_id)
    assert hash_error.value.code == "schema_binding_mismatch"
    with pytest.raises(SchemaBindingMismatchError):
        schema.get_field(workspace_id, paper_id, "controller_type")
    assert schema.current_run_identities(workspace_id, [paper_id]) == {paper_id: None}


def test_readable_run_with_version_mismatch_rejected_with_stable_code(
    session, project_tmp_path
):
    """AC-013 (persisted run): a READABLE run whose run-manifest schema_version
    disagrees with the binding is rejected explicitly — not merely because the
    pointer happens to agree."""
    paper_id, workspace_id, layout, schema = _materialized_bound_workspace(
        session, project_tmp_path, workspace_id="ws-runver"
    )
    run_id = layout.schema_storage().read_current(paper_id).run_id
    rewrite_run_manifest(layout, paper_id, run_id, {"schema_version": "0.0-forged"})

    with pytest.raises(SchemaBindingMismatchError) as version_error:
        schema.get_instance(workspace_id, paper_id)
    assert version_error.value.code == "schema_binding_mismatch"
    with pytest.raises(SchemaBindingMismatchError):
        schema.get_field(workspace_id, paper_id, "research_problem.control_type")


def test_readable_run_with_schema_id_mismatch_rejected_with_stable_code(
    session, project_tmp_path
):
    """AC-013 (persisted run): a fully READABLE run (all digests valid) whose
    schema_id disagrees with the binding is rejected with the stable code."""
    paper_id, workspace_id, layout, schema = _materialized_bound_workspace(
        session, project_tmp_path, workspace_id="ws-runid"
    )
    run_id = layout.schema_storage().read_current(paper_id).run_id
    retarget_run_schema_id(layout, paper_id, run_id, "foreign_schema")

    with pytest.raises(SchemaBindingMismatchError) as id_error:
        schema.get_instance(workspace_id, paper_id)
    assert id_error.value.code == "schema_binding_mismatch"


def test_readable_run_with_manifest_hash_mismatch_rejected_with_stable_code(
    session, project_tmp_path
):
    """AC-014 (persisted run): the run manifest exposes schema_hash; a hash
    incompatible with the binding rejects the run explicitly."""
    paper_id, workspace_id, layout, schema = _materialized_bound_workspace(
        session, project_tmp_path, workspace_id="ws-runhash"
    )
    run_id = layout.schema_storage().read_current(paper_id).run_id
    rewrite_run_manifest(layout, paper_id, run_id, {"schema_hash": "f" * 64})

    with pytest.raises(SchemaBindingMismatchError) as hash_error:
        schema.get_instance(workspace_id, paper_id)
    assert hash_error.value.code == "schema_binding_mismatch"


def test_pointer_to_missing_run_reports_schema_missing_not_ready(
    session, project_tmp_path
):
    """AC-012: current.json exists but the referenced run was removed ->
    explicit ``schema_missing`` — the pointer alone never makes content
    usable."""
    paper_id, workspace_id, layout, schema = _materialized_bound_workspace(
        session, project_tmp_path, workspace_id="ws-corrupt"
    )
    run_id = layout.schema_storage().read_current(paper_id).run_id
    assert (layout.schemas_dir / paper_id / "current.json").is_file()
    shutil.rmtree(layout.schemas_dir / paper_id / "runs" / run_id)

    with pytest.raises(SchemaMissingError) as missing_error:
        schema.get_instance(workspace_id, paper_id)
    assert missing_error.value.code == "schema_missing"
    with pytest.raises(SchemaMissingError):
        schema.get_field(workspace_id, paper_id, "research_problem.control_type")


# ---------------------------------------------------------------------------
# AC-015: a fully compatible persisted run keeps working everywhere
# ---------------------------------------------------------------------------


def test_compatible_run_supports_every_read_surface(session, project_tmp_path):
    paper = add_paper(session, paper_id="cp1")
    ws = create_bound_workspace(session, workspace_id="ws-ok")
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)

    result = schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())

    # get_instance() / get_field() read the compatible persisted run.
    instance = schema.get_instance(ws.workspace_id, paper.id)
    assert instance.paper_id == paper.id
    assert instance.schema_id == DEFINITION.schema_id
    field = schema.get_field(ws.workspace_id, paper.id, "research_problem.control_type")
    assert field is not None
    # Historical read by explicit run id stays supported.
    historical = schema.get_instance(ws.workspace_id, paper.id, run_id=result.run_id)
    assert historical == instance
    # Current-run identity derivation stays intact.
    identities = schema.current_run_identities(ws.workspace_id, [paper.id])
    assert identities[paper.id]["run_id"] == result.run_id
    # Derivations report ready — no error code.
    readiness = schema.paper_schema_readiness(ws.workspace_id, [paper.id])
    assert readiness[paper.id].status == "ready"
    assert readiness[paper.id].error_code is None