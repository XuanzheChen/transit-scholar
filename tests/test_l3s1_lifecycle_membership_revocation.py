"""Layer3 Stage1 lifecycle tests — membership governance (T-003).

Proves:

- AC-014: adding a valid global Paper to an active Workspace succeeds even
  when the Paper lacks L2S1 retrieval readiness and Workspace Schema content;
  the bound gateway exposes the missing/unavailable derived state instead of
  rejecting the membership;
- AC-015: after removal, all Workspace-bound access paths (Paper, evidence
  search/read, Schema, Wiki) stop exposing the Paper even while orphaned
  Workspace-owned files still exist — visibility is revoked by the
  authoritative control-plane state, not by file cleanup;
- AC-018: a non-member Paper's evidence call never reaches the lower-layer
  L2S1 operation.
"""

from __future__ import annotations

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema import RetrievalResult
from transit_scholar.layer2.schema_extraction import get_schema_definition
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.workspace import (
    L2S1EvidenceDelegate,
    PaperNotMemberError,
    WorkspaceKnowledgeGateway,
    WorkspaceService,
)
from transit_scholar.layer3.wiki import (
    WikiEmptyMembershipError,
    WikiUnsupportedError,
)

DEFINITION = get_schema_definition("bus_control_rl")


class FakeEvidence:
    """Recording L2S1 seam; proves access decisions happen before delegation."""

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


class BoomEvidence:
    """A delegate that fails the test if the lower layer is ever reached."""

    def l2s1_ready(self, paper_id: str) -> bool:
        return True

    def search(self, paper_id, query, *, top_k=20, filters=None):
        raise AssertionError(
            f"L2S1 search must not be called for non-member {paper_id!r} (AC-018)"
        )

    def read_blocks(self, paper_id, block_ids):
        raise AssertionError(
            f"L2S1 read must not be called for non-member {paper_id!r} (AC-018)"
        )


def add_unprocessed_paper(session, paper_id: str, title: str | None = None) -> Paper:
    """A valid global Paper with NO L2S1/Schema/Wiki derived assets at all."""
    paper = Paper(
        id=paper_id,
        title=title or f"Unprocessed {paper_id}",
        status="active",
    )
    session.add(paper)
    session.flush()
    return paper


# ---------------------------------------------------------------------------
# AC-014: membership succeeds for unready Papers; derived state is observable
# ---------------------------------------------------------------------------


def test_add_unprocessed_paper_succeeds_and_exposes_missing_derived_state(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    bound = service.create(
        name="Bound WS", schema_definition=DEFINITION
    ).workspace
    none_ws = service.create(name="None WS").workspace
    paper = add_unprocessed_paper(session, "p-unprocessed")

    # Membership must succeed without ANY derived readiness.
    added_bound = service.add_paper(bound.workspace_id, paper.id)
    added_none = service.add_paper(none_ws.workspace_id, paper.id)
    assert added_bound.already_member is False
    assert added_none.already_member is False

    evidence = FakeEvidence()
    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=bound.workspace_id,
        data_root=project_tmp_path,
        evidence=evidence,
    )

    view = gateway.get_paper(paper.id)
    # AC-014: the membership is established; derived status is missing, not an
    # error — Grounding-style exposure of the unavailable derived state.
    assert view.paper_id == paper.id
    assert view.title == "Unprocessed p-unprocessed"
    assert view.l2s1_ready is False
    assert view.schema_status == "missing"
    assert [listed.paper_id for listed in gateway.list_papers()] == [paper.id]

    none_gateway = WorkspaceKnowledgeGateway(
        session, workspace_id=none_ws.workspace_id, data_root=project_tmp_path
    )
    assert none_gateway.get_paper(paper.id).schema_status == "disabled"


def test_l2s1_ready_derives_true_when_global_assets_exist(
    session, project_tmp_path, l2_config
):
    service = WorkspaceService(session)
    workspace = service.create(name="Ready").workspace
    paper = add_unprocessed_paper(session, "p-ready")

    # Simulate a global L2S1 canonical run pointer + retrieval index.
    from transit_scholar.layer2.paths import (
        retrieval_index_dir,
        run_paths,
        save_current,
    )

    save_current(l2_config.parsed_paper_dir(paper.id), "run-1")
    run_paths(l2_config, paper.id, "run-1").run_dir.mkdir(parents=True, exist_ok=True)
    retrieval_index_dir(l2_config, paper.id).mkdir(parents=True, exist_ok=True)

    service.add_paper(workspace.workspace_id, paper.id)
    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace.workspace_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )
    assert gateway.get_paper(paper.id).l2s1_ready is True


