"""Layer3 Stage1 end-to-end regression — bound-schema Workspace (T-006).

This is the integration-level counterpart of the T-001..T-005 unit suites. It
runs ONE complete Layer3 Stage1 flow through the REAL lower-layer machinery:

- a global Paper with REAL L2S1 canonical parse + built retrieval index
  (plain Layer2 public pipeline, fake parser, offline);
- a bound-schema Workspace created from the real ``bus_control_rl``
  SchemaDefinition (REQ-003 / AC-004);
- REAL Workspace-owned Schema runs persisted through the L2S2 public API
  (``extract_schema``) into the Workspace-specific storage root (REQ-004 / AC-006);
- a REAL Base Wiki built through the L2S3 ``WorkspaceWikiBuildService``
  composition bound to the Workspace-specific roots (REQ-005 / REQ-006 / AC-008);
- read-only Grounding over the complete state (REQ-007 / REQ-008 / AC-012 /
  AC-013);
- the bound ``WorkspaceKnowledgeGateway`` consuming Papers, L2S1 evidence,
  Workspace Schema content and the Base Wiki (REQ-010 / REQ-011 / AC-018..22);
- stale-revision rejection and membership revocation (REQ-012 / AC-015 /
  AC-023);
- deducible Wiki staleness after membership change (REQ-007 / AC-010 / AC-011);
- archive + delete lifecycle with global Paper/L2S1 assets surviving
  (REQ-009 / AC-016 / AC-017 / C-009) — through dedicated committed sessions,
  including an independent-session read that simulates a process restart
  (AC-001), and continued direct usability of the L2S1 public APIs after
  Workspace deletion (AC-024).

No Agent framework, LangGraph, ResearchPlan or Agentic-Wiki machinery is
involved anywhere (C-001); see ``test_l3s1_no_agent_dependency.py`` for the
static import-graph assertion.
"""

from __future__ import annotations

import json

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import Paper as PaperRow
from transit_scholar.db.models import Workspace as WorkspaceRow
from transit_scholar.db.models import WorkspacePaperMembership as MembershipRow
from transit_scholar.layer2 import build_retrieval
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from transit_scholar.layer2.retrieval.api import read_blocks as l2_read_blocks
from transit_scholar.layer2.retrieval.api import search_bm25 as l2_search_bm25
from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    get_schema_definition,
)
from transit_scholar.layer2.schema_extraction.models import FieldResult
from transit_scholar.layer2.wiki import (
    PaperMetadata,
    create_production_wiki_composition,
)
from transit_scholar.layer3.grounding import (
    ACTION_BUILD_BASE_WIKI,
    ACTION_MATERIALIZE_SCHEMA_RUNS,
    ACTION_REBUILD_BASE_WIKI,
    WorkspaceGroundingService,
)
from transit_scholar.layer3.knowledge import L2S1EvidenceDelegate
from transit_scholar.layer3.schema import WorkspaceSchemaService
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.wiki import WorkspaceWikiService
from transit_scholar.layer3.workspace import (
    PaperNotMemberError,
    WorkspaceChangedError,
    WorkspaceKnowledgeGateway,
    WorkspaceNotActiveError,
    WorkspaceService,
    compute_schema_hash,
)
from tests.l2s1_fixtures import make_ready_paper, patch_parsers

DEFINITION = get_schema_definition("bus_control_rl")

EVIDENCE_ITEMS = [
    make_item(
        item_id="h1", item_type="heading", text="Method", order=0, page=1,
        level=1, bbox=[70, 60, 530, 80],
    ),
    make_item(
        item_id="p1", item_type="paragraph",
        text="Reinforcement learning trains the holding controller.",
        order=1, page=1, bbox=[70, 100, 530, 120],
    ),
    make_item(
        item_id="p2", item_type="paragraph",
        text="Deep neural networks approximate the value function.",
        order=2, page=1, bbox=[70, 140, 530, 160],
    ),
]


class _NoProposalClient:
    """Fake structured LLM client for the offline Wiki composition."""

    is_fake = False
    provider_name = "test"
    model_name = "test"

    def generate_structured(self, messages, output_schema, metadata=None):
        return output_schema.model_validate({"proposals": []})


