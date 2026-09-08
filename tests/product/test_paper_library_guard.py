from __future__ import annotations

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import Paper, Workspace, WorkspacePaperMembership
from transit_scholar.product import PaperInUseError
from transit_scholar.product import facade
from transit_scholar.product.facade import TransitScholarProduct


def test_library_delete_rejects_active_workspace_membership(session):
    paper = Paper(status="active")
    workspace = Workspace(name="Active", status="active", schema_mode="none")
    session.add_all([paper, workspace])
    session.flush()
    session.add(WorkspacePaperMembership(workspace_id=workspace.id, paper_id=paper.id))
    session.commit()
    product = TransitScholarProduct(session, runtime_factory=None)
    with pytest.raises(PaperInUseError):
        product.soft_delete_library_paper(paper.id)
    assert session.get(Paper, paper.id).status == "active"


def test_library_delete_and_restore_delegate_to_existing_workflows(session, monkeypatch):
    product = TransitScholarProduct(session, runtime_factory=None)
    expected_delete = object()
    expected_restore = object()
    monkeypatch.setattr(facade, "soft_delete_paper", lambda paper_id: expected_delete)
    monkeypatch.setattr(facade, "restore_paper", lambda paper_id: expected_restore)

    assert product.soft_delete_library_paper("unused-paper") is expected_delete
    assert product.restore_library_paper("unused-paper") is expected_restore
