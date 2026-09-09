from __future__ import annotations

import pytest
from sqlalchemy import text

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
    def delete(paper_id, *, session_factory, data_root):
        assert data_root == product.data_root
        assert session_factory is product.session_factory
        return expected_delete

    def restore(paper_id, *, session_factory, data_root):
        assert data_root == product.data_root
        assert session_factory is product.session_factory
        return expected_restore

    monkeypatch.setattr(facade, "soft_delete_paper", delete)
    monkeypatch.setattr(facade, "restore_paper", restore)

    assert product.soft_delete_library_paper("unused-paper") is expected_delete
    assert product.restore_library_paper("unused-paper") is expected_restore


def test_layer1_factory_creates_owned_sessions_without_closing_product(session):
    product = TransitScholarProduct(session, runtime_factory=None)
    with product.session_factory() as first, product.session_factory() as second:
        assert first is not second
        assert first is not session
        assert first.get_bind() is session.get_bind()
    assert product.session is session
    assert session.execute(text("SELECT 1")).scalar_one() == 1
