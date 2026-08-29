"""Layer3 Stage1 stale bound-consumer tests (T-005 / REQ-012 / AC-023).

Proves the bind/ground flow: a consumer grounds the Workspace, binds the
gateway to the snapshot's revision, and every later call revalidates the
authoritative state:

- a membership mutation between bind/ground and the next call advances the
  Workspace to N+1 and EVERY subsequent gateway operation returns the explicit
  ``workspace_changed`` outcome before any asset is read — authorization never
  comes from the old snapshot alone (AC-023);
- re-grounding after the mutation restores operations against the new
  revision, while the removed Paper remains inaccessible (AC-015);
- revision changes are per-Workspace: mutating Workspace A never invalidates
  a gateway bound to Workspace B (cross-Workspace independence of REQ-012).
"""

from __future__ import annotations

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema import RetrievalResult
from transit_scholar.layer3.grounding import WorkspaceGroundingService
from transit_scholar.layer3.knowledge import WorkspaceKnowledgeGateway
from transit_scholar.layer3.workspace import (
    PaperNotMemberError,
    WorkspaceChangedError,
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


def add_paper(session, paper_id: str, title: str = "Stale Paper") -> Paper:
    paper = Paper(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def grounding_service(session, project_tmp_path):
    return WorkspaceGroundingService(session, data_root=project_tmp_path)


def gateway_at_snapshot(
    session, project_tmp_path, workspace_id, snapshot, *, evidence=None
):
    return WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace_id,
        expected_revision=snapshot.revision,
        data_root=project_tmp_path,
        evidence=evidence or BoomEvidence(),
    )


class BenignEvidence:
    """Returns normal results; used on the unaffected Workspace's gateway."""

    def l2s1_ready(self, paper_id: str) -> bool:
        return True

    def search(self, paper_id, query, *, top_k=20, filters=None):
        return RetrievalResult(status="ok", method="bm25", hits=[])

    def read_blocks(self, paper_id, block_ids):
        return []


def test_membership_removal_between_bind_and_call_rejects_every_operation(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = service.create(name="Stale Bind").workspace
    workspace_id = workspace.workspace_id
    paper = add_paper(session, "gw-stale-p1")
    service.add_paper(workspace_id, paper.id)

    # Grounded snapshot at revision N (bind/ground flow, AC-023).
    snapshot = grounding_service(session, project_tmp_path).ground(workspace_id)
    assert snapshot.revision == service.get(workspace_id).revision
    assert paper.id in snapshot.member_paper_ids

    gateway = gateway_at_snapshot(session, project_tmp_path, workspace_id, snapshot)
    # Access is authorized while the snapshot is current.
    assert [view.paper_id for view in gateway.list_papers()] == [paper.id]

    # Membership removal advances the Workspace to N+1.
    service.remove_paper(workspace_id, paper.id)
    assert service.get(workspace_id).revision == snapshot.revision + 1

    # Every operation is rejected with the explicit workspace_changed outcome
    # before any Paper/evidence/Schema/Wiki asset is read.
    for operation in (
        lambda: gateway.list_papers(),
        lambda: gateway.get_paper(paper.id),
        lambda: gateway.search_evidence(paper.id, "controller"),
        lambda: gateway.read_evidence(paper.id, ["b1"]),
        lambda: gateway.get_schema_instance(paper.id),
        lambda: gateway.get_schema_field(paper.id, "controller_type"),
        lambda: gateway.search_wiki("controller"),
        lambda: gateway.wiki_status(),
        lambda: gateway.current_state(),
    ):
        with pytest.raises(WorkspaceChangedError) as exc_info:
            operation()
        assert exc_info.value.code == "workspace_changed"
        assert exc_info.value.expected_revision == snapshot.revision
        assert exc_info.value.current_revision == snapshot.revision + 1


def test_regrounding_uses_new_revision_and_removed_paper_stays_inaccessible(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = service.create(name="Re-ground").workspace
    workspace_id = workspace.workspace_id
    paper = add_paper(session, "gw-stale-p2")
    service.add_paper(workspace_id, paper.id)

    grounding = grounding_service(session, project_tmp_path)
    first = grounding.ground(workspace_id)
    service.remove_paper(workspace_id, paper.id)

    # Re-grounding returns the new revision and current membership.
    second = grounding.ground(workspace_id)
    assert second.revision == first.revision + 1
    assert paper.id not in second.member_paper_ids

    # A fresh gateway bound to the new snapshot works, but the removed Paper
    # is inaccessible immediately (AC-015) — re-grounding never resurrects it.
    gateway = gateway_at_snapshot(session, project_tmp_path, workspace_id, second)
    assert gateway.list_papers() == []
    with pytest.raises(PaperNotMemberError) as exc_info:
        gateway.get_paper(paper.id)
    assert exc_info.value.code == "paper_not_member"


def test_cross_workspace_revision_changes_are_independent(session, project_tmp_path):
    """REQ-012 is evaluated per Workspace: A's mutation never invalidates a
    consumer bound to B, and B's revision is untouched."""
    service = WorkspaceService(session)
    ws_a = service.create(name="A", workspace_id="gw-stale-a").workspace
    ws_b = service.create(name="B", workspace_id="gw-stale-b").workspace
    paper_a = add_paper(session, "gw-stale-pa", "Paper A")
    paper_b = add_paper(session, "gw-stale-pb", "Paper B")
    service.add_paper(ws_a.workspace_id, paper_a.id)
    service.add_paper(ws_b.workspace_id, paper_b.id)

    grounding = grounding_service(session, project_tmp_path)
    snapshot_a = grounding.ground(ws_a.workspace_id)
    snapshot_b = grounding.ground(ws_b.workspace_id)

    gateway_a = gateway_at_snapshot(session, project_tmp_path, ws_a.workspace_id, snapshot_a)
    gateway_b = gateway_at_snapshot(
        session, project_tmp_path, ws_b.workspace_id, snapshot_b,
        evidence=BenignEvidence(),
    )
    assert gateway_a.get_paper(paper_a.id).paper_id == paper_a.id
    assert gateway_b.get_paper(paper_b.id).paper_id == paper_b.id

    # Mutate A only: B's revision does not advance.
    service.remove_paper(ws_a.workspace_id, paper_a.id)
    assert service.get(ws_a.workspace_id).revision == snapshot_a.revision + 1
    assert service.get(ws_b.workspace_id).revision == snapshot_b.revision

    # A's stale consumer is rejected; B's consumer keeps working (its own
    # authoritative state is unchanged) and keeps delegating evidence reads.
    with pytest.raises(WorkspaceChangedError):
        gateway_a.get_paper(paper_a.id)
    assert gateway_b.get_paper(paper_b.id).paper_id == paper_b.id
    assert [view.paper_id for view in gateway_b.list_papers()] == [paper_b.id]
    assert gateway_b.search_evidence(paper_b.id, "controller").status == "ok"

    # A's membership change never leaks into B: a live (unbound) B gateway
    # still sees exactly B's membership.
    live_b = WorkspaceKnowledgeGateway(
        session,
        workspace_id=ws_b.workspace_id,
        data_root=project_tmp_path,
        evidence=BenignEvidence(),
    )
    assert [view.paper_id for view in live_b.list_papers()] == [paper_b.id]
    with pytest.raises(PaperNotMemberError):
        live_b.get_paper(paper_a.id)