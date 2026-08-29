"""Layer3 Stage1 gateway Base-Wiki isolation tests (T-005 / AC-021).

Proves through the bound gateway that Wiki read/search operations resolve the
Workspace's own Wiki service/store only:

- AC-008/AC-021: a Wiki built for Workspace A is never returned when
  Workspace B's gateway requests its Base Wiki — B sees explicit missing
  outcomes, and after B builds its own snapshot the two Workspaces search
  independent Wiki identities;
- REQ-005 boundary: even if A's snapshot files are copied into B's boundary,
  B's gateway reports an explicit mismatch/corrupt outcome, never A's content;
- AC-010: input changes (membership) make the built Wiki observably stale and
  the gateway returns the explicit ``wiki_stale`` degraded outcome instead of
  silently reading non-current facts;
- AC-009: no-schema Workspaces report ``wiki_unsupported`` through the
  gateway with no fallback construction.

The L2S3 build composition is fully offline (fake structured-LLM client and
fake embedding provider, mirroring the T-004 suite); Schema runs are produced
through the real L2S2 public API with an offline fake LLM.
"""

from __future__ import annotations

import shutil

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    get_schema,
    get_schema_definition,
)
from transit_scholar.layer2.wiki import (
    PaperMetadata,
    WorkspaceWikiBuildService,
    create_production_wiki_composition,
)
from transit_scholar.layer3.knowledge import WorkspaceKnowledgeGateway
from transit_scholar.layer3.schema import WorkspaceSchemaService
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.wiki import (
    WikiCorruptError,
    WikiMissingError,
    WikiStaleError,
    WikiUnsupportedError,
    WorkspaceWikiService,
)
from transit_scholar.layer3.workspace import WorkspaceService

DEFINITION = get_schema_definition("bus_control_rl")


class _Client:
    """Fake structured LLM client: no entity proposals (still a valid build)."""

    is_fake = False
    provider_name = "test"
    model_name = "test"

    def generate_structured(self, messages, output_schema, metadata=None):
        return output_schema.model_validate({"proposals": []})


class _Embedding(EmbeddingProvider):
    available = True
    reason = None
    info = ProviderInfo(provider="test", model="test", dimension=2)

    def dimension(self):
        return 2

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


def add_paper(session, paper_id="gw-p1", title="Wiki Shared Paper") -> Paper:
    paper = Paper(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def create_bound_workspace(session, name, workspace_id):
    return WorkspaceService(session).create(
        name=name, schema_definition=DEFINITION, workspace_id=workspace_id
    ).workspace


def create_none_workspace(session, name="No Schema", workspace_id="gw-wiki-none"):
    return WorkspaceService(session).create(name=name, workspace_id=workspace_id).workspace


def _offline_build_factory(session):
    """Offline L2S3 build factory preserving each Workspace's Schema root."""

    def factory(workspace_id, layout):
        storage = layout.schema_storage()

        def metadata_loader(paper_id):
            paper = session.get(Paper, paper_id)
            if paper is None or not paper.title:
                return None
            return PaperMetadata(paper_id=paper.id, title=paper.title, year=2024)

        return WorkspaceWikiBuildService(
            schema_instance_loader=lambda paper_id, schema_id: get_schema(
                paper_id, schema_id, storage=storage
            ),
            paper_metadata_loader=metadata_loader,
            composition_factory=lambda context, store: create_production_wiki_composition(
                context,
                store,
                llm_client=_Client(),
                embedding_provider=_Embedding(),
            ),
            wiki_storage_root=layout.wiki_store_base,
        )

    return factory


def wiki_service(session, project_tmp_path):
    return WorkspaceWikiService(
        session,
        data_root=project_tmp_path,
        build_service_factory=_offline_build_factory(session),
    )


def gateway_for(session, project_tmp_path, workspace_id, *, wiki=None):
    return WorkspaceKnowledgeGateway(
        session, workspace_id=workspace_id, data_root=project_tmp_path, wiki=wiki
    )


def materialize(session, project_tmp_path, workspace_id, paper_id):
    WorkspaceSchemaService(
        session, data_root=project_tmp_path
    ).materialize(workspace_id, paper_id, llm_client=FakeLLMProvider())


# ---------------------------------------------------------------------------
# AC-008/AC-021: gateway Wiki reads resolve only the bound Workspace's Wiki
# ---------------------------------------------------------------------------


def test_gateway_wiki_search_never_returns_another_workspaces_wiki(
    session, project_tmp_path
):
    paper = add_paper(session, "gw-wiki-pa", "Bus Control RL Shared Paper")
    ws_a = create_bound_workspace(session, "A", "gww-a")
    ws_b = create_bound_workspace(session, "B", "gww-b")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)
    materialize(session, project_tmp_path, ws_a.workspace_id, paper.id)
    materialize(session, project_tmp_path, ws_b.workspace_id, paper.id)

    wiki = wiki_service(session, project_tmp_path)
    wiki.build(ws_a.workspace_id)

    gateway_a = gateway_for(
        session, project_tmp_path, ws_a.workspace_id, wiki=wiki
    )
    gateway_b = gateway_for(
        session, project_tmp_path, ws_b.workspace_id, wiki=wiki
    )

    assert gateway_a.wiki_status().status == "ready"
    hits_a = gateway_a.search_wiki("bus control", mode="lexical").hits
    assert hits_a
    page_a = hits_a[0].object_id

    # B has no snapshot of its own: explicit missing, never A's content.
    status_b = gateway_b.wiki_status()
    assert status_b.status == "missing"
    with pytest.raises(WikiMissingError) as missing_b:
        gateway_b.search_wiki("bus control")
    assert missing_b.value.code == "wiki_missing"

    # After B builds its own Wiki, each gateway searches its own only — the
    # same Paper yields independent Wiki identities per Workspace (AC-008).
    wiki.build(ws_b.workspace_id)
    hits_a = gateway_a.search_wiki("bus control", mode="lexical").hits
    hits_b = gateway_b.search_wiki("bus control", mode="lexical").hits
    assert hits_a and hits_b
    assert hits_b[0].object_id != hits_a[0].object_id


