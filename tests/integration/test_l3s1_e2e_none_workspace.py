"""Layer3 Stage1 end-to-end regression — no-schema Workspace (T-006).

Runs the complete Layer3 Stage1 flow for a ``none``-mode Workspace through
the REAL lower-layer machinery:

- creation persists ``none`` mode with the schema fields absent (REQ-003 /
  AC-004) and the control-plane row survives an independent-session read
  (AC-001);
- membership of a global Paper with real L2S1 assets AND one without any
  derived assets succeeds regardless of readiness (AC-014);
- Grounding reports the no-schema semantics: per-Paper Schema status
  ``disabled``, Schema coverage ``disabled`` and Base Wiki ``unsupported``
  (AC-007 / AC-009 / REQ-005), with the capability summary reflecting that
  no Schema/Wiki operation is available;
- a second Grounding pass is side-effect-free (AC-013): no Workspace Schema or
  Wiki storage is materialized and no Schema/Wiki read falls back to global or
  foreign content;
- the bound gateway keeps the Workspace-safe L2S1 evidence path working for
  member Papers (REQ-010), while Schema reads report ``schema_disabled``
  (AC-007) and Wiki reads report ``wiki_unsupported`` (AC-009);
- Workspace deletion removes the memberships and leaves the global Paper and
  its L2S1 assets intact (AC-017 / C-009) and the L2S1 APIs directly usable
  (AC-024).
"""

from __future__ import annotations

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import Paper as PaperRow
from transit_scholar.db.models import Workspace as WorkspaceRow
from transit_scholar.db.models import WorkspacePaperMembership as MembershipRow
from transit_scholar.layer2 import build_retrieval
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from transit_scholar.layer2.retrieval.api import search_bm25 as l2_search_bm25
from transit_scholar.layer3.grounding import WorkspaceGroundingService
from transit_scholar.layer3.knowledge import L2S1EvidenceDelegate
from transit_scholar.layer3.schema import SchemaDisabledError, WorkspaceSchemaService
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.wiki import WikiUnsupportedError, WorkspaceWikiService
from transit_scholar.layer3.workspace import (
    PaperNotMemberError,
    WorkspaceKnowledgeGateway,
    WorkspaceService,
)
from tests.l2s1_fixtures import make_ready_paper, patch_parsers

EVIDENCE_ITEMS = [
    make_item(
        item_id="p1", item_type="paragraph",
        text="Reinforcement learning trains the holding controller.",
        order=0, page=1, bbox=[70, 100, 530, 120],
    ),
    make_item(
        item_id="p2", item_type="paragraph",
        text="Bus headway regularity is measured by waiting time.",
        order=1, page=1, bbox=[70, 140, 530, 160],
    ),
]


def build_ready_paper(project_tmp_path, monkeypatch, l2_config, *, title: str):
    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path, title=title)
    patch_parsers(
        monkeypatch, [FakeParserAdapter(items=EVIDENCE_ITEMS, page_count=1)]
    )
    from transit_scholar.layer2.pipeline import parse_paper

    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    build_retrieval(paper_id, config=l2_config)
    return paper_id


