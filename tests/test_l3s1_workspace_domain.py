"""Layer3 Stage1 Workspace domain persistence tests (T-001).

Covers the ORM layer itself: migration-created tables, bound-vs-none CHECK
invariants (REQ-003), unique membership pairs (REQ-002), and the constraint
that the global Paper model gains no workspace ownership column (AC-024).
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from transit_scholar.db.engine import engine as _engine
from transit_scholar.db.models import (
    Paper,
    Workspace,
    WorkspacePaperMembership,
    WORKSPACE_SCHEMA_MODES,
    WORKSPACE_STATUSES,
)


def test_workspace_tables_exist():
    """The migration creates both Workspace control-plane tables."""
    tables = set(inspect(_engine).get_table_names())
    assert "workspaces" in tables
    assert "workspace_paper_memberships" in tables


def test_workspace_columns_and_indexes():
    """Workspace persists the REQ-001/REQ-003 column set with indexes."""
    inspector = inspect(_engine)
    columns = {column["name"] for column in inspector.get_columns("workspaces")}
    for required in (
        "id",
        "name",
        "status",
        "schema_mode",
        "schema_id",
        "schema_version",
        "schema_hash",
        "revision",
        "created_at",
        "updated_at",
    ):
        assert required in columns, f"missing workspaces column: {required}"
    indexes = {index["name"] for index in inspector.get_indexes("workspaces")}
    assert "ix_workspaces_status" in indexes
    assert "ix_workspaces_schema_mode" in indexes


def test_membership_columns_and_unique_pair_constraint():
    """Membership persists workspace_id/paper_id with a unique pair."""
    inspector = inspect(_engine)
    columns = {column["name"] for column in inspector.get_columns("workspace_paper_memberships")}
    for required in ("id", "workspace_id", "paper_id", "created_at"):
        assert required in columns, f"missing membership column: {required}"
    unique = {
        tuple(sorted(constraint["column_names"]))
        for constraint in inspector.get_unique_constraints("workspace_paper_memberships")
    }
    assert ("paper_id", "workspace_id") in unique


def test_global_paper_model_has_no_workspace_ownership_column():
    """AC-024: Layer3 MUST NOT add workspace_id to the global Paper model."""
    columns = {column.name for column in Paper.__table__.columns}
    assert "workspace_id" not in columns
    assert "workspace_paper_memberships" not in Paper.__table__.name


def test_workspace_check_violation_invalid_status(session):
    """Lifecycle status outside the REQ-001 vocabulary is rejected by the DB."""
    workspace = Workspace(
        id="w" * 32,
        name="Bad Status",
        status="frozen",
        schema_mode="none",
        revision=1,
    )
    session.add(workspace)
    with pytest.raises(IntegrityError):
        session.flush()


def test_workspace_check_violation_invalid_schema_mode(session):
    """Schema mode outside {bound, none} is rejected by the DB."""
    workspace = Workspace(
        id="w" * 32,
        name="Bad Mode",
        status="active",
        schema_mode="hybrid",
        revision=1,
    )
    session.add(workspace)
    with pytest.raises(IntegrityError):
        session.flush()


def test_workspace_check_violation_bound_without_binding(session):
    """REQ-003 invariant: bound mode without the schema triple is rejected."""
    workspace = Workspace(
        id="w" * 32,
        name="Incomplete Bound",
        status="active",
        schema_mode="bound",
        schema_id=None,
        schema_version=None,
        schema_hash=None,
        revision=1,
    )
    session.add(workspace)
    with pytest.raises(IntegrityError):
        session.flush()


def test_workspace_check_violation_none_with_binding(session):
    """REQ-003 invariant: none mode with schema fields present is rejected."""
    workspace = Workspace(
        id="w" * 32,
        name="None With Binding",
        status="active",
        schema_mode="none",
        schema_id="schema-a",
        schema_version="v1",
        schema_hash="a" * 64,
        revision=1,
    )
    session.add(workspace)
    with pytest.raises(IntegrityError):
        session.flush()


def test_workspace_check_violation_zero_revision(session):
    """The monotonic revision marker must start at >= 1."""
    workspace = Workspace(
        id="w" * 32,
        name="Zero Revision",
        status="active",
        schema_mode="none",
        revision=0,
    )
    session.add(workspace)
    with pytest.raises(IntegrityError):
        session.flush()


def test_duplicate_membership_pair_rejected_at_db(session):
    """REQ-002/AC-003: the unique (workspace_id, paper_id) pair is enforced."""
    workspace = Workspace(
        id="w" * 32, name="Membership Constraint", schema_mode="none", revision=1
    )
    paper = Paper(title="Shared Paper")
    session.add_all([workspace, paper])
    session.flush()
    session.add_all(
        [
            WorkspacePaperMembership(workspace_id=workspace.id, paper_id=paper.id),
            WorkspacePaperMembership(workspace_id=workspace.id, paper_id=paper.id),
        ]
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_valid_bound_and_none_rows_persist(session):
    """Both valid schema-mode shapes pass the CHECK constraints."""
    bound = Workspace(
        id="b" * 32,
        name="Bound OK",
        schema_mode="bound",
        schema_id="schema-a",
        schema_version="v1",
        schema_hash="b" * 64,
        revision=1,
    )
    none_ws = Workspace(
        id="n" * 32, name="None OK", schema_mode="none", revision=1
    )
    session.add_all([bound, none_ws])
    session.flush()
    assert bound.schema_id == "schema-a"
    assert none_ws.schema_id is None and none_ws.schema_hash is None


def test_membership_navigates_to_paper_without_duplicating(session):
    """Membership points at the single global Paper row."""
    workspace = Workspace(id="w" * 32, name="Nav", schema_mode="none", revision=1)
    paper = Paper(title="Navigated")
    session.add_all([workspace, paper])
    session.flush()
    membership = WorkspacePaperMembership(
        workspace_id=workspace.id, paper_id=paper.id
    )
    session.add(membership)
    session.flush()
    assert membership.paper.id == paper.id
    assert membership.workspace.id == workspace.id
    assert len(workspace.memberships) == 1


def test_status_and_mode_vocabularies_are_stable():
    """Constants match the DB CHECK constraint wording (single source)."""
    assert WORKSPACE_STATUSES == ("active", "archived", "deleting", "deleted")
    assert WORKSPACE_SCHEMA_MODES == ("bound", "none")