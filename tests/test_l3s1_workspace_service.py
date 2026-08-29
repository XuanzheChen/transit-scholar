"""Layer3 Stage1 Workspace service tests (T-001).

Proves the authoritative control-plane contract:

- AC-001: create/read persistence with full state, reconstructible after
  restart (fresh session over committed rows);
- AC-002: one global Paper can join two Workspaces with two independent
  membership rows and exactly one Paper identity;
- AC-003: duplicate add is idempotent — no duplicate membership, no revision
  churn;
- AC-004: bound Workspaces persist schema_id/schema_version/schema_hash
  matching the SchemaDefinition; none Workspaces persist no schema fields;
- AC-005: schema binding mutation is rejected and persisted state is
  untouched;
- revision advances on authoritative mutations only.
"""

from __future__ import annotations

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema_extraction.hashing import (
    compute_schema_hash as l2s2_compute_schema_hash,
)
from transit_scholar.layer2.schema_extraction.models import (
    FieldDefinition,
    SchemaDefinition,
    SectionDefinition,
)
from transit_scholar.layer3.workspace import (
    InvalidWorkspaceInputError,
    PaperNotFoundError,
    PaperNotMemberError,
    SchemaBindingImmutableError,
    WorkspaceNotFoundError,
    WorkspaceNotActiveError,
    WorkspaceService,
    compute_schema_hash,
)


def make_schema_definition(schema_id: str = "bus-control-rl", version: str = "v1"):
    """A minimal but structurally valid SchemaDefinition for tests."""
    return SchemaDefinition(
        schema_id=schema_id,
        version=version,
        name="Bus Control RL",
        sections=[
            SectionDefinition(
                id="sec1",
                label="Control Design",
                fields=[
                    FieldDefinition(
                        id="controller_type",
                        label="Controller Type",
                        question="Which controller type is used?",
                        type="string",
                    ),
                    FieldDefinition(
                        id="env",
                        label="Environment",
                        question="What environment was used?",
                        type="string",
                        options=None,
                    ),
                ],
            )
        ],
    )


