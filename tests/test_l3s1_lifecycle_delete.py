"""Layer3 Stage1 Workspace lifecycle tests — delete (T-003 / AC-017).

Proves the delete contract (REQ-009 / C-009):

- deletion is two-phase: the Workspace first becomes non-accessible in the
  ``deleting`` state (revision advanced) BEFORE destructive cleanup, with
  memberships already removed and the bound gateway rejecting access;
- the ``deleting`` transition and membership revocation are committed
  durably before destructive cleanup starts: an INDEPENDENT database session
  observes the non-accessible state inside the cleanup hook (regression for
  the flush-only ordering), and a cleanup failure leaves the durable
  ``deleting`` state resumable with normal access permanently denied;
- it settles in the ``deleted`` tombstone state with the revision advanced,
  also durably committed;
- Workspace-owned Schema/Wiki storage is removed while global Paper records
  and L2S1 assets remain intact;
- delete is idempotent for an already-deleted Workspace and completes an
  interrupted (``deleting``) deletion;
- a missing Workspace reports ``workspace_not_found``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete as sqlalchemy_delete

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    Paper,
    Workspace as WorkspaceRow,
    WorkspacePaperMembership,
)
from transit_scholar.layer2.schema import RetrievalResult
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.workspace import (
    WorkspaceKnowledgeGateway,
    WorkspaceNotActiveError,
    WorkspaceNotFoundError,
    WorkspaceService,
)


class FakeEvidence:
    """Recording L2S1 seam; the deletion flow must never reach it."""

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str]] = []
        self.read_calls: list[tuple[str, list[str]]] = []

    def l2s1_ready(self, paper_id: str) -> bool:
        return False

    def search(self, paper_id, query, *, top_k=20, filters=None):
        self.search_calls.append((paper_id, query))
        return RetrievalResult(status="ok", method="bm25", hits=[])

    def read_blocks(self, paper_id, block_ids):
        self.read_calls.append((paper_id, list(block_ids)))
        return []


def create_workspace_with_member(session, name: str, paper_id: str | None = None):
    """Create a Workspace with one member Paper.

    ``paper_id`` defaults to a fresh uuid: ``delete()`` commits durably, so
    fixed paper ids would collide across tests in the shared migrated DB.
    """
    service = WorkspaceService(session)
    workspace = service.create(name=name).workspace
    paper = Paper(
        id=paper_id or uuid.uuid4().hex, title="Doomed Paper", status="active"
    )
    session.add(paper)
    session.flush()
    service.add_paper(workspace.workspace_id, paper.id)
    return service, workspace.workspace_id, paper


def _write_workspace_owned_files(project_tmp_path, workspace_id: str) -> list:
    layout = workspace_layout(workspace_id, data_root=project_tmp_path)
    files = [
        layout.schemas_dir / "p-del" / "run-1" / "run.json",
        layout.schemas_dir / "p-del" / "current.json",
        layout.wiki_dir / "manifest.json",
        layout.wiki_dir / "pages.jsonl",
        layout.derived_dir / "stray-orphan.json",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"artifact": true}\n', encoding="utf-8")
    return files


def _write_global_l2s1_assets(config, paper_id: str) -> list:
    """Simulate global L2S1 canonical/retrieval assets (C-009 survivors)."""
    from transit_scholar.layer2.paths import (
        retrieval_index_dir,
        retrieval_manifest_path,
        run_paths,
        save_current,
    )

    parsed_dir = config.parsed_paper_dir(paper_id)
    save_current(parsed_dir, "run-global-1")
    run = run_paths(config, paper_id, "run-global-1")
    run.run_dir.mkdir(parents=True, exist_ok=True)
    (run.run_dir / "document.json").write_text('{"doc": true}\n', encoding="utf-8")
    (run.run_dir / "blocks.jsonl").write_text("", encoding="utf-8")
    index_dir = retrieval_index_dir(config, paper_id)
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "store.json").write_text('{"store": "local"}\n', encoding="utf-8")
    retrieval_manifest_path(config, paper_id).write_text(
        '{"format_version": "1.0"}\n', encoding="utf-8"
    )
    return [parsed_dir, index_dir]


def test_delete_is_two_phase_non_accessible_before_cleanup(session, project_tmp_path):
    service, workspace_id, _ = create_workspace_with_member(
        session, name="Two Phase"
    )
    revision_before = service.get(workspace_id).revision
    observed: dict = {}

    def cleanup(workspace_id, layout):
        # Called AFTER access revocation + membership removal, BEFORE the
        # deleted tombstone: the Workspace must be observably non-accessible.
        observed["status"] = service.get(workspace_id).status
        observed["memberships"] = [
            m.paper_id for m in service.list_memberships(workspace_id)
        ]
        observed["membership_rows"] = (
            session.query(WorkspacePaperMembership)
            .filter_by(workspace_id=workspace_id)
            .count()
        )
        evidence = FakeEvidence()
        gateway = WorkspaceKnowledgeGateway(
            session, workspace_id=workspace_id, evidence=evidence
        )
        with pytest.raises(WorkspaceNotActiveError) as exc_info:
            gateway.list_papers()
        observed["gateway_code"] = exc_info.value.code
        observed["evidence_calls"] = len(evidence.search_calls)
        layout.delete()  # the destructive cleanup itself

    result = service.delete(
        workspace_id, data_root=project_tmp_path, file_cleanup=cleanup
    )

    assert observed["status"] == "deleting"
    assert observed["memberships"] == []
    assert observed["membership_rows"] == 0
    assert observed["gateway_code"] == "workspace_not_active"
    assert observed["evidence_calls"] == 0
    assert result.already_deleted is False
    assert result.workspace.status == "deleted"
    assert service.get(workspace_id).status == "deleted"
    # Both lifecycle transitions (deleting, deleted) are authoritative.
    assert service.get(workspace_id).revision == revision_before + 2


def test_delete_removes_workspace_owned_storage_keeps_global_assets(
    session, project_tmp_path, l2_config
):
    service, workspace_id, paper = create_workspace_with_member(
        session, name="Global Keep"
    )
    owned_files = _write_workspace_owned_files(project_tmp_path, workspace_id)
    global_roots = _write_global_l2s1_assets(l2_config, paper.id)

    result = service.delete(workspace_id, data_root=project_tmp_path)

    # AC-017: Workspace-owned Schema/Wiki storage is gone.
    layout = workspace_layout(workspace_id, data_root=project_tmp_path)
    assert not layout.derived_dir.exists()
    for path in owned_files:
        assert not path.exists(), f"workspace-owned file survived delete: {path}"
    for root in global_roots:
        assert root.is_dir()
    assert (global_roots[1] / "store.json").is_file()
    # C-009: memberships removed, global Paper record intact.
    assert (
        session.query(WorkspacePaperMembership)
        .filter_by(workspace_id=workspace_id)
        .count()
        == 0
    )
    survivor = session.get(Paper, paper.id)
    assert survivor is not None
    assert survivor.title == "Doomed Paper"
    assert result.workspace.status == "deleted"
    assert result.already_deleted is False


def test_delete_is_idempotent_and_interrupted_delete_resumes(session, project_tmp_path):
    service = WorkspaceService(session)
    from transit_scholar.db.models import Workspace as WorkspaceRow

    # Fresh delete, then a second delete on the tombstone.
    workspace = service.create(name="Tombstone").workspace
    workspace_id = workspace.workspace_id
    first = service.delete(workspace_id, data_root=project_tmp_path)
    revision_after = service.get(workspace_id).revision
    second = service.delete(workspace_id, data_root=project_tmp_path)
    assert first.workspace.status == "deleted"
    assert second.already_deleted is True
    assert second.workspace.status == "deleted"
    assert service.get(workspace_id).revision == revision_after

    # An interrupted delete (persisted in ``deleting``) completes the same
    # two-phase sequence instead of erroring.
    interrupted = service.create(name="Interrupted").workspace
    interrupted_id = interrupted.workspace_id
    row = session.get(WorkspaceRow, interrupted_id)
    row.status = "deleting"
    session.flush()
    revision_before = session.get(WorkspaceRow, interrupted_id).revision
    resumed = service.delete(interrupted_id, data_root=project_tmp_path)
    assert resumed.already_deleted is False
    assert resumed.workspace.status == "deleted"
    assert session.get(WorkspaceRow, interrupted_id).revision == revision_before + 1


def test_delete_archived_workspace_also_removes_memberships_and_storage(
    session, project_tmp_path
):
    service, workspace_id, _ = create_workspace_with_member(
        session, name="Archived Delete"
    )
    service.archive(workspace_id)
    _write_workspace_owned_files(project_tmp_path, workspace_id)

    result = service.delete(workspace_id, data_root=project_tmp_path)

    assert result.workspace.status == "deleted"
    assert service.list_memberships(workspace_id) == []
    assert not workspace_layout(
        workspace_id, data_root=project_tmp_path
    ).derived_dir.exists()


def test_delete_missing_workspace_reports_not_found(session, project_tmp_path):
    service = WorkspaceService(session)
    with pytest.raises(WorkspaceNotFoundError) as exc_info:
        service.delete("0" * 32, data_root=project_tmp_path)
    assert exc_info.value.code == "workspace_not_found"


# ---------------------------------------------------------------------------
# Durable revocation boundary before destructive cleanup (REQ-009 / AC-017)
# ---------------------------------------------------------------------------
#
# Regression for the supervisor finding on the flush-only ordering: the
# ``deleting`` transition and the membership revocation must be COMMITTED
# durably before the file-cleanup hook runs, so an independent database
# session can rely on the revocation and a crash/rollback after destructive
# work can never resurrect an active/visible Workspace whose files were
# already removed.


def _tidy_committed_rows(*, workspace_id: str | None = None, paper_id: str | None = None):
    """Remove rows committed by these tests from the shared migrated DB."""
    session = SessionLocal()
    try:
        if workspace_id is not None:
            session.execute(
                sqlalchemy_delete(WorkspacePaperMembership).where(
                    WorkspacePaperMembership.workspace_id == workspace_id
                )
            )
            session.execute(
                sqlalchemy_delete(WorkspaceRow).where(WorkspaceRow.id == workspace_id)
            )
        if paper_id is not None:
            session.execute(sqlalchemy_delete(Paper).where(Paper.id == paper_id))
        session.commit()
    finally:
        session.close()


def test_delete_revocation_committed_before_cleanup_seen_by_independent_session(
    project_tmp_path,
):
    """AC-017 regression: an independent DB session (own connection and
    transaction, simulating another process) MUST observe the ``deleting``
    status and zero memberships INSIDE the cleanup hook — i.e. the revocation
    is durably committed before destructive work starts, not merely flushed
    inside the deleting caller's transaction."""
    first = SessionLocal()
    workspace_id = None
    paper_id = "p-durable-revoke"
    try:
        service = WorkspaceService(first)
        workspace = service.create(name="Durable Revoke").workspace
        workspace_id = workspace.workspace_id
        first.add(Paper(id=paper_id, title="Durable Paper", status="active"))
        first.flush()
        service.add_paper(workspace_id, paper_id)
        revision_at_deleting = service.get(workspace_id).revision + 1

        observed: dict = {}

        def cleanup(workspace_id, layout):
            # Fully independent session on its own connection: can only see
            # the revocation if it was durably committed before this hook ran.
            observer = SessionLocal()
            try:
                row = observer.get(WorkspaceRow, workspace_id)
                observed["status"] = row.status
                observed["revision"] = row.revision
                observed["membership_rows"] = (
                    observer.query(WorkspacePaperMembership)
                    .filter_by(workspace_id=workspace_id)
                    .count()
                )
            finally:
                observer.close()
            layout.delete()  # the destructive cleanup itself

        result = service.delete(
            workspace_id, data_root=project_tmp_path, file_cleanup=cleanup
        )

        assert observed["status"] == "deleting"
        assert observed["revision"] == revision_at_deleting
        assert observed["membership_rows"] == 0
        assert result.workspace.status == "deleted"

        # The tombstone is durable too: an independent session sees the
        # completed deletion, an idempotent second delete, and the global
        # Paper surviving (C-009).
        later = SessionLocal()
        try:
            row = later.get(WorkspaceRow, workspace_id)
            assert row.status == "deleted"
            assert (
                later.query(WorkspacePaperMembership)
                .filter_by(workspace_id=workspace_id)
                .count()
                == 0
            )
            again = WorkspaceService(later).delete(
                workspace_id, data_root=project_tmp_path
            )
            assert again.already_deleted is True
            assert later.get(Paper, paper_id) is not None
        finally:
            later.close()
    finally:
        first.close()
        _tidy_committed_rows(workspace_id=workspace_id, paper_id=paper_id)