def test_gateway_wiki_cross_workspace_contamination_is_rejected(
    session, project_tmp_path
):
    paper = add_paper(session, "gw-wiki-pa", "Isolation Shared Paper")
    ws_a = create_bound_workspace(session, "A", "gww-iso-a")
    ws_b = create_bound_workspace(session, "B", "gww-iso-b")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)
    materialize(session, project_tmp_path, ws_a.workspace_id, paper.id)
    materialize(session, project_tmp_path, ws_b.workspace_id, paper.id)

    wiki = wiki_service(session, project_tmp_path)
    wiki.build(ws_a.workspace_id)

    layout_a = workspace_layout(ws_a.workspace_id, data_root=project_tmp_path)
    layout_b = workspace_layout(ws_b.workspace_id, data_root=project_tmp_path)

    # Simulate contamination: A's snapshot files are copied into B's boundary.
    assert not layout_b.wiki_dir.exists()
    shutil.copytree(layout_a.wiki_dir, layout_b.wiki_dir)

    gateway_b = gateway_for(
        session, project_tmp_path, ws_b.workspace_id, wiki=wiki
    )
    # B's derived status is an explicit workspace-mismatch error, never
    # ready-from-A (REQ-005 boundary enforcement), and search raises the
    # explicit corrupt/error outcome instead of returning A's pages.
    status_b = gateway_b.wiki_status()
    assert status_b.status == "error"
    assert status_b.error_code == "workspace_mismatch"
    with pytest.raises(WikiCorruptError) as corrupt:
        gateway_b.search_wiki("isolation")
    assert corrupt.value.code == "wiki_corrupt"


# ---------------------------------------------------------------------------
# AC-010: derived staleness through the gateway (no silent stale reads)
# ---------------------------------------------------------------------------


def test_gateway_wiki_becomes_stale_after_membership_change(session, project_tmp_path):
    paper_one = add_paper(session, "gw-wiki-p1", "First Paper")
    ws = create_bound_workspace(session, "Stale Wiki", "gww-stale")
    service = WorkspaceService(session)
    service.add_paper(ws.workspace_id, paper_one.id)
    materialize(session, project_tmp_path, ws.workspace_id, paper_one.id)

    wiki = wiki_service(session, project_tmp_path)
    wiki.build(ws.workspace_id)
    gateway = gateway_for(session, project_tmp_path, ws.workspace_id, wiki=wiki)
    assert gateway.wiki_status().status == "ready"
    assert gateway.search_wiki("regulation", mode="lexical").hits is not None

    # Add a second member Paper: authoritative inputs changed -> stale.
    paper_two = add_paper(session, "gw-wiki-p2", "Second Paper")
    service.add_paper(ws.workspace_id, paper_two.id)
    materialize(session, project_tmp_path, ws.workspace_id, paper_two.id)

    stale = gateway.wiki_status()
    assert stale.status == "stale"
    assert stale.error_code == "input_fingerprint_mismatch"
    with pytest.raises(WikiStaleError) as stale_read:
        gateway.search_wiki("regulation")
    assert stale_read.value.code == "wiki_stale"


# ---------------------------------------------------------------------------
# AC-009: no-schema Workspace Wiki access is unsupported through the gateway
# ---------------------------------------------------------------------------


def test_gateway_wiki_unsupported_for_no_schema_workspace(session, project_tmp_path):
    paper = add_paper(session, "gw-wiki-none-p")
    ws = create_none_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)

    gateway = gateway_for(session, project_tmp_path, ws.workspace_id)
    assert gateway.wiki_status().status == "unsupported"
    with pytest.raises(WikiUnsupportedError) as unsupported:
        gateway.search_wiki("anything")
    assert unsupported.value.code == "wiki_unsupported"
    # Nothing was ever constructed for the no-schema Workspace.
    assert not workspace_layout(
        ws.workspace_id, data_root=project_tmp_path
    ).derived_dir.exists()