def add_paper_row(session, title: str = "Shared") -> Paper:
    paper = Paper(title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


# ---------------------------------------------------------------------------
# AC-001: create/read persistence + restart reconstruction
# ---------------------------------------------------------------------------


def test_create_none_mode_workspace_persists_full_state(session):
    service = WorkspaceService(session)
    result = service.create(name="Research Workspace")
    record = result.workspace

    assert record.workspace_id
    assert len(record.workspace_id) == 32
    assert record.name == "Research Workspace"
    assert record.status == "active"
    assert record.schema_mode == "none"
    assert record.schema_binding is None
    assert record.revision == 1
    assert record.created_at is not None
    assert record.updated_at is not None

    read_back = service.get(record.workspace_id)
    assert read_back == record


def test_create_bound_mode_workspace_persists_hash_matching_definition(session):
    definition = make_schema_definition()
    service = WorkspaceService(session)
    record = service.create(name="Bound Workspace", schema_definition=definition).workspace

    assert record.schema_mode == "bound"
    assert record.schema_binding is not None
    assert record.schema_binding.schema_id == definition.schema_id
    assert record.schema_binding.schema_version == definition.version
    expected_hash = l2s2_compute_schema_hash(definition)
    assert record.schema_binding.schema_hash == expected_hash
    # The Layer3-level helper exposes the same canonical hash.
    assert compute_schema_hash(definition) == expected_hash


def test_workspace_restart_read_back_reconstructs_same_state():
    """AC-001: committed rows read back identically from a fresh session."""
    definition = make_schema_definition()
    first = SessionLocal()
    try:
        created = WorkspaceService(first).create(
            name="Restart Workspace", schema_definition=definition
        )
        first.commit()
        workspace_id = created.workspace.workspace_id
        revision = created.workspace.revision
    finally:
        first.close()

    second = SessionLocal()
    try:
        fresh = WorkspaceService(second).get(workspace_id)
        assert fresh.workspace_id == workspace_id
        assert fresh.name == "Restart Workspace"
        assert fresh.status == "active"
        assert fresh.schema_mode == "bound"
        assert fresh.schema_binding is not None
        assert fresh.schema_binding.schema_id == definition.schema_id
        assert fresh.schema_binding.schema_hash == l2s2_compute_schema_hash(definition)
        assert fresh.revision == revision
        assert fresh.created_at is not None and fresh.updated_at is not None
    finally:
        second.close()


# ---------------------------------------------------------------------------
# AC-002: one Paper in two Workspaces, no Paper duplication
# ---------------------------------------------------------------------------


def test_one_paper_can_belong_to_two_workspaces_without_duplication(session):
    service = WorkspaceService(session)
    paper = add_paper_row(session, title="Single Global Paper")

    workspace_a = service.create(name="Workspace A").workspace
    workspace_b = service.create(name="Workspace B").workspace

    result_a = service.add_paper(workspace_a.workspace_id, paper.id)
    result_b = service.add_paper(workspace_b.workspace_id, paper.id)

    assert result_a.already_member is False
    assert result_b.already_member is False
    assert result_a.membership.paper_id == paper.id
    assert result_b.membership.paper_id == paper.id
    assert result_a.membership.workspace_id == workspace_a.workspace_id
    assert result_b.membership.workspace_id == workspace_b.workspace_id

    memberships = service.list_memberships(workspace_a.workspace_id)
    assert [m.paper_id for m in memberships] == [paper.id]
    assert len(service.list_memberships(workspace_b.workspace_id)) == 1

    # Exactly one global Paper row exists; membership never duplicates it.
    remaining = session.query(Paper).filter_by(id=paper.id).all()
    assert len(remaining) == 1
    # AC-002: one set of global assets — the Paper row keeps its identity.
    session.refresh(paper)
    assert paper.title == "Single Global Paper"


# ---------------------------------------------------------------------------
# AC-003: duplicate membership is idempotent (chosen service contract)
# ---------------------------------------------------------------------------


def test_duplicate_add_is_idempotent(session):
    service = WorkspaceService(session)
    paper = add_paper_row(session)
    workspace = service.create(name="Idempotent").workspace
    workspace_id = workspace.workspace_id

    first = service.add_paper(workspace_id, paper.id)
    revision_after_add = first.workspace.revision
    second = service.add_paper(workspace_id, paper.id)

    assert second.already_member is True
    assert second.membership.paper_id == paper.id
    assert len(service.list_memberships(workspace_id)) == 1
    # A no-op add is not an authoritative mutation: revision is unchanged.
    assert second.workspace.revision == revision_after_add

    # Exactly one membership row exists for the pair after repeated adds.
    from transit_scholar.db.models import WorkspacePaperMembership

    session.flush()
    rows = (
        session.query(WorkspacePaperMembership)
        .filter_by(workspace_id=workspace_id, paper_id=paper.id)
        .all()
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# AC-004: bound/none persistence invariants
# ---------------------------------------------------------------------------


def test_bound_and_none_invariants_after_persist(session):
    service = WorkspaceService(session)
    bound = service.create(
        name="Bound", schema_definition=make_schema_definition("schema-x", "v2")
    ).workspace
    none_ws = service.create(name="None").workspace

    # Read the raw persisted rows.
    from transit_scholar.db.models import Workspace as WorkspaceRow

    bound_row = session.get(WorkspaceRow, bound.workspace_id)
    none_row = session.get(WorkspaceRow, none_ws.workspace_id)

    assert bound_row.schema_id == "schema-x"
    assert bound_row.schema_version == "v2"
    assert bound_row.schema_hash == l2s2_compute_schema_hash(
        make_schema_definition("schema-x", "v2")
    )

    assert none_row.schema_id is None
    assert none_row.schema_version is None
    assert none_row.schema_hash is None

    # Service-level read of the binding agrees.
    assert service.schema_binding(bound.workspace_id) is not None
    assert service.schema_binding(none_ws.workspace_id) is None


def test_bound_mode_requires_valid_definition(session):
    service = WorkspaceService(session)
    with pytest.raises(InvalidWorkspaceInputError):
        service.create(name="Bad Bound", schema_definition=object())  # type: ignore[arg-type]


def test_create_validates_name(session):
    service = WorkspaceService(session)
    with pytest.raises(InvalidWorkspaceInputError):
        service.create(name="   ")


# ---------------------------------------------------------------------------
# AC-005: schema binding mutation is rejected without persisted mutation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda service, ws_id: service.rebind_schema(ws_id, schema_mode="none"),
        lambda service, ws_id: service.rebind_schema(
            ws_id, schema_definition=make_schema_definition("other-schema", "v9")
        ),
        lambda service, ws_id: service.rebind_schema(
            ws_id, schema_mode="bound", schema_definition=make_schema_definition()
        ),
    ],
)
def test_schema_binding_mutation_rejected_and_state_untouched(session, call):
    service = WorkspaceService(session)
    none_ws = service.create(name="Immutable None").workspace
    bound_ws = service.create(
        name="Immutable Bound", schema_definition=make_schema_definition()
    ).workspace

    for workspace_id in (none_ws.workspace_id, bound_ws.workspace_id):
        before = service.get(workspace_id)
        with pytest.raises(SchemaBindingImmutableError) as exc_info:
            call(service, workspace_id)
        assert exc_info.value.code == "schema_binding_immutable"

        # Reload from the database: nothing was mutated.
        after = service.get(workspace_id)
        assert after == before
        assert after.revision == before.revision


def test_rebind_on_missing_workspace_reports_not_found_first(session):
    service = WorkspaceService(session)
    with pytest.raises(WorkspaceNotFoundError):
        service.rebind_schema("0" * 32)


