"""Layer3 Stage1 read-only Grounding tests (T-004 / REQ-008 / AC-013).

Proves Grounding is a pure inspection path:

- with recording fakes wrapped around the real services it invokes ONLY the
  read-only seam methods (control-plane get/list_memberships, current-pointer
  reads, derived Wiki status, L2S1 readiness) and never any mutating lower-layer
  function (Schema materialization/extraction, retrieval-index build, Wiki
  build/search, provenance writes, LLM/embedding provider resolution);
- end-to-end with the production services, booby-trapped mutating entry points
  are never reached, including configured LLM/embedding provider factories
  (C-007: Grounding is network-independent with respect to provider calls);
- repeated Grounding against unchanged state mutates no files and no database
  rows (deterministic, side-effect-free snapshot).
"""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select

from transit_scholar.db.models import Paper, Workspace, WorkspacePaperMembership
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    get_schema_definition,
)
from transit_scholar.layer3.grounding import WorkspaceGroundingService
from transit_scholar.layer3.schema import WorkspaceSchemaService
from transit_scholar.layer3.wiki.service import WorkspaceWikiService
from transit_scholar.layer3.workspace import WorkspaceService

DEFINITION = get_schema_definition("bus_control_rl")

_MUTATION_MSG = "Grounding must never reach this mutating/LLM/embedding path (AC-013/C-007)"


def _boom(name: str):
    def _raise(*args, **kwargs):
        raise AssertionError(f"{name}: {_MUTATION_MSG}")

    return _raise


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def add_paper(session, paper_id="p1", title="Readonly Paper") -> Paper:
    paper = Paper(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def create_bound_workspace(session, workspace_id="ws-ro"):
    return WorkspaceService(session).create(
        name="Readonly Bound", schema_definition=DEFINITION, workspace_id=workspace_id
    ).workspace


def _offline_build_kwargs(session):
    """Offline L2S3 build composition (fake LLM/embedding/metadata) bound to
    the Workspace-specific storage roots, mirroring the Wiki tests."""
    from transit_scholar.layer2.retrieval.providers import (  # noqa: PLC0415
        EmbeddingProvider,
        ProviderInfo,
    )
    from transit_scholar.layer2.wiki import (  # noqa: PLC0415
        PaperMetadata,
        create_production_wiki_composition,
    )

    class _Client:
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

    def metadata_loader(paper_id):
        paper = session.get(Paper, paper_id)
        if paper is None or not paper.title:
            return None
        return PaperMetadata(paper_id=paper.id, title=paper.title, year=2024)

    return {
        "paper_metadata_loader": metadata_loader,
        "composition_factory": lambda context, store: create_production_wiki_composition(
            context,
            store,
            llm_client=_Client(),
            embedding_provider=_Embedding(),
        ),
    }


def make_ready_workspace(session, project_tmp_path, *, workspace_id="ws-ro"):
    """A bound Workspace with materialized Schema runs and a built Wiki."""
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id=workspace_id)
    papers = [
        add_paper(session, paper_id="pa", title="Paper A"),
        add_paper(session, paper_id="pb", title="Paper B"),
    ]
    service.add_paper(workspace.workspace_id, papers[0].id)
    service.add_paper(workspace.workspace_id, papers[1].id)
    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schemas.materialize(workspace.workspace_id, papers[0].id, llm_client=FakeLLMProvider())
    schemas.materialize(workspace.workspace_id, papers[1].id, llm_client=FakeLLMProvider())
    wiki = WorkspaceWikiService(
        session,
        data_root=project_tmp_path,
        **_offline_build_kwargs(session),
    )
    wiki.build(workspace.workspace_id)
    return workspace


# ---------------------------------------------------------------------------
# recording fakes (spies) over the real collaborators
# ---------------------------------------------------------------------------