def test_delete_cleanup_failure_leaves_durable_deleting_state_resumable_and_denied(
    project_tmp_path,
):
    """AC-017 regression: if destructive cleanup fails after the durable
    boundary, an independent session (restart) sees the Workspace durably in
    ``deleting`` with no memberships; normal Workspace access stays denied —
    and a resumed delete completes the sequence, removes Workspace-owned
    storage, and leaves the global Paper intact."""
    first = SessionLocal()
    workspace_id = None
    paper_id = "p-durable-fail"
    try:
        service = WorkspaceService(first)
        workspace = service.create(name="Durable Failure").workspace
        workspace_id = workspace.workspace_id
        first.add(Paper(id=paper_id, title="Survivor Paper", status="active"))
        first.flush()
        service.add_paper(workspace_id, paper_id)
        revision_after_revocation = service.get(workspace_id).revision + 1
        owned_files = _write_workspace_owned_files(project_tmp_path, workspace_id)

        def exploding_cleanup(workspace_id, layout):
            # Destructive work starts (post-durable-boundary) and fails.
            raise RuntimeError("simulated partial cleanup failure")

        with pytest.raises(RuntimeError, match="simulated partial cleanup"):
            service.delete(
                workspace_id,
                data_root=project_tmp_path,
                file_cleanup=exploding_cleanup,
            )

        # Restart: an independent session reads the DURABLE deleting state.
        restarted = SessionLocal()
        try:
            row = restarted.get(WorkspaceRow, workspace_id)
            assert row.status == "deleting"
            assert row.revision == revision_after_revocation
            assert (
                restarted.query(WorkspacePaperMembership)
                .filter_by(workspace_id=workspace_id)
                .count()
                == 0
            )
            # Normal access cannot return after destructive work started, even
            # though the orphaned Workspace-owned files still exist.
            for path in owned_files:
                assert path.is_file(), f"orphan file missing: {path}"
            evidence = FakeEvidence()
            gateway = WorkspaceKnowledgeGateway(
                restarted,
                workspace_id=workspace_id,
                data_root=project_tmp_path,
                evidence=evidence,
            )
            with pytest.raises(WorkspaceNotActiveError) as exc_info:
                gateway.list_papers()
            assert exc_info.value.code == "workspace_not_active"
            with pytest.raises(WorkspaceNotActiveError):
                gateway.get_paper(paper_id)
            assert len(evidence.search_calls) == 0
            assert len(evidence.read_calls) == 0

            # The durable state is resumable: a fresh delete finishes the job.
            resumed = WorkspaceService(restarted).delete(
                workspace_id, data_root=project_tmp_path
            )
            assert resumed.already_deleted is False
            assert resumed.workspace.status == "deleted"
            row = restarted.get(WorkspaceRow, workspace_id)
            assert row.revision == revision_after_revocation + 1
            assert not workspace_layout(
                workspace_id, data_root=project_tmp_path
            ).derived_dir.exists()
            for path in owned_files:
                assert not path.exists(), f"workspace-owned file survived: {path}"
            # C-009: the global Paper record survives delete and resume.
            assert restarted.get(Paper, paper_id).title == "Survivor Paper"
            # And access stays denied on the tombstone.
            with pytest.raises(WorkspaceNotActiveError):
                WorkspaceKnowledgeGateway(
                    restarted, workspace_id=workspace_id, data_root=project_tmp_path
                ).list_papers()
        finally:
            restarted.close()
    finally:
        first.close()
        _tidy_committed_rows(workspace_id=workspace_id, paper_id=paper_id)