# ---------------------------------------------------------------------------
# membership removal + revision semantics (REQ-009 visibility contract)
# ---------------------------------------------------------------------------


def test_remove_paper_revokes_membership(session):
    service = WorkspaceService(session)
    paper = add_paper_row(session)
    workspace = service.create(name="Removal").workspace
    workspace_id = workspace.workspace_id

    service.add_paper(workspace_id, paper.id)
    revision_after_add = service.get(workspace_id).revision
    removed = service.remove_paper(workspace_id, paper.id)

    assert removed.paper_id == paper.id
    assert removed.workspace.revision == revision_after_add + 1
    assert service.list_memberships(workspace_id) == []

    # The membership row is gone from the database.
    assert service.get(workspace_id).revision == revision_after_add + 1
    # The global Paper survives membership removal untouched.
    assert session.get(Paper, paper.id).title == "Shared"


def test_remove_non_member_raises_paper_not_member(session):
    service = WorkspaceService(session)
    paper = add_paper_row(session)
    workspace = service.create(name="No Member").workspace
    with pytest.raises(PaperNotMemberError) as exc_info:
        service.remove_paper(workspace.workspace_id, paper.id)
    assert exc_info.value.code == "paper_not_member"


def test_revision_advances_only_on_authoritative_mutations(session):
    service = WorkspaceService(session)
    paper = add_paper_row(session)
    workspace = service.create(name="Revisions").workspace
    workspace_id = workspace.workspace_id

    assert service.get(workspace_id).revision == 1
    service.add_paper(workspace_id, paper.id)
    assert service.get(workspace_id).revision == 2
    service.add_paper(workspace_id, paper.id)  # idempotent no-op
    assert service.get(workspace_id).revision == 2
    service.remove_paper(workspace_id, paper.id)
    assert service.get(workspace_id).revision == 3


def test_list_memberships_is_deterministically_ordered(session):
    service = WorkspaceService(session)
    paper_ids = []
    for index in range(3):
        paper = add_paper_row(session, title=f"Paper {index}")
        paper_ids.append(paper.id)
    workspace = service.create(name="Ordered").workspace
    for paper_id in reversed(paper_ids):
        service.add_paper(workspace.workspace_id, paper_id)

    listed = [m.paper_id for m in service.list_memberships(workspace.workspace_id)]
    assert listed == sorted(paper_ids)


# ---------------------------------------------------------------------------
# error codes + lifecycle guards
# ---------------------------------------------------------------------------


def test_workspace_not_found_error_code(session):
    service = WorkspaceService(session)
    for operation in (
        lambda: service.get("0" * 32),
        lambda: service.add_paper("0" * 32, "1" * 32),
        lambda: service.remove_paper("0" * 32, "1" * 32),
        lambda: service.list_memberships("0" * 32),
    ):
        with pytest.raises(WorkspaceNotFoundError) as exc_info:
            operation()
        assert exc_info.value.code == "workspace_not_found"


def test_add_missing_paper_rejected(session):
    service = WorkspaceService(session)
    workspace = service.create(name="No Paper").workspace
    with pytest.raises(PaperNotFoundError) as exc_info:
        service.add_paper(workspace.workspace_id, "f" * 32)
    assert exc_info.value.code == "paper_not_found"
    assert service.list_memberships(workspace.workspace_id) == []


def test_membership_mutation_on_non_active_workspace_rejected(session):
    service = WorkspaceService(session)
    paper = add_paper_row(session)
    workspace = service.create(name="Archived Soon").workspace
    from transit_scholar.db.models import Workspace as WorkspaceRow

    row = session.get(WorkspaceRow, workspace.workspace_id)
    row.status = "archived"
    session.flush()

    with pytest.raises(WorkspaceNotActiveError) as exc_info:
        service.add_paper(workspace.workspace_id, paper.id)
    assert exc_info.value.code == "workspace_not_active"

    # Reads still work for a non-active Workspace (control plane visibility).
    assert service.get(workspace.workspace_id).status == "archived"


# ---------------------------------------------------------------------------
# AC-024: Layer1/L2 public APIs remain independently usable
# ---------------------------------------------------------------------------


def test_layer2_l1_public_api_signatures_gain_no_workspace_param():
    """AC-024: L2S1 public APIs must not require a workspace_id parameter."""
    import inspect as py_inspect

    from transit_scholar.layer2.retrieval.api import read_blocks, search_bm25

    for function in (search_bm25, read_blocks):
        assert "workspace_id" not in py_inspect.signature(function).parameters


def test_papers_and_l2_apis_work_without_any_workspace(session):
    """AC-024: global Paper persistence works with no Layer3 involvement."""
    paper = Paper(title="Independent Paper", status="active")
    session.add(paper)
    session.flush()
    fetched = session.get(Paper, paper.id)
    assert fetched.title == "Independent Paper"
    assert fetched.status == "active"