class RecordingWorkspaces:
    """Read-only spy over the real ``WorkspaceService``; mutations boom."""

    def __init__(self, delegate: WorkspaceService) -> None:
        self.delegate = delegate
        self.calls: list[tuple] = []

    def get(self, workspace_id: str):
        self.calls.append(("get", workspace_id))
        return self.delegate.get(workspace_id)

    def list_memberships(self, workspace_id: str):
        self.calls.append(("list_memberships", workspace_id))
        return self.delegate.list_memberships(workspace_id)

    def add_paper(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceService.add_paper: {_MUTATION_MSG}")

    def remove_paper(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceService.remove_paper: {_MUTATION_MSG}")

    def archive(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceService.archive: {_MUTATION_MSG}")

    def delete(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceService.delete: {_MUTATION_MSG}")


class RecordingSchemas:
    """Read-only spy over the real ``WorkspaceSchemaService``; mutations boom."""

    def __init__(self, delegate: WorkspaceSchemaService) -> None:
        self.delegate = delegate
        self.calls: list[tuple] = []

    def paper_schema_readiness(self, workspace_id: str, paper_ids):
        self.calls.append(("paper_schema_readiness", workspace_id, tuple(paper_ids)))
        return self.delegate.paper_schema_readiness(workspace_id, paper_ids)

    def materialize(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceSchemaService.materialize: {_MUTATION_MSG}")

    def get_instance(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceSchemaService.get_instance: {_MUTATION_MSG}")

    def get_field(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceSchemaService.get_field: {_MUTATION_MSG}")


class RecordingWiki:
    """Read-only spy over the real ``WorkspaceWikiService``; mutations boom."""

    def __init__(self, delegate: WorkspaceWikiService) -> None:
        self.delegate = delegate
        self.calls: list[tuple] = []

    def status(self, workspace_id: str):
        self.calls.append(("status", workspace_id))
        return self.delegate.status(workspace_id)

    def build(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceWikiService.build: {_MUTATION_MSG}")

    def search(self, *args, **kwargs):
        raise AssertionError(f"WorkspaceWikiService.search: {_MUTATION_MSG}")


class SpyingEvidence:
    """Read-only L2S1 seam; lower-layer search/read calls boom."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def l2s1_ready(self, paper_id: str) -> bool:
        self.calls.append(("l2s1_ready", paper_id))
        return False

    def search(self, *args, **kwargs):
        raise AssertionError(f"L2S1 search: {_MUTATION_MSG}")

    def read_blocks(self, *args, **kwargs):
        raise AssertionError(f"L2S1 read_blocks: {_MUTATION_MSG}")


# ---------------------------------------------------------------------------
# AC-013: only read-only seam methods are ever invoked
# ---------------------------------------------------------------------------


def test_grounding_invokes_only_read_only_collaborators(session, project_tmp_path):
    workspace = make_ready_workspace(session, project_tmp_path)

    workspaces = RecordingWorkspaces(WorkspaceService(session))
    schemas = RecordingSchemas(
        WorkspaceSchemaService(session, data_root=project_tmp_path)
    )
    wiki = RecordingWiki(
        WorkspaceWikiService(session, data_root=project_tmp_path)
    )
    evidence = SpyingEvidence()
    ground = WorkspaceGroundingService(
        session,
        data_root=project_tmp_path,
        workspaces=workspaces,
        schemas=schemas,
        wiki=wiki,
        evidence=evidence,
    ).ground

    snapshot = ground(workspace.workspace_id)
    assert snapshot.base_wiki.status == "ready"

    # Every recorded call is on the read-only allowlist (AC-013).
    for call in workspaces.calls:
        assert call[0] in {"get", "list_memberships"}
    for call in schemas.calls:
        assert call[0] == "paper_schema_readiness"
    for call in wiki.calls:
        assert call[0] == "status"
    for call in evidence.calls:
        assert call[0] == "l2s1_ready"

    assert workspaces.calls == [
        ("get", workspace.workspace_id),
        ("list_memberships", workspace.workspace_id),
    ]
    assert schemas.calls == [
        (
            "paper_schema_readiness",
            workspace.workspace_id,
            ("pa", "pb"),
        )
    ]
    assert wiki.calls == [("status", workspace.workspace_id)]
    assert evidence.calls == [
        ("l2s1_ready", "pa"),
        ("l2s1_ready", "pb"),
    ]
    # Mutating methods of every collaborator were never reached — the spy
    # wrappers raise AssertionError on the first such attempt.
    assert snapshot.visible_papers[0].schema_status == "ready"

    # Repeated grounding is stable: no extra state, no new calls beyond the
    # same read-only pattern (determinism).
    again = ground(workspace.workspace_id)
    assert again == snapshot


def test_grounding_no_schema_workspace_never_inspects_schema_storage(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = service.create(name="None", workspace_id="ws-ro-none").workspace
    paper = add_paper(session, paper_id="pn")
    service.add_paper(workspace.workspace_id, paper.id)

    workspaces = RecordingWorkspaces(WorkspaceService(session))
    schemas = RecordingSchemas(
        WorkspaceSchemaService(session, data_root=project_tmp_path)
    )
    wiki = RecordingWiki(WorkspaceWikiService(session, data_root=project_tmp_path))
    evidence = SpyingEvidence()

    snapshot = WorkspaceGroundingService(
        session,
        data_root=project_tmp_path,
        workspaces=workspaces,
        schemas=schemas,
        wiki=wiki,
        evidence=evidence,
    ).ground(workspace.workspace_id)

    assert snapshot.base_wiki.status == "unsupported"
    assert snapshot.visible_papers[0].schema_status == "disabled"
    # A no-schema Workspace's Schema storage must not even be inspected
    # (AC-007: no fallback/exposure for none mode).
    assert schemas.calls == []
    assert wiki.calls == [("status", workspace.workspace_id)]
    assert [call[0] for call in workspaces.calls] == ["get", "list_memberships"]


# ---------------------------------------------------------------------------
# AC-013: end-to-end with production services, mutating entry points trapped
# ---------------------------------------------------------------------------


def test_grounding_end_to_end_never_reaches_mutating_or_provider_paths(
    session, project_tmp_path, monkeypatch
):
    workspace = make_ready_workspace(session, project_tmp_path)
    none_ws = WorkspaceService(session).create(
        name="None", workspace_id="ws-ro-none-e2e"
    ).workspace
    add_paper(session, paper_id="pn-e2e")
    WorkspaceService(session).add_paper(none_ws.workspace_id, "pn-e2e")

    # Booby-trap every mutating lower-layer entry point and every
    # LLM/embedding provider factory the Knowledge path could reach.
    traps = {
        "transit_scholar.layer2.schema_extraction.api": (
            "extract_schema",
            "get_schema",
            "get_field",
        ),
        "transit_scholar.layer2.retrieval.api": (
            "build_retrieval",
            "search_bm25",
            "read_blocks",
        ),
        "transit_scholar.layer2.retrieval.providers": (
            "resolve_embedding_provider",
            "resolve_reranker_provider",
        ),
        "transit_scholar.layer2.wiki.application": (
            "create_production_wiki_composition",
        ),
        "transit_scholar.layer3.storage.provenance": (
            "record_build_provenance",
        ),
    }
    for module_name, names in traps.items():
        module = __import__(module_name, fromlist=["*"])
        for name in names:
            monkeypatch.setattr(module, name, _boom(name))
    monkeypatch.setattr(
        WorkspaceWikiService, "build", _boom("WorkspaceWikiService.build")
    )
    monkeypatch.setattr(
        WorkspaceWikiService, "search", _boom("WorkspaceWikiService.search")
    )

    service = WorkspaceGroundingService(session, data_root=project_tmp_path)
    ready = service.ground(workspace.workspace_id)
    assert ready.base_wiki.status == "ready"
    assert ready.schema_coverage.status == "complete"
    assert ready.capabilities.knowledge_access is True

    unsupported = service.ground(none_ws.workspace_id)
    assert unsupported.base_wiki.status == "unsupported"
    assert unsupported.schema_mode == "none"


# ---------------------------------------------------------------------------
# no files / no DB rows are ever mutated by Grounding
# ---------------------------------------------------------------------------


def _tree_snapshot(root) -> dict[str, tuple[int, int, str]]:
    """Deterministic file-tree fingerprint: (size, mtime_ns, sha256)."""
    entries: dict[str, tuple[int, int, str]] = {}
    if not root.exists():
        return entries
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            entries[str(path.relative_to(root))] = (
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return entries


def _db_snapshot(session):
    return (
        session.execute(select(func.count()).select_from(Workspace)).scalar_one(),
        session.execute(
            select(func.count()).select_from(WorkspacePaperMembership)
        ).scalar_one(),
        session.execute(select(func.count()).select_from(Paper)).scalar_one(),
        tuple(
            (row.id, row.revision, row.status, row.schema_mode)
            for row in session.execute(
                select(Workspace).order_by(Workspace.id)
            ).scalars()
        ),
    )


def test_repeated_grounding_mutates_no_files_and_no_db_rows(
    session, project_tmp_path
):
    workspace = make_ready_workspace(session, project_tmp_path)
    ground = WorkspaceGroundingService(
        session, data_root=project_tmp_path, evidence=SpyingEvidence()
    ).ground

    before_files = _tree_snapshot(project_tmp_path)
    before_db = _db_snapshot(session)

    first = ground(workspace.workspace_id)
    second = ground(workspace.workspace_id)
    third = ground(workspace.workspace_id)
    assert first == second == third

    after_files = _tree_snapshot(project_tmp_path)
    after_db = _db_snapshot(session)

    # No file was created, removed, resized or rewritten by Grounding.
    assert after_files == before_files
    # No database row appeared, disappeared or changed (identity/status/
    # revision/schema-mode all identical).
    assert after_db == before_db
    # The ORM session carries no pending INSERT/UPDATE/DELETE.
    assert not session.new
    assert not session.dirty
    assert not session.deleted


def test_grounding_missing_state_also_mutates_nothing(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-ro-missing")
    paper = add_paper(session, paper_id="p-raw-ro")
    service.add_paper(workspace.workspace_id, paper.id)

    ground = WorkspaceGroundingService(
        session, data_root=project_tmp_path, evidence=SpyingEvidence()
    ).ground
    before_files = _tree_snapshot(project_tmp_path)
    before_db = _db_snapshot(session)

    snapshot = ground(workspace.workspace_id)
    assert snapshot.schema_coverage.status == "missing"
    assert snapshot.base_wiki.status == "missing"
    assert [action.code for action in snapshot.recommended_actions]

    # The reporting of recommended actions must not itself mutate anything
    # (they are only reported, never executed, REQ-008).
    assert _tree_snapshot(project_tmp_path) == before_files
    assert _db_snapshot(session) == before_db
    assert not session.new and not session.dirty and not session.deleted