def test_e2e_none_workspace_full_flow(session, project_tmp_path, monkeypatch, l2_config):
    # Real L2S1-ready global Paper, committed before the fixture session
    # performs any statement (the L2S1 suite convention).
    paper_ready = build_ready_paper(
        project_tmp_path, monkeypatch, l2_config, title="None Flow Ready Paper"
    )

    service = WorkspaceService(session)

    # AC-004: no-schema Workspace persists none mode with no binding fields.
    workspace = service.create(
        name="None E2E", workspace_id="e2e-none-ws"
    ).workspace
    assert workspace.schema_mode == "none"
    assert workspace.schema_binding is None
    assert workspace.status == "active"
    assert workspace.revision == 1
    ws_id = workspace.workspace_id

    # AC-014: membership succeeds regardless of the Paper's derived readiness.
    paper_bare = PaperRow(id="e2e-none-bare", title="Bare Paper", status="active")
    session.add(paper_bare)
    session.flush()
    service.add_paper(ws_id, paper_ready)
    service.add_paper(ws_id, paper_bare.id)
    assert service.get(ws_id).revision == 3

    # AC-012: Grounding reports the CURRENT state, including no-schema shape.
    grounder = WorkspaceGroundingService(
        session, data_root=project_tmp_path, evidence=L2S1EvidenceDelegate(config=l2_config)
    )
    snapshot = grounder.ground(ws_id)
    assert snapshot.schema_mode == "none"
    assert snapshot.schema_binding is None
    assert snapshot.member_paper_ids == sorted([paper_ready, paper_bare.id])
    assert snapshot.schema_coverage.model_dump() == {
        "workspace_id": ws_id,
        "total": 2,
        "ready": 0,
        "missing": 0,
        "status": "disabled",
    }
    by_id = {paper.paper_id: paper for paper in snapshot.visible_papers}
    assert by_id[paper_ready].l2s1_ready is True
    assert by_id[paper_ready].schema_status == "disabled"
    assert by_id[paper_bare.id].l2s1_ready is False
    assert by_id[paper_bare.id].schema_status == "disabled"

    # AC-009/REQ-005: Base Wiki capability is unsupported for none mode.
    assert snapshot.base_wiki.status == "unsupported"
    caps = snapshot.capabilities
    assert caps.knowledge_access is True
    assert caps.paper_access is True
    assert caps.evidence_access is True
    assert caps.schema_read is False
    assert caps.schema_materialization is False
    assert caps.wiki_build is False
    assert caps.wiki_read is False
    assert snapshot.recommended_actions == []

    # AC-007: Workspace Schema reads report schema_disabled; there is no
    # fallback to a global or foreign SchemaInstance (no materialization).
    gateway = WorkspaceKnowledgeGateway(
        session,
        workspace_id=ws_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )
    with pytest.raises(SchemaDisabledError) as exc_info:
        gateway.get_schema_instance(paper_ready)
    assert exc_info.value.code == "schema_disabled"
    with pytest.raises(SchemaDisabledError):
        gateway.get_schema_field(paper_ready, "research_problem.control_type")
    # A materialization attempt is rejected too, and never creates storage.
    from transit_scholar.layer2.schema_extraction import FakeLLMProvider

    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    with pytest.raises(SchemaDisabledError):
        schemas.materialize(ws_id, paper_bare.id, llm_client=FakeLLMProvider())

    # AC-009: Wiki build/read report unsupported, never a fabricated Wiki.
    wiki = WorkspaceWikiService(session, data_root=project_tmp_path)
    assert wiki.capability(ws_id).build_supported is False
    with pytest.raises(WikiUnsupportedError) as exc_info:
        gateway.search_wiki("controller")
    assert exc_info.value.code == "wiki_unsupported"

    # REQ-010: the member-Paper L2S1 evidence path stays usable through the
    # Workspace-bound gateway (Workspace-safe even without a Schema).
    evidence = gateway.search_evidence(paper_ready, "reinforcement")
    assert evidence.status == "ok"
    assert len(evidence.hits) > 0

    # AC-018: non-members stay rejected before any lower-layer call.
    with pytest.raises(PaperNotMemberError):
        gateway.search_evidence(paper_bare.id + "x", "reinforcement")
    with pytest.raises(PaperNotMemberError):
        gateway.get_paper(paper_bare.id + "x")

    # AC-013: a second Grounding pass mutates nothing — no Schema/Wiki storage
    # exists in this Workspace's boundary even after materialization attempts.
    layout = workspace_layout(ws_id, data_root=project_tmp_path)
    assert not layout.schemas_dir.exists()
    assert not layout.wiki_dir.exists()
    snapshot_again = grounder.ground(ws_id)
    assert snapshot_again.model_dump() == snapshot.model_dump()
    assert not layout.derived_dir.exists()


def test_e2e_none_workspace_delete_preserves_global_assets(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-001/AC-017 with committed sessions: restart-style read reconstructs
    the none-mode Workspace; delete leaves the global Paper and L2S1 assets
    usable through the direct L2S1 public API (AC-024 / C-009)."""
    paper_id = build_ready_paper(
        project_tmp_path, monkeypatch, l2_config, title="None Delete Survivor"
    )

    first = SessionLocal()
    workspace_id = None
    try:
        service = WorkspaceService(first)
        workspace = service.create(name="None Delete E2E").workspace
        workspace_id = workspace.workspace_id
        service.add_paper(workspace_id, paper_id)
        first.commit()

        # AC-001: independent-session read reconstructs the control plane.
        second = SessionLocal()
        try:
            row = second.get(WorkspaceRow, workspace_id)
            assert row.status == "active"
            assert row.schema_mode == "none"
            assert row.schema_id is None
            assert row.schema_version is None
            assert row.schema_hash is None
        finally:
            second.close()

        result = service.delete(workspace_id, data_root=project_tmp_path)
        assert result.workspace.status == "deleted"
        assert first.query(MembershipRow).filter_by(
            workspace_id=workspace_id
        ).count() == 0
        assert not workspace_layout(
            workspace_id, data_root=project_tmp_path
        ).derived_dir.exists()

        # C-009: the global Paper record and L2S1 assets survive.
        assert first.get(PaperRow, paper_id).title == "None Delete Survivor"
        direct = l2_search_bm25(paper_id, "reinforcement", config=l2_config)
        assert direct.status == "ok"
    finally:
        first.close()
        cleanup = SessionLocal()
        try:
            if workspace_id is not None:
                cleanup.execute(
                    MembershipRow.__table__.delete().where(
                        MembershipRow.workspace_id == workspace_id
                    )
                )
                cleanup.execute(
                    WorkspaceRow.__table__.delete().where(
                        WorkspaceRow.id == workspace_id
                    )
                )
            cleanup.commit()
        finally:
            cleanup.close()