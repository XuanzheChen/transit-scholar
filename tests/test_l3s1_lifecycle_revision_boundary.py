"""Layer3 Stage1 lifecycle tests — stale bound consumers (T-003 / AC-023).

Proves REQ-012: Workspace-bound knowledge operations validate the current
Workspace state on every call.

- a gateway bound to an expected revision N rejects every call after an
  authoritative mutation advances the Workspace to N+1 with the explicit
  ``workspace_changed`` outcome (no asset is read, no lower-layer call is
  made);
- the error carries the stale expected and current authoritative revisions;
- a gateway created WITHOUT an expected revision revalidates authoritative
  membership on every call: a Paper removed after an earlier snapshot is
  immediately inaccessible, and re-grounding (fresh gateway) restores access
  only for the current membership;
- a gateway over a missing Workspace reports ``workspace_not_found``.
"""

from __future__ import annotations

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema import RetrievalResult
from transit_scholar.layer3.workspace import (
    PaperNotMemberError,
    WorkspaceChangedError,
    WorkspaceKnowledgeGateway,
    WorkspaceNotFoundError,
    WorkspaceService,
)


class BoomEvidence:
    """Fails the test if any lower-layer L2S1 call happens."""

    def l2s1_ready(self, paper_id: str) -> bool:
        return True

    def search(self, paper_id, query, *, top_k=20, filters=None):
        raise AssertionError("stale bounds must fail before L2S1 delegation")

    def read_blocks(self, paper_id, block_ids):
        raise AssertionError("stale bounds must fail before L2S1 delegation")


def add_paper(session, paper_id: str = "p-stale") -> Paper:
    paper = Paper(id=paper_id, title="Stale Paper", status="active")
    session.add(paper)
    session.flush()
    return paper


def test_stale_gateway_rejected_after_membership_mutation(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = service.create(name="Stale").workspace
    workspace_id = workspace.workspace_id
    paper = add_paper(session)
    service.add_paper(workspace_id, paper.id)
    revision_at_snapshot = service.get(workspace_id).revision

    # Bound consumer grounded at revision N.
    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace_id,
        expected_revision=revision_at_snapshot,
        data_root=project_tmp_path,
        evidence=BoomEvidence(),
    )
    # Access is authorized while the snapshot is current.
    assert [view.paper_id for view in gateway.list_papers()] == [paper.id]
    assert gateway.current_state().revision == revision_at_snapshot

    # AC-023: a membership mutation advances the Workspace to N+1.
    service.remove_paper(workspace_id, paper.id)
    assert service.get(workspace_id).revision == revision_at_snapshot + 1

    # The stale consumer is rejected on EVERY operation with the explicit
    # Workspace-changed outcome; nothing is read from any asset.
    for operation in (
        lambda: gateway.list_papers(),
        lambda: gateway.get_paper(paper.id),
        lambda: gateway.search_evidence(paper.id, "controller"),
        lambda: gateway.read_evidence(paper.id, ["b1"]),
        lambda: gateway.get_schema_instance(paper.id),
        lambda: gateway.get_schema_field(paper.id, "controller_type"),
        lambda: gateway.search_wiki("controller"),
        lambda: gateway.current_state(),
    ):
        with pytest.raises(WorkspaceChangedError) as exc_info:
            operation()
        assert exc_info.value.code == "workspace_changed"
        assert exc_info.value.expected_revision == revision_at_snapshot
        assert exc_info.value.current_revision == revision_at_snapshot + 1


def test_stale_gateway_rejected_after_archive(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = service.create(name="Stale Archive").workspace
    workspace_id = workspace.workspace_id
    revision_at_snapshot = service.get(workspace_id).revision

    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace_id,
        expected_revision=revision_at_snapshot,
        data_root=project_tmp_path,
        evidence=BoomEvidence(),
    )
    assert gateway.current_state().status == "active"

    service.archive(workspace_id)
    with pytest.raises(WorkspaceChangedError) as exc_info:
        gateway.list_papers()
    assert exc_info.value.code == "workspace_changed"
    assert exc_info.value.current_revision == revision_at_snapshot + 1


def test_gateway_without_expected_revision_revalidates_per_call(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = service.create(name="Live Revalidate").workspace
    workspace_id = workspace.workspace_id
    paper = add_paper(session)
    service.add_paper(workspace_id, paper.id)

    # No expected revision: the gateway revalidates against the authoritative
    # DB on every call (the ``revalidate`` alternative of REQ-012).
    gateway = WorkspaceKnowledgeGateway(
        session, workspace_id=workspace_id, data_root=project_tmp_path,
        evidence=BoomEvidence(),
    )
    assert gateway.get_paper(paper.id).paper_id == paper.id

    service.remove_paper(workspace_id, paper.id)
    with pytest.raises(PaperNotMemberError) as exc_info:
        gateway.get_paper(paper.id)
    assert exc_info.value.code == "paper_not_member"
    assert gateway.list_papers() == []

    # Re-grounding after the Paper rejoins: fresh snapshot authorizes again.
    service.add_paper(workspace_id, paper.id)
    assert [view.paper_id for view in gateway.list_papers()] == [paper.id]


def test_gateway_bound_to_removed_paper_never_authorizes_old_membership(
    session, project_tmp_path
):
    """AC-023: authorization never comes from the old snapshot alone."""
    service = WorkspaceService(session)
    workspace = service.create(name="Revoke").workspace
    workspace_id = workspace.workspace_id
    paper = add_paper(session)
    service.add_paper(workspace_id, paper.id)
    revision_at_snapshot = service.get(workspace_id).revision

    stale = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace_id,
        expected_revision=revision_at_snapshot,
        data_root=project_tmp_path,
        evidence=BoomEvidence(),
    )
    live = WorkspaceKnowledgeGateway(
        session, workspace_id=workspace_id, data_root=project_tmp_path,
        evidence=BoomEvidence(),
    )

    service.remove_paper(workspace_id, paper.id)

    # The stale gateway must not present the Paper as a current member.
    with pytest.raises(WorkspaceChangedError):
        stale.list_papers()
    # The live gateway validates the current membership: Paper gone.
    with pytest.raises(PaperNotMemberError):
        live.get_paper(paper.id)


def test_gateway_for_missing_workspace_reports_not_found(session, project_tmp_path):
    gateway = WorkspaceKnowledgeGateway(
        session, workspace_id="0" * 32, data_root=project_tmp_path
    )
    with pytest.raises(WorkspaceNotFoundError) as exc_info:
        gateway.list_papers()
    assert exc_info.value.code == "workspace_not_found"


def test_gateway_expected_revision_mismatch_reports_changed_not_membership(
    session, project_tmp_path
):
    """The stale-revision outcome dominates the membership check for a bound
    consumer (membership may have changed together with the revision)."""
    service = WorkspaceService(session)
    workspace = service.create(name="Ordered Checks").workspace
    workspace_id = workspace.workspace_id
    paper = add_paper(session)
    service.add_paper(workspace_id, paper.id)
    revision_at_snapshot = service.get(workspace_id).revision
    service.remove_paper(workspace_id, paper.id)

    stale = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace_id,
        expected_revision=revision_at_snapshot,
        data_root=project_tmp_path,
        evidence=BoomEvidence(),
    )
    with pytest.raises(WorkspaceChangedError) as exc_info:
        stale.get_paper(paper.id)
    assert exc_info.value.code == "workspace_changed"