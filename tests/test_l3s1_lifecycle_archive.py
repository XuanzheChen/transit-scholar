"""Layer3 Stage1 Workspace lifecycle tests — archive (T-003 / AC-016).

Proves the archive contract (REQ-009):

- archiving transitions an active Workspace to ``archived`` and advances the
  revision;
- memberships and Workspace-owned Schema/Wiki files are preserved;
- normal active knowledge access through the bound gateway returns an
  explicit ``workspace_not_active`` outcome;
- archive is idempotent (no revision churn) and rejected for
  deleting/deleted Workspaces;
- membership mutations on an archived Workspace are rejected.
"""

from __future__ import annotations

import pytest

from transit_scholar.db.models import Paper, WorkspacePaperMembership
from transit_scholar.layer2.schema import RetrievalResult
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.workspace import (
    WorkspaceKnowledgeGateway,
    WorkspaceNotActiveError,
    WorkspaceService,
)


class FakeEvidence:
    """Deterministic L2S1 delegate seam (recording, offline)."""

    def __init__(self) -> None:
        self.ready: dict[str, bool] = {}
        self.search_calls: list[tuple[str, str]] = []
        self.read_calls: list[tuple[str, list[str]]] = []

    def l2s1_ready(self, paper_id: str) -> bool:
        return self.ready.get(paper_id, False)

    def search(self, paper_id, query, *, top_k=20, filters=None):
        self.search_calls.append((paper_id, query))
        return RetrievalResult(status="ok", method="bm25", hits=[])

    def read_blocks(self, paper_id, block_ids):
        self.read_calls.append((paper_id, list(block_ids)))
        return []


def add_paper_row(session, paper_id: str = "p-archive") -> Paper:
    paper = Paper(id=paper_id, title="Archived Paper", status="active")
    session.add(paper)
    session.flush()
    return paper


def _write_workspace_owned_files(project_tmp_path, workspace_id: str) -> list:
    """Simulate persisted Workspace-owned Schema/Wiki artifacts."""
    layout = workspace_layout(workspace_id, data_root=project_tmp_path)
    files = [
        layout.schemas_dir / "p-archive" / "run-1" / "run.json",
        layout.wiki_dir / "manifest.json",
        layout.derived_dir / "stray-orphan.json",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"artifact": true}\n', encoding="utf-8")
    return files


def test_archive_transitions_active_to_archived_and_advances_revision(session):
    service = WorkspaceService(session)
    workspace = service.create(name="To Archive").workspace
    workspace_id = workspace.workspace_id
    add_paper_row(session)
    service.add_paper(workspace_id, "p-archive")
    revision_before = service.get(workspace_id).revision

    result = service.archive(workspace_id)

    assert result.already_archived is False
    assert result.workspace.status == "archived"
    assert service.get(workspace_id).status == "archived"
    # Archive is an authoritative lifecycle mutation: revision advances.
    assert service.get(workspace_id).revision == revision_before + 1
    # The tombstone/status columns persist through the ORM row.
    from transit_scholar.db.models import Workspace as WorkspaceRow

    row = session.get(WorkspaceRow, workspace_id)
    assert row.status == "archived"


def test_archive_preserves_memberships_and_workspace_owned_files(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = service.create(name="Preserve Me").workspace
    workspace_id = workspace.workspace_id
    add_paper_row(session)
    service.add_paper(workspace_id, "p-archive")
    files = _write_workspace_owned_files(project_tmp_path, workspace_id)

    service.archive(workspace_id)

    # AC-016: memberships are preserved.
    memberships = service.list_memberships(workspace_id)
    assert [m.paper_id for m in memberships] == ["p-archive"]
    rows = (
        session.query(WorkspacePaperMembership)
        .filter_by(workspace_id=workspace_id)
        .all()
    )
    assert len(rows) == 1
    # AC-016: Workspace-owned files are preserved untouched.
    for path in files:
        assert path.is_file(), f"workspace-owned file vanished: {path}"


def test_archive_rejects_normal_active_knowledge_access(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = service.create(name="Locked").workspace
    workspace_id = workspace.workspace_id
    add_paper_row(session)
    service.add_paper(workspace_id, "p-archive")
    service.archive(workspace_id)

    evidence = FakeEvidence()
    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace_id,
        data_root=project_tmp_path,
        evidence=evidence,
    )

    with pytest.raises(WorkspaceNotActiveError) as exc_info:
        gateway.list_papers()
    assert exc_info.value.code == "workspace_not_active"
    with pytest.raises(WorkspaceNotActiveError):
        gateway.get_paper("p-archive")
    with pytest.raises(WorkspaceNotActiveError):
        gateway.search_evidence("p-archive", "controller")
    with pytest.raises(WorkspaceNotActiveError):
        gateway.read_evidence("p-archive", ["b1"])
    with pytest.raises(WorkspaceNotActiveError):
        gateway.get_schema_instance("p-archive")
    with pytest.raises(WorkspaceNotActiveError):
        gateway.search_wiki("controller")

    # The boundary check happens before any lower-layer call.
    assert evidence.search_calls == []
    assert evidence.read_calls == []


def test_archive_is_idempotent_without_revision_churn(session):
    service = WorkspaceService(session)
    workspace = service.create(name="Twice Archived").workspace
    workspace_id = workspace.workspace_id

    first = service.archive(workspace_id)
    revision_after_first = service.get(workspace_id).revision
    second = service.archive(workspace_id)

    assert first.already_archived is False
    assert second.already_archived is True
    assert second.workspace.status == "archived"
    assert service.get(workspace_id).revision == revision_after_first


def test_archive_rejected_for_deleting_and_deleted_workspaces(session):
    service = WorkspaceService(session)
    from transit_scholar.db.models import Workspace as WorkspaceRow

    for status in ("deleting", "deleted"):
        workspace = service.create(name=f"Already {status}").workspace
        row = session.get(WorkspaceRow, workspace.workspace_id)
        row.status = status
        session.flush()
        with pytest.raises(WorkspaceNotActiveError) as exc_info:
            service.archive(workspace.workspace_id)
        assert exc_info.value.code == "workspace_not_active"
        # State untouched by the rejected request.
        assert session.get(WorkspaceRow, workspace.workspace_id).status == status


def test_archive_blocks_membership_mutations_but_keeps_control_plane_reads(session):
    service = WorkspaceService(session)
    workspace = service.create(name="Frozen").workspace
    workspace_id = workspace.workspace_id
    paper = add_paper_row(session, paper_id="p-late")
    service.archive(workspace_id)

    with pytest.raises(WorkspaceNotActiveError) as exc_info:
        service.add_paper(workspace_id, paper.id)
    assert exc_info.value.code == "workspace_not_active"
    with pytest.raises(WorkspaceNotActiveError):
        service.remove_paper(workspace_id, paper.id)

    # Control-plane reads keep working for lifecycle visibility.
    assert service.get(workspace_id).status == "archived"
    assert service.list_memberships(workspace_id) == []