# ---------------------------------------------------------------------------
# AC-015: removal revokes visibility independent of orphaned files
# ---------------------------------------------------------------------------


def test_removed_paper_inaccessible_even_with_orphan_files(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = service.create(
        name="Orphans", schema_definition=DEFINITION
    ).workspace
    workspace_id = workspace.workspace_id
    paper = add_unprocessed_paper(session, "p-orphan")
    service.add_paper(workspace_id, paper.id)

    # Simulate orphaned Workspace-owned derived files that cleanup has NOT
    # (and must not need to) remove for access revocation.
    layout = workspace_layout(workspace_id, data_root=project_tmp_path)
    orphan_paths = [
        layout.schemas_dir / "p-orphan" / "current.json",
        layout.schemas_dir / "p-orphan" / "run-orphan" / "run.json",
        layout.wiki_dir / "manifest.json",
        layout.derived_dir / "orphan-marker.json",
    ]
    for path in orphan_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"orphan": true}\n', encoding="utf-8")

    service.remove_paper(workspace_id, paper.id)
    assert layout.derived_dir.is_dir()
    for path in orphan_paths:
        assert path.is_file(), f"orphan file missing: {path}"

    evidence = FakeEvidence()
    gateway = WorkspaceKnowledgeGateway(
        session, workspace_id=workspace_id, data_root=project_tmp_path,
        evidence=evidence,
    )

    # Paper listing: the removed Paper is not a current member.
    assert gateway.list_papers() == []

    # Paper read: explicit membership error.
    with pytest.raises(PaperNotMemberError) as exc_info:
        gateway.get_paper(paper.id)
    assert exc_info.value.code == "paper_not_member"

    # Evidence search/read: explicit membership error BEFORE the L2S1 layer.
    with pytest.raises(PaperNotMemberError):
        gateway.search_evidence(paper.id, "controller type")
    with pytest.raises(PaperNotMemberError):
        gateway.read_evidence(paper.id, ["b1"])
    assert evidence.search_calls == []
    assert evidence.read_calls == []

    # Schema-derived access: membership gate fires first.
    with pytest.raises(PaperNotMemberError):
        gateway.get_schema_instance(paper.id)
    with pytest.raises(PaperNotMemberError):
        gateway.get_schema_field(paper.id, "controller_type")

    # Wiki-derived access: the removed Paper is no longer a Wiki input and the
    # bound Workspace now has no member Papers -> explicit empty-membership
    # outcome (a stale/foreign Wiki is never substituted).
    with pytest.raises(WikiEmptyMembershipError) as wiki_exc:
        gateway.search_wiki("controller")
    assert wiki_exc.value.code == "empty_membership"


def test_removed_paper_evidence_never_reaches_lower_layer(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = service.create(name="Boom").workspace
    workspace_id = workspace.workspace_id
    paper = add_unprocessed_paper(session, "p-boom")
    service.add_paper(workspace_id, paper.id)
    service.remove_paper(workspace_id, paper.id)

    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=workspace_id,
        data_root=project_tmp_path,
        evidence=BoomEvidence(),
    )
    with pytest.raises(PaperNotMemberError):
        gateway.search_evidence(paper.id, "controller")
    with pytest.raises(PaperNotMemberError):
        gateway.read_evidence(paper.id, ["b1"])


def test_wiki_access_for_no_schema_workspace_is_unsupported_not_fallback(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = service.create(name="No Schema Wiki").workspace
    paper = add_unprocessed_paper(session, "p-wiki-none")
    service.add_paper(workspace.workspace_id, paper.id)

    gateway = WorkspaceKnowledgeGateway(
        session, workspace_id=workspace.workspace_id, data_root=project_tmp_path
    )
    assert gateway.wiki_status().status == "unsupported"
    with pytest.raises(WikiUnsupportedError) as exc_info:
        gateway.search_wiki("controller")
    assert exc_info.value.code == "wiki_unsupported"