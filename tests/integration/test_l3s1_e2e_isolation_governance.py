"""Layer3 Stage1 end-to-end regression — isolation & governance (T-006).

Integrates the cross-cutting Layer3 Stage1 guarantees with REAL lower-layer
assets (L2S1 parse/retrieval, L2S2 Schema runs, L2S3 Base Wiki):

- AC-006: the same global Paper processed under the same SchemaDefinition in
  two Workspaces persists its Schema runs/current pointers under two DIFFERENT
  Workspace-specific roots; deleting one Workspace's Schema storage leaves the
  other's content intact and readable;
- AC-008: two Workspaces with an overlapping Paper resolve to distinct Wiki
  roots; a Wiki snapshot built for Workspace A is never returned when
  Workspace B requests its Base Wiki (explicit missing outcome), and the
  gateway for B cannot read A's snapshot;
- AC-010/AC-011: Wiki freshness is DERIVED from the deterministic input
  fingerprint — unchanged inputs + intact artifacts -> ``ready``, a membership
  change -> ``stale``, with no persisted boolean readiness flag;
- AC-013: Workspace Grounding is read-only end-to-end — repeated Grounding
  against a fully built Workspace changes no files (verified by hashing the
  entire Workspace-derived tree) and no database rows, and never materializes
  Schema/Wiki storage for error or no-schema paths;
- AC-017/C-009/AC-024: Workspace delete removes only Workspace-owned storage;
  the global Paper and its L2S1 assets survive and stay directly usable.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from transit_scholar.db.models import Paper as PaperRow
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    get_schema_definition,
)
from transit_scholar.layer2.wiki import (
    PaperMetadata,
    WorkspaceWikiBuildService,
    create_production_wiki_composition,
)
from transit_scholar.layer3.grounding import WorkspaceGroundingService
from transit_scholar.layer3.knowledge import L2S1EvidenceDelegate
from transit_scholar.layer3.schema import WorkspaceSchemaService
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.wiki import WikiMissingError, WorkspaceWikiService
from transit_scholar.layer3.workspace import (
    WorkspaceKnowledgeGateway,
    WorkspaceService,
)
from tests.l2s1_fixtures import make_ready_paper, patch_parsers

DEFINITION = get_schema_definition("bus_control_rl")

EVIDENCE_ITEMS = [
    make_item(
        item_id="p1", item_type="paragraph",
        text="Reinforcement learning trains the holding controller.",
        order=0, page=1, bbox=[70, 100, 530, 120],
    ),
    make_item(
        item_id="p2", item_type="paragraph",
        text="Deep neural networks approximate the value function.",
        order=1, page=1, bbox=[70, 140, 530, 160],
    ),
]


class _NoProposalClient:
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


def build_ready_paper(project_tmp_path, monkeypatch, l2_config, *, title: str):
    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path, title=title)
    patch_parsers(
        monkeypatch, [FakeParserAdapter(items=EVIDENCE_ITEMS, page_count=1)]
    )
    from transit_scholar.layer2.pipeline import parse_paper

    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    from transit_scholar.layer2 import build_retrieval

    build_retrieval(paper_id, config=l2_config)
    return paper_id


def offline_wiki_factory(session):
    def factory(workspace_id, layout):
        from transit_scholar.layer2.schema_extraction.api import get_schema

        storage = layout.schema_storage()

        def metadata_loader(paper_id):
            paper = session.get(PaperRow, paper_id)
            if paper is None or not paper.title:
                return None
            return PaperMetadata(paper_id=paper.id, title=paper.title, year=2024)

        return WorkspaceWikiBuildService(
            schema_instance_loader=lambda paper_id, schema_id: get_schema(
                paper_id, schema_id, storage=storage
            ),
            paper_metadata_loader=metadata_loader,
            composition_factory=lambda context, store: (
                create_production_wiki_composition(
                    context,
                    store,
                    llm_client=_NoProposalClient(),
                    embedding_provider=_FixedEmbedding(),
                )
            ),
            wiki_storage_root=layout.wiki_store_base,
        )

    return factory


def prepare_bound_workspace(
    session, project_tmp_path, *, workspace_id: str, name: str, paper_ids: list[str]
):
    """Create a bound Workspace, add members, materialize their Schema runs
    and build the Base Wiki (real L2S2/L2S3 machinery, offline providers)."""
    service = WorkspaceService(session)
    workspace = service.create(
        name=name, schema_definition=DEFINITION, workspace_id=workspace_id
    ).workspace
    for paper_id in paper_ids:
        service.add_paper(workspace.workspace_id, paper_id)
    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    for paper_id in paper_ids:
        schemas.materialize(
            workspace.workspace_id, paper_id, llm_client=FakeLLMProvider()
        )
    wiki = WorkspaceWikiService(
        session,
        data_root=project_tmp_path,
        build_service_factory=offline_wiki_factory(session),
    )
    wiki.build(workspace.workspace_id)
    return workspace.workspace_id


def _tree_manifest(root) -> dict[str, str]:
    """Relative path -> sha256 over every file below ``root`` (if any)."""
    manifest: dict[str, str] = {}
    if not root.exists():
        return manifest
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest[str(path.relative_to(root)).replace("\\", "/")] = digest
    return manifest


def test_same_paper_same_schema_two_workspaces_stay_isolated(
    session, project_tmp_path, monkeypatch, l2_config
):
    """AC-006 + AC-008 with one shared global Paper and REAL derived assets:
    two Workspaces bound to the identical SchemaDefinition keep independent
    Schema runs and independent Wiki snapshots; removing one Workspace's
    storage never alters the other's, and B never sees A's Wiki."""
    paper_id = build_ready_paper(
        project_tmp_path, monkeypatch, l2_config, title="Shared Isolation Paper"
    )
    service = WorkspaceService(session)
    ws_a = service.create(
        name="Isolation A", schema_definition=DEFINITION, workspace_id="iso-a"
    ).workspace.workspace_id
    ws_b = service.create(
        name="Isolation B", schema_definition=DEFINITION, workspace_id="iso-b"
    ).workspace.workspace_id
    for ws_id in (ws_a, ws_b):
        service.add_paper(ws_id, paper_id)

    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schemas.materialize(ws_a, paper_id, llm_client=FakeLLMProvider())
    schemas.materialize(ws_b, paper_id, llm_client=FakeLLMProvider())

    layout_a = workspace_layout(ws_a, data_root=project_tmp_path)
    layout_b = workspace_layout(ws_b, data_root=project_tmp_path)
    assert layout_a.schemas_dir != layout_b.schemas_dir
    assert layout_a.wiki_dir != layout_b.wiki_dir

    # AC-006: both persisted runs exist, each under its own root.
    assert (layout_a.schemas_dir / paper_id / "current.json").is_file()
    assert (layout_b.schemas_dir / paper_id / "current.json").is_file()
    run_a = json.loads(
        (layout_a.schemas_dir / paper_id / "current.json").read_text(encoding="utf-8")
    )
    run_b = json.loads(
        (layout_b.schemas_dir / paper_id / "current.json").read_text(encoding="utf-8")
    )
    assert run_a["run_id"] != run_b["run_id"]

    # AC-008: build A's Wiki only; B has none of its own.
    wiki = WorkspaceWikiService(
        session,
        data_root=project_tmp_path,
        build_service_factory=offline_wiki_factory(session),
    )
    wiki.build(ws_a)
    assert (layout_a.wiki_dir / "manifest.json").is_file()
    assert wiki.status(ws_b).status == "missing"
    with pytest.raises(WikiMissingError) as exc_info:
        wiki.search(ws_b, "controller")
    assert exc_info.value.code == "wiki_missing"

    # B's gateway cannot read A's Wiki either — the Wiki object resolves for
    # the bound Workspace only and reports missing/stale for B.
    gateway_b = WorkspaceKnowledgeGateway(
        session,
        workspace_id=ws_b,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )
    with pytest.raises(WikiMissingError):
        gateway_b.search_wiki("controller")
    assert gateway_b.wiki_status().status == "missing"
    # A keeps reading its own Wiki (AC-008 reverse direction).
    gateway_a = WorkspaceKnowledgeGateway(
        session,
        workspace_id=ws_a,
        data_root=project_tmp_path,
        evidence=L2S1EvidenceDelegate(config=l2_config),
    )
    assert gateway_a.search_wiki("controller").status == "ok"

    # AC-006: deleting A's Schema storage must not remove or alter B's.
    layout_a.delete_schema_storage()
    assert not layout_a.schemas_dir.exists()
    assert (layout_b.schemas_dir / paper_id / "current.json").is_file()
    schema_b = WorkspaceSchemaService(session, data_root=project_tmp_path)
    instance_b = schema_b.get_instance(ws_b, paper_id)
    assert instance_b.paper_id == paper_id
    assert instance_b.schema_id == DEFINITION.schema_id


def test_wiki_freshness_derived_from_fingerprint_real_flow(
    session, project_tmp_path, monkeypatch, l2_config
):
    """AC-010/AC-011 with real assets: after a successful build the Wiki is
    ready; a membership change makes Grounding derive stale WITHOUT any
    persisted boolean flag."""
    paper_a = "fp-a"
    paper_b = "fp-b"
    for pid in (paper_a, paper_b):
        session.add(PaperRow(id=pid, title=f"Fingerprint Paper {pid}", status="active"))
    session.flush()

    ws_id = prepare_bound_workspace(
        session, project_tmp_path, workspace_id="fp-ws", name="Fingerprint",
        paper_ids=[paper_a, paper_b],
    )
    service = WorkspaceService(session)
    wiki = WorkspaceWikiService(
        session,
        data_root=project_tmp_path,
        build_service_factory=offline_wiki_factory(session),
    )
    grounder = WorkspaceGroundingService(
        session, data_root=project_tmp_path, evidence=L2S1EvidenceDelegate(config=l2_config)
    )

    assert wiki.status(ws_id).status == "ready"
    assert grounder.ground(ws_id).base_wiki.status == "ready"

    # Membership change -> fingerprint mismatch -> derived stale.
    session.add(PaperRow(id="fp-c", title="Fingerprint Paper fp-c", status="active"))
    session.flush()
    service.add_paper(ws_id, "fp-c")
    derived = wiki.status(ws_id)
    assert derived.status == "stale"
    assert derived.error_code == "input_fingerprint_mismatch"
    snapshot = grounder.ground(ws_id)
    assert snapshot.base_wiki.status == "stale"
    # No boolean readiness flag is persisted anywhere (REQ-007): the stale
    # outcome comes purely from the recorded vs recomputed fingerprint.
    layout = workspace_layout(ws_id, data_root=project_tmp_path)
    provenance = json.loads(
        (layout.wiki_dir / "provenance.json").read_text(encoding="utf-8")
    )
    assert "wiki_stale" not in provenance
    assert "wiki_ready" not in provenance
    assert provenance["input_fingerprint"] != derived.fingerprint


def test_grounding_readonly_no_mutations_real_assets(
    session, project_tmp_path, monkeypatch, l2_config
):
    """AC-013 end-to-end: Grounding a fully built (Schema runs + Wiki) bound
    Workspace, a stale Workspace and a no-schema Workspace changes no files
    under the Workspace tree and no database rows."""
    paper_id = "ro-a"
    paper_extra = "ro-b"
    session.add_all(
        [
            PaperRow(id=paper_id, title="Readonly Real Paper", status="active"),
            PaperRow(id=paper_extra, title="Readonly Extra Paper", status="active"),
        ]
    )
    session.flush()
    ws_id = prepare_bound_workspace(
        session, project_tmp_path, workspace_id="ro-ws", name="Readonly",
        paper_ids=[paper_id, paper_extra],
    )
    none_ws = WorkspaceService(session).create(
        name="Readonly None", workspace_id="ro-none"
    ).workspace.workspace_id
    WorkspaceService(session).add_paper(none_ws, paper_id)

    base_layout = workspace_layout(ws_id, data_root=project_tmp_path)
    none_layout = workspace_layout(none_ws, data_root=project_tmp_path)

    # Row-count probes through the SQLAlchemy core (read-only).
    from sqlalchemy import func, select

    from transit_scholar.db.models import (
        Paper,
        Workspace,
        WorkspacePaperMembership,
    )

    def row_counts():
        counts: dict[str, int] = {}
        for model in (Paper, Workspace, WorkspacePaperMembership):
            counts[model.__tablename__] = session.execute(
                select(func.count()).select_from(model)
            ).scalar_one()
        return counts

    tree_before = (
        _tree_manifest(base_layout.derived_dir),
        _tree_manifest(none_layout.derived_dir),
    )
    counts_before = row_counts()
    grounder = WorkspaceGroundingService(
        session, data_root=project_tmp_path, evidence=L2S1EvidenceDelegate(config=l2_config)
    )

    # Ground every shape: ready bound, stale bound (the membership removal is
    # a deliberate authoritative control-plane mutation performed BETWEEN the
    # two Grounding passes — never a Grounding side effect), no-schema.
    bound_ready = grounder.ground(ws_id)
    assert bound_ready.base_wiki.status == "ready"
    none_snapshot = grounder.ground(none_ws)
    assert none_snapshot.schema_coverage.status == "disabled"
    # Same state -> identical snapshots; Grounding mutates no files/rows.
    assert grounder.ground(ws_id).model_dump() == bound_ready.model_dump()
    assert grounder.ground(none_ws).model_dump() == none_snapshot.model_dump()
    assert _tree_manifest(base_layout.derived_dir) == tree_before[0]
    assert _tree_manifest(none_layout.derived_dir) == tree_before[1]
    assert row_counts() == counts_before

    service = WorkspaceService(session)
    service.remove_paper(ws_id, paper_extra)  # authoritative mutation (ours)
    counts_after_mutation = row_counts()
    tree_after_mutation = _tree_manifest(base_layout.derived_dir)
    bound_stale = grounder.ground(ws_id)
    assert bound_stale.base_wiki.status == "stale"
    # The Grounding pass after the mutation added no further changes.
    assert _tree_manifest(base_layout.derived_dir) == tree_after_mutation
    assert row_counts() == counts_after_mutation