class _FixedEmbedding(EmbeddingProvider):
    available = True
    reason = None
    info = ProviderInfo(provider="test", model="test", dimension=2)

    def dimension(self):
        return 2

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def build_ready_paper(project_tmp_path, monkeypatch, l2_config, *, title: str):
    """Global Paper with REAL L2S1 canonical parse + built retrieval index.

    The Paper row is committed through the real session-local engine BEFORE
    the transactional ``session`` fixture performs any statement (the L2S1
    suite convention), so it remains visible to the fixture's snapshot.
    """
    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path, title=title)
    patch_parsers(
        monkeypatch, [FakeParserAdapter(items=EVIDENCE_ITEMS, page_count=1)]
    )
    from transit_scholar.layer2.pipeline import parse_paper

    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    build_retrieval(paper_id, config=l2_config)
    return paper_id


def add_plain_paper(session, *, paper_id: str, title: str):
    """A global Paper row without any L2S1 derived assets (AC-014 case)."""
    paper = PaperRow(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def offline_wiki_kwargs(session):
    """Offline L2S3 build composition through Workspace-specific roots."""

    def metadata_loader(paper_id):
        paper = session.get(PaperRow, paper_id)
        if paper is None or not paper.title:
            return None
        return PaperMetadata(paper_id=paper.id, title=paper.title, year=2024)

    return {
        "paper_metadata_loader": metadata_loader,
        "composition_factory": lambda context, store: (
            create_production_wiki_composition(
                context,
                store,
                llm_client=_NoProposalClient(),
                embedding_provider=_FixedEmbedding(),
            )
        ),
    }


# ---------------------------------------------------------------------------
# AC-001..AC-023: one complete bound-schema Workspace flow
# ---------------------------------------------------------------------------


def test_e2e_bound_workspace_full_flow(session, project_tmp_path, monkeypatch, l2_config):
    # --- global Paper with real L2S1 assets (committed before the fixture
    # --- session touches the DB).
    paper_a = build_ready_paper(
        project_tmp_path, monkeypatch, l2_config, title="Bound Flow Paper A"
    )

    service = WorkspaceService(session)

    # AC-004: bound-schema Workspace persists the exact binding triple.
    workspace = service.create(
        name="Bound E2E", schema_definition=DEFINITION, workspace_id="e2e-bound-ws"
    ).workspace
    assert workspace.schema_mode == "bound"
    assert workspace.schema_binding is not None
    assert workspace.schema_binding.schema_id == DEFINITION.schema_id
    assert workspace.schema_binding.schema_version == DEFINITION.version
    assert workspace.schema_binding.schema_hash == compute_schema_hash(DEFINITION)
    assert workspace.status == "active"
    assert workspace.revision == 1
    ws_id = workspace.workspace_id

    # AC-014: a member Paper may lack L2S1 readiness / Workspace Schema runs.
    paper_b = add_plain_paper(session, paper_id="e2e-plain-b", title="Plain Paper B")
    service.add_paper(ws_id, paper_a)
    service.add_paper(ws_id, paper_b.id)
    assert service.get(ws_id).revision == 3

    # AC-012: Grounding exposes the CURRENT derived state (missing Schema/Wiki).
    grounder = WorkspaceGroundingService(
        session, data_root=project_tmp_path, evidence=L2S1EvidenceDelegate(config=l2_config)
    )
    snapshot = grounder.ground(ws_id)
    assert snapshot.status == "active"
    assert snapshot.member_paper_ids == sorted([paper_a, paper_b.id])
    assert snapshot.schema_coverage.status == "missing"
    assert snapshot.base_wiki.status == "missing"
    by_id = {paper.paper_id: paper for paper in snapshot.visible_papers}

    # Global L2S1 assets are the global Paper's own (C-002 / AC-024); the
    # snapshot derives readiness from them read-only.
    assert by_id[paper_a].l2s1_ready is True
    assert by_id[paper_a].schema_status == "missing"
    assert by_id[paper_b.id].l2s1_ready is False
    assert by_id[paper_b.id].schema_status == "missing"
    assert ACTION_MATERIALIZE_SCHEMA_RUNS in [
        action.code for action in snapshot.recommended_actions
    ]
    assert ACTION_BUILD_BASE_WIKI in [
        action.code for action in snapshot.recommended_actions
    ]

    # --- Workspace-owned Schema runs through the L2S2 public API.
    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schemas.materialize(ws_id, paper_a, llm_client=FakeLLMProvider())
    schemas.materialize(ws_id, paper_b.id, llm_client=FakeLLMProvider())

    # REQ-004/AC-006: the runs live under THIS Workspace's schemas root only.
    layout = workspace_layout(ws_id, data_root=project_tmp_path)
    assert (layout.schemas_dir / paper_a / "current.json").is_file()
    assert (layout.schemas_dir / paper_b.id / "current.json").is_file()

    snapshot = grounder.ground(ws_id)
    assert snapshot.schema_coverage.status == "complete"
    assert all(p.schema_status == "ready" for p in snapshot.visible_papers)

    # --- Base Wiki built through the real L2S3 composition (offline fakes).
    wiki = WorkspaceWikiService(
        session,
        data_root=project_tmp_path,
        **offline_wiki_kwargs(session),
    )
    outcome = wiki.build(ws_id)
    assert (layout.wiki_dir / "manifest.json").is_file()
    assert outcome.fingerprint == outcome.provenance.input_fingerprint

    # AC-011: unchanged inputs + intact artifacts -> ready.
    snapshot = grounder.ground(ws_id)
    assert snapshot.base_wiki.status == "ready"
    assert snapshot.base_wiki.fingerprint == snapshot.base_wiki.recorded_fingerprint
    revision_ready = snapshot.revision

    # --- AC-022: the bound gateway is created with the Workspace identity and
    # --- its public methods take no workspace_id.
    bound = WorkspaceKnowledgeGateway(
        session,
        workspace_id=ws_id,
        expected_revision=revision_ready,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )

    # REQ-010/AC-019: member-Paper evidence delegates to the L2S1 public API.
    direct = l2_search_bm25(paper_a, "reinforcement", config=l2_config)
    via_gateway = bound.search_evidence(paper_a, "reinforcement")
    assert via_gateway.status == direct.status == "ok"
    assert [(h.chunk_id, h.rank, h.score) for h in via_gateway.hits] == [
        (h.chunk_id, h.rank, h.score) for h in direct.hits
    ]

    # REQ-010/AC-020: Workspace Schema reads resolve THIS Workspace's root.
    instance = bound.get_schema_instance(paper_a)
    assert instance.paper_id == paper_a
    assert instance.schema_id == DEFINITION.schema_id
    field = bound.get_schema_field(paper_a, "research_problem.control_type")
    assert isinstance(field, FieldResult)
    assert field.status in {
        "explicit", "inferred", "unclear", "not_found", "not_applicable",
        "conflicting",
    }

    # REQ-010/AC-021: Base Wiki reads resolve the bound Workspace's own Wiki.
    wiki_result = bound.search_wiki("controller")
    assert wiki_result.status == "ok"

    # AC-018: a non-member Paper is rejected BEFORE any lower-layer call.
    stranger = add_plain_paper(session, paper_id="e2e-stranger", title="Stranger")
    with pytest.raises(PaperNotMemberError) as exc_info:
        bound.get_paper(stranger.id)
    assert exc_info.value.code == "paper_not_member"

    # --- AC-023: an authoritative mutation advances the revision; the gateway
    # --- bound to the OLD revision rejects the stale call explicitly.
    service.add_paper(ws_id, stranger.id)  # revision N -> N+1
    assert service.get(ws_id).revision == revision_ready + 1
    with pytest.raises(WorkspaceChangedError) as exc_info:
        bound.search_evidence(paper_a, "reinforcement")
    assert exc_info.value.code == "workspace_changed"
    assert exc_info.value.expected_revision == revision_ready
    assert exc_info.value.current_revision == revision_ready + 1
    # A gateway without an expected revision revalidates against the latest
    # authoritative state and keeps working (REQ-012 second branch).
    fresh = WorkspaceKnowledgeGateway(
        session,
        workspace_id=ws_id,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )
    assert fresh.search_evidence(paper_a, "reinforcement").status == "ok"

    # --- AC-015/REQ-012: removing a Paper makes it inaccessible immediately;
    # --- AC-010: the Wiki becomes observably stale (no persisted flag).
    service.remove_paper(ws_id, paper_a)
    with pytest.raises(PaperNotMemberError):
        fresh.get_paper(paper_a)
    with pytest.raises(PaperNotMemberError):
        fresh.search_evidence(paper_a, "question")
    with pytest.raises(PaperNotMemberError):
        fresh.get_schema_instance(paper_a)
    assert wiki.status(ws_id).status == "stale"
    snapshot = grounder.ground(ws_id)
    assert snapshot.base_wiki.status == "stale"
    # Derived actions stay REPORTED-only (never executed by Grounding): the
    # newly added member lacking a Schema run keeps the materialize action and
    # the changed membership adds the rebuild-base-wiki action.
    action_codes = [action.code for action in snapshot.recommended_actions]
    assert ACTION_MATERIALIZE_SCHEMA_RUNS in action_codes
    materialize_action = next(
        action for action in snapshot.recommended_actions
        if action.code == ACTION_MATERIALIZE_SCHEMA_RUNS
    )
    assert materialize_action.target_paper_ids == ["e2e-stranger"]
    assert ACTION_REBUILD_BASE_WIKI in action_codes

    # --- AC-016: archiving preserves memberships/files; active access is
    # --- rejected with an explicit not-active outcome.
    service.archive(ws_id)
    with pytest.raises(WorkspaceNotActiveError) as exc_info:
        fresh.list_papers()
    assert exc_info.value.code == "workspace_not_active"
    assert [m.paper_id for m in service.list_memberships(ws_id)] == sorted(
        [paper_b.id, stranger.id]
    )
    assert (layout.wiki_dir / "manifest.json").is_file()


def test_e2e_bound_workspace_delete_preserves_global_assets(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-001/AC-017/AC-024 with dedicated committed sessions.

    A process-restart-style read reconstructs the authoritative control-plane
    state; after Workspace delete the global Paper row and its L2S1 assets
    survive and the L2S1 public APIs remain directly usable.
    """
    paper_id = build_ready_paper(
        project_tmp_path, monkeypatch, l2_config, title="Delete Survivor Paper"
    )

    first = SessionLocal()
    workspace_id = None
    try:
        service = WorkspaceService(first)
        workspace = service.create(
            name="Delete E2E", schema_definition=DEFINITION
        ).workspace
        workspace_id = workspace.workspace_id
        first.add(PaperRow(id="e2e-delete-b", title="Member B", status="active"))
        first.flush()
        service.add_paper(workspace_id, paper_id)
        service.add_paper(workspace_id, "e2e-delete-b")
        # Workspace-owned derived artifacts exist before deletion.
        layout = workspace_layout(workspace_id, data_root=project_tmp_path)
        (layout.schemas_dir / paper_id).mkdir(parents=True, exist_ok=True)
        (layout.schemas_dir / paper_id / "current.json").write_text(
            '{"run_id": "r1"}\n', encoding="utf-8"
        )
        (layout.wiki_dir).mkdir(parents=True, exist_ok=True)
        (layout.wiki_dir / "manifest.json").write_text(
            '{"manifest": true}\n', encoding="utf-8"
        )
        first.commit()

        # AC-001: reading through an entirely fresh session (process restart
        # semantics) reconstructs the same authoritative state.
        second = SessionLocal()
        try:
            row = second.get(WorkspaceRow, workspace_id)
            assert row is not None
            assert row.status == "active"
            assert row.schema_mode == "bound"
            assert row.schema_id == DEFINITION.schema_id
            assert row.schema_version == DEFINITION.version
            member_ids = {
                m.paper_id for m in service.list_memberships(workspace_id)
            }
            assert member_ids == {paper_id, "e2e-delete-b"}
        finally:
            second.close()

        # AC-017: delete transitions through deleting -> deleted, removes the
        # Workspace's Schema/Wiki storage and memberships...
        result = WorkspaceService(first).delete(
            workspace_id, data_root=project_tmp_path
        )
        assert result.workspace.status == "deleted"
        assert first.query(MembershipRow).filter_by(
            workspace_id=workspace_id
        ).count() == 0
        assert not workspace_layout(
            workspace_id, data_root=project_tmp_path
        ).derived_dir.exists()

        # ... while the global Paper record and L2S1 assets survive (C-009).
        survivor = first.get(PaperRow, paper_id)
        assert survivor is not None and survivor.title == "Delete Survivor Paper"

        # AC-024: the L2S1 public APIs remain usable independently of Layer3.
        direct = l2_search_bm25(paper_id, "reinforcement", config=l2_config)
        assert direct.status == "ok"
        assert len(direct.hits) > 0
        from transit_scholar.layer2.paths import load_current, run_paths

        run_id = load_current(l2_config.parsed_paper_dir(paper_id))
        assert run_id is not None
        rp = run_paths(l2_config, paper_id, run_id)
        block_ids = [
            json.loads(line)["block_id"]
            for line in rp.blocks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert block_ids, "expected canonical blocks for the parsed paper"
        assert l2_read_blocks(paper_id, block_ids[:1]) != []
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
            cleanup.execute(
                PaperRow.__table__.delete().where(PaperRow.id == "e2e-delete-b")
            )
            cleanup.commit()
        finally:
            cleanup.close()
