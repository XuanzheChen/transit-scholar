"""Layer3 Stage1 Workspace-owned Base Wiki governance tests (T-002).

Proves:

- AC-008: two Workspaces with overlapping Papers resolve to distinct Base
  Wiki storage roots; a Wiki built for Workspace A is never returned for
  Workspace B (explicit missing/error outcomes, never cross-Workspace reuse);
- AC-009: no-schema Workspaces report Base Wiki build capability as
  unsupported and never construct a Wiki from another Schema/Workspace;
- AC-010: membership or current-Schema-run changes make a previously built
  Wiki fingerprint non-current -> derived ``stale`` with no persisted boolean
  flag;
- AC-011: unchanged authoritative inputs + intact artifacts -> ``ready``;
- REQ-001/AC-001..AC-006: ``ready`` is production completeness — a partial or
  failed ``WikiManifest`` build_status, a non-complete recorded provenance,
  or a missing/stale/incompatible mandatory persistent vector index maps to
  an explicit ``error`` outcome, and only a complete/current/valid snapshot
  with a current compatible vector index is ``ready`` and searchable;
- REQ-006/AC-024: the L2S3 ``WorkspaceWikiBuildService``/``WikiStore``/
  ``WikiService`` composition is reused through Workspace-specific storage
  roots;
- REQ-001/AC-001..AC-003: ``build()`` validates every member Paper's current
  Workspace Schema run through the same ``WorkspaceSchemaService`` governance
  boundary used by Schema reads BEFORE the L2S3 build consumes it — a
  binding-incompatible pointer or persisted run (schema_hash / schema_version
  mismatch) fails with the stable ``schema_binding_mismatch`` code and a
  missing/corrupt referenced run with ``schema_missing``, with no fallback
  construction and no L2S3 consumption;
- REQ-001/AC-004: fully compatible member runs keep building through the
  existing Workspace-specific L2S3 composition.

The L2S3 build composition is fully offline (fake structured-LLM client and
fake embedding provider, mirroring the L2S3 deterministic suite); Schema runs
are produced through the real L2S2 public API with an offline fake LLM.
"""

from __future__ import annotations

import json
import shutil

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    get_schema,
    get_schema_definition,
)
from transit_scholar.layer2.schema_extraction.persistence import RUN_MANIFEST_FILE
from transit_scholar.layer2.wiki import (
    PaperMetadata,
    WorkspaceWikiBuildService,
    create_production_wiki_composition,
)
from transit_scholar.layer2.wiki.store import WikiCorruptionError
from transit_scholar.layer3.schema import (
    SchemaBindingMismatchError,
    SchemaMissingError,
    WorkspaceSchemaService,
)
from transit_scholar.layer3.storage import read_build_provenance, workspace_layout
from transit_scholar.layer3.wiki import (
    WikiCorruptError,
    WikiEmptyMembershipError,
    WikiMissingError,
    WikiStaleError,
    WikiUnsupportedError,
    WorkspaceWikiService,
    derive_workspace_context,
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def add_paper(session, paper_id="p1", title="Shared Paper") -> Paper:
    paper = Paper(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def create_bound_workspace(session, name="Bound", workspace_id="ws-1"):
    return WorkspaceService(session).create(
        name=name, schema_definition=DEFINITION, workspace_id=workspace_id
    ).workspace


def create_none_workspace(session, name="No Schema", workspace_id="ws-none"):
    return WorkspaceService(session).create(name=name, workspace_id=workspace_id).workspace


def _offline_build_factory(session):
    """A build-service factory mirroring the L3 default composition, with the
    LLM/embedding/metadata collaborators replaced by deterministic fakes.

    Schema instances are still loaded from each Workspace's OWN L2S2 storage
    root (workspace boundary preserved); only the Wiki composition providers
    are faked for offline determinism.
    """

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


def wiki_service(session, project_tmp_path, *, factory=None):
    return WorkspaceWikiService(
        session,
        data_root=project_tmp_path,
        build_service_factory=(
            factory if factory is not None else _offline_build_factory(session)
        ),
    )


def materialize_all(session, schema_service, workspaces, paper):
    for workspace in workspaces:
        schema_service.materialize(
            workspace.workspace_id, paper.id, llm_client=FakeLLMProvider()
        )


# ---------------------------------------------------------------------------
# AC-008: Base Wiki storage isolated per Workspace
# ---------------------------------------------------------------------------


def test_two_workspaces_with_overlapping_paper_have_distinct_wiki_roots(
    session, project_tmp_path
):
    paper = add_paper(session, paper_id="pa", title="Bus Control RL Shared Paper")
    ws_a = create_bound_workspace(session, "Workspace A", workspace_id="ws-aa")
    ws_b = create_bound_workspace(session, "Workspace B", workspace_id="ws-bb")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    materialize_all(session, schema, [ws_a, ws_b], paper)

    wiki = wiki_service(session, project_tmp_path)
    outcome_a = wiki.build(ws_a.workspace_id)
    assert (project_tmp_path / "layer3" / "workspaces" / ws_a.workspace_id / "wiki").exists()

    layout_a = workspace_layout(ws_a.workspace_id, data_root=project_tmp_path)
    layout_b = workspace_layout(ws_b.workspace_id, data_root=project_tmp_path)
    assert layout_a.wiki_dir != layout_b.wiki_dir
    assert outcome_a.provenance.workspace_id == ws_a.workspace_id

    # Workspace A: ready and searchable.
    assert wiki.status(ws_a.workspace_id).status == "ready"
    hits_a = wiki.search(ws_a.workspace_id, "bus control", mode="lexical").hits
    assert hits_a
    page_a = hits_a[0].object_id

    # Workspace B: missing its own snapshot -> explicit missing, no reuse of A.
    status_b = wiki.status(ws_b.workspace_id)
    assert status_b.status == "missing"
    assert status_b.error_code == "snapshot_missing"
    with pytest.raises(WikiMissingError):
        wiki.search(ws_b.workspace_id, "bus control")

    # After B's own build, the same Paper resolves to a different
    # Workspace-scoped Wiki identity (page ids embed the Workspace), so the
    # same Paper produces independent Wiki content per Workspace (AC-008).
    wiki.build(ws_b.workspace_id)
    assert wiki.status(ws_b.workspace_id).status == "ready"
    hits_b = wiki.search(ws_b.workspace_id, "bus control").hits
    assert hits_b
    assert hits_b[0].object_id != page_a


def test_wiki_snapshot_of_workspace_a_is_never_returned_for_workspace_b(
    session, project_tmp_path
):
    paper = add_paper(session, paper_id="pa", title="Isolation Shared Paper")
    ws_a = create_bound_workspace(session, "A", workspace_id="ws-aa")
    ws_b = create_bound_workspace(session, "B", workspace_id="ws-bb")
    service = WorkspaceService(session)
    service.add_paper(ws_a.workspace_id, paper.id)
    service.add_paper(ws_b.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    materialize_all(session, schema, [ws_a, ws_b], paper)

    wiki = wiki_service(session, project_tmp_path)
    wiki.build(ws_a.workspace_id)

    layout_a = workspace_layout(ws_a.workspace_id, data_root=project_tmp_path)
    layout_b = workspace_layout(ws_b.workspace_id, data_root=project_tmp_path)

    # Simulate contamination: A's snapshot files are copied into B's boundary.
    assert not layout_b.wiki_dir.exists()
    shutil.copytree(layout_a.wiki_dir, layout_b.wiki_dir)

    # B's own store refuses the foreign snapshot: manifest context mismatch.
    record_b = WorkspaceService(session).get(ws_b.workspace_id)
    memberships_b = WorkspaceService(session).list_memberships(ws_b.workspace_id)
    context_b = derive_workspace_context(record_b, memberships_b)
    with pytest.raises(WikiCorruptionError):
        layout_b.wiki_store(context_b).get_manifest()

    # B's derived status is an explicit workspace-mismatch error, never
    # ready/stale-from-A (REQ-005 boundary enforcement).
    status_b = wiki.status(ws_b.workspace_id)
    assert status_b.status == "error"
    assert status_b.error_code == "workspace_mismatch"
    with pytest.raises(WikiCorruptError):
        wiki.search(ws_b.workspace_id, "isolation")


# ---------------------------------------------------------------------------
# AC-009: no-schema Workspace Base Wiki build is unsupported
# ---------------------------------------------------------------------------


def test_no_schema_workspace_wiki_build_is_unsupported(session, project_tmp_path):
    paper = add_paper(session)
    ws = create_none_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)

    wiki = wiki_service(session, project_tmp_path)
    derived = wiki.status(ws.workspace_id)
    assert derived.status == "unsupported"

    capability = wiki.capability(ws.workspace_id)
    assert capability.build_supported is False
    assert capability.read_supported is False
    assert capability.status == "unsupported"

    with pytest.raises(WikiUnsupportedError) as build_error:
        wiki.build(ws.workspace_id)
    assert build_error.value.code == "wiki_unsupported"
    with pytest.raises(WikiUnsupportedError) as search_error:
        wiki.search(ws.workspace_id, "anything")
    assert search_error.value.code == "wiki_unsupported"

    # Nothing was ever constructed for the no-schema Workspace.
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    assert not layout.derived_dir.exists()


def test_schema_bound_workspace_without_members_cannot_build_wiki(
    session, project_tmp_path
):
    ws = create_bound_workspace(session, "Empty Members")
    wiki = wiki_service(session, project_tmp_path)

    assert wiki.status(ws.workspace_id).status == "missing"
    with pytest.raises(WikiEmptyMembershipError) as empty:
        wiki.build(ws.workspace_id)
    assert empty.value.code == "empty_membership"
    assert not workspace_layout(ws.workspace_id, data_root=project_tmp_path).derived_dir.exists()


# ---------------------------------------------------------------------------
# AC-010: input changes -> derived stale (no persisted boolean flag)
# ---------------------------------------------------------------------------


def test_membership_change_makes_built_wiki_stale(session, project_tmp_path):
    paper_one = add_paper(session, paper_id="p1", title="First Paper")
    ws = create_bound_workspace(session)
    service = WorkspaceService(session)
    service.add_paper(ws.workspace_id, paper_one.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper_one.id, llm_client=FakeLLMProvider())

    wiki = wiki_service(session, project_tmp_path)
    outcome = wiki.build(ws.workspace_id)
    assert wiki.status(ws.workspace_id).status == "ready"

    # Add a second member Paper (authoritative membership change).
    paper_two = add_paper(session, paper_id="p2", title="Second Paper")
    service.add_paper(ws.workspace_id, paper_two.id)

    stale = wiki.status(ws.workspace_id)
    assert stale.status == "stale"
    assert stale.error_code == "input_fingerprint_mismatch"
    assert stale.recorded_fingerprint == outcome.fingerprint
    assert stale.fingerprint != outcome.fingerprint

    # Rebuild with the new membership -> current again. The newly added Paper
    # must first have Workspace-owned Schema content (build input requirement).
    schema.materialize(ws.workspace_id, paper_two.id, llm_client=FakeLLMProvider())
    still_stale = wiki.status(ws.workspace_id)
    assert still_stale.status == "stale"
    assert still_stale.error_code == "input_fingerprint_mismatch"
    rebuilt = wiki.build(ws.workspace_id)
    assert rebuilt.fingerprint == still_stale.fingerprint
    assert wiki.status(ws.workspace_id).status == "ready"

    # Removing the Paper flips the Wiki stale again (AC-010 semantics).
    service.remove_paper(ws.workspace_id, paper_two.id)
    assert wiki.status(ws.workspace_id).status == "stale"


def test_current_schema_run_change_makes_built_wiki_stale(session, project_tmp_path):
    paper = add_paper(session, title="Schema Change Paper")
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())

    wiki = wiki_service(session, project_tmp_path)
    outcome = wiki.build(ws.workspace_id)
    assert wiki.status(ws.workspace_id).status == "ready"

    # A new current Schema run (new run identity) for the same member Paper.
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())

    stale = wiki.status(ws.workspace_id)
    assert stale.status == "stale"
    assert stale.error_code == "input_fingerprint_mismatch"
    assert stale.fingerprint != outcome.fingerprint


def test_wiki_read_rejects_stale_snapshot_explicitly(session, project_tmp_path):
    paper = add_paper(session, title="Stale Read Paper")
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    wiki = wiki_service(session, project_tmp_path)
    wiki.build(ws.workspace_id)
    assert wiki.search(ws.workspace_id, "stale read").hits

    # Change membership without rebuilding.
    second = add_paper(session, paper_id="p2", title="Member Two")
    WorkspaceService(session).add_paper(ws.workspace_id, second.id)

    with pytest.raises(WikiStaleError) as stale_read:
        wiki.search(ws.workspace_id, "stale read")
    assert stale_read.value.code == "wiki_stale"


# ---------------------------------------------------------------------------
# AC-011: unchanged inputs + intact artifacts -> current/ready
# ---------------------------------------------------------------------------


def test_unchanged_inputs_preserve_wiki_freshness(session, project_tmp_path):
    paper = add_paper(session, title="Unchanged Inputs Paper")
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)

    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())

    wiki = wiki_service(session, project_tmp_path)
    outcome = wiki.build(ws.workspace_id)
    first = wiki.status(ws.workspace_id)
    assert first.status == "ready"
    assert first.fingerprint == outcome.fingerprint
    assert first.recorded_fingerprint == outcome.fingerprint

    # Re-running status (a read-only Grounding-style derivation) stays ready.
    second = wiki.status(ws.workspace_id)
    assert second.status == "ready"
    assert second.fingerprint == first.fingerprint
    assert second.build_revision == 1

    # A repeat build records a higher build revision and stays current.
    rebuilt = wiki.build(ws.workspace_id)
    assert rebuilt.provenance.build_revision == 2
    assert wiki.status(ws.workspace_id).status == "ready"


def test_corrupted_artifacts_are_not_ready(session, project_tmp_path):
    paper = add_paper(session, title="Integrity Paper")
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    wiki = wiki_service(session, project_tmp_path)
    wiki.build(ws.workspace_id)
    assert wiki.status(ws.workspace_id).status == "ready"

    # Removing a mandatory snapshot file breaks the existing integrity checks.
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    (layout.wiki_dir / "entities.jsonl").unlink()
    status = wiki.status(ws.workspace_id)
    assert status.status == "error"
    assert status.error_code == "wiki_corrupt"


def test_provenance_recorded_inside_workspace_wiki_boundary(session, project_tmp_path):
    paper = add_paper(session, title="Provenance Paper")
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())

    wiki = wiki_service(session, project_tmp_path)
    outcome = wiki.build(ws.workspace_id)

    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    assert (layout.wiki_dir / "provenance.json").is_file()
    provenance = read_build_provenance(layout.wiki_dir)
    assert provenance is not None
    assert provenance.input_fingerprint == outcome.fingerprint
    assert provenance.workspace_id == ws.workspace_id
    # No boolean readiness flag is ever persisted (REQ-007).
    raw = (layout.wiki_dir / "provenance.json").read_text(encoding="utf-8")
    assert "wiki_stale" not in raw
    assert "wiki_ready" not in raw


def test_rebuild_recovers_from_corrupt_provenance(session, project_tmp_path):
    paper = add_paper(session, title="Recovery Paper")
    ws = create_bound_workspace(session)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())
    wiki = wiki_service(session, project_tmp_path)
    wiki.build(ws.workspace_id)

    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    (layout.wiki_dir / "provenance.json").write_text("{not json", encoding="utf-8")
    assert wiki.status(ws.workspace_id).status == "error"

    rebuilt = wiki.build(ws.workspace_id)
    assert rebuilt.provenance.build_revision == 1
    assert wiki.status(ws.workspace_id).status == "ready"


# ---------------------------------------------------------------------------
# REQ-001 / AC-001..AC-006: ready is production completeness
# ---------------------------------------------------------------------------


def _built_wiki(session, project_tmp_path, *, workspace_id="ws-ac", paper_id="ac-p"):
    """A bound Workspace with one materialized member and a built Base Wiki."""
    paper = add_paper(session, paper_id=paper_id, title="Completeness Paper")
    ws = create_bound_workspace(session, workspace_id=workspace_id)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        ws.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    wiki = wiki_service(session, project_tmp_path)
    wiki.build(ws.workspace_id)
    assert wiki.status(ws.workspace_id).status == "ready"
    return wiki, ws


def _rewrite_json(path, changes):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _index_path(project_tmp_path, workspace_id):
    layout = workspace_layout(workspace_id, data_root=project_tmp_path)
    return layout.wiki_dir / "index" / "package_b_index.json"


def test_partial_manifest_is_not_ready(session, project_tmp_path):
    """AC-001: partial WikiManifest build_status never derives ready."""
    wiki, ws = _built_wiki(session, project_tmp_path)
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    _rewrite_json(layout.wiki_dir / "manifest.json", {"build_status": "partial"})

    status = wiki.status(ws.workspace_id)
    assert status.status == "error"
    assert status.error_code == "manifest_build_partial"
    assert status.manifest_status == "partial"
    with pytest.raises(WikiCorruptError) as corrupt:
        wiki.search(ws.workspace_id, "completeness")
    assert corrupt.value.code == "wiki_corrupt"


def test_failed_manifest_is_not_ready(session, project_tmp_path):
    """AC-002: failed WikiManifest build_status never derives ready."""
    wiki, ws = _built_wiki(session, project_tmp_path, workspace_id="ws-ac2")
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    _rewrite_json(layout.wiki_dir / "manifest.json", {"build_status": "failed"})

    status = wiki.status(ws.workspace_id)
    assert status.status == "error"
    assert status.error_code == "manifest_build_failed"
    assert status.manifest_status == "failed"
    with pytest.raises(WikiCorruptError) as corrupt:
        wiki.search(ws.workspace_id, "completeness")
    assert corrupt.value.code == "wiki_corrupt"


@pytest.mark.parametrize("provenance_status", ["partial", "failed"])
def test_non_complete_provenance_is_not_ready(session, project_tmp_path, provenance_status):
    """AC-003: non-complete recorded provenance is never ready, even when the
    authoritative JSON/JSONL source files are readable and the fingerprint
    matches the current inputs."""
    wiki, ws = _built_wiki(
        session, project_tmp_path, workspace_id="ws-ac3", paper_id="ac-p3"
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    _rewrite_json(
        layout.wiki_dir / "provenance.json", {"build_status": provenance_status}
    )

    status = wiki.status(ws.workspace_id)
    assert status.status == "error"
    assert status.error_code == "build_provenance_incomplete"
    # The authoritative source files are still readable (integrity checks
    # would pass); the provenance completeness gate alone blocks ready.
    assert (layout.wiki_dir / "manifest.json").is_file()
    assert (layout.wiki_dir / "pages.jsonl").is_file()
    with pytest.raises(WikiCorruptError) as corrupt:
        wiki.search(ws.workspace_id, "completeness")
    assert corrupt.value.code == "wiki_corrupt"


def test_missing_vector_index_is_explicit_error(session, project_tmp_path):
    """AC-004: an absent mandatory persistent vector index is an explicit
    stable error outcome and the Wiki is never exposed as current/ready."""
    wiki, ws = _built_wiki(session, project_tmp_path, workspace_id="ws-ac4")
    index_path = _index_path(project_tmp_path, ws.workspace_id)
    assert index_path.is_file()
    index_path.unlink()

    status = wiki.status(ws.workspace_id)
    assert status.status == "error"
    assert status.error_code == "vector_index_missing"
    with pytest.raises(WikiCorruptError) as corrupt:
        wiki.search(ws.workspace_id, "completeness")
    assert corrupt.value.code == "wiki_corrupt"


def test_stale_vector_index_is_explicit_error(session, project_tmp_path):
    """AC-005: a vector index stale for the current authoritative Wiki source
    fingerprint is an explicit error, never ready."""
    wiki, ws = _built_wiki(session, project_tmp_path, workspace_id="ws-ac5")
    _rewrite_json(_index_path(project_tmp_path, ws.workspace_id), {"source_fingerprint": "stale-forged"})

    status = wiki.status(ws.workspace_id)
    assert status.status == "error"
    assert status.error_code == "vector_index_stale"


def test_vector_index_incompatible_dimensions_is_error(session, project_tmp_path):
    """AC-005: invalid vector dimensions/metadata are an explicit error."""
    wiki, ws = _built_wiki(session, project_tmp_path, workspace_id="ws-ac5b")
    _rewrite_json(
        _index_path(project_tmp_path, ws.workspace_id),
        {"vector_metadata": {"provider": "test", "model": "test", "dimension": 3}},
    )

    status = wiki.status(ws.workspace_id)
    assert status.status == "error"
    assert status.error_code == "vector_index_incompatible"


def test_vector_index_incomplete_coverage_is_error(session, project_tmp_path):
    """AC-005: missing required Page/Entity vector coverage is an explicit
    error."""
    wiki, ws = _built_wiki(session, project_tmp_path, workspace_id="ws-ac5c")
    index_path = _index_path(project_tmp_path, ws.workspace_id)
    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["vectors"], "the offline build writes page vectors"
    data["vectors"] = data["vectors"][1:]
    index_path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    status = wiki.status(ws.workspace_id)
    assert status.status == "error"
    assert status.error_code == "vector_index_missing"


def test_complete_current_valid_wiki_is_ready_and_searchable(session, project_tmp_path):
    """AC-006: matching provenance, complete provenance/manifest build status,
    structurally valid assets and a current compatible vector index with
    complete required coverage derive ready and remain searchable."""
    wiki, ws = _built_wiki(session, project_tmp_path, workspace_id="ws-ac6")
    index_path = _index_path(project_tmp_path, ws.workspace_id)
    assert index_path.is_file()

    status = wiki.status(ws.workspace_id)
    assert status.status == "ready"
    assert status.error_code is None
    assert status.manifest_status == "complete"

    hits = wiki.search(ws.workspace_id, "completeness", mode="lexical").hits
    assert hits

    # The L2S3 provider-free vector audit independently confirms the index.
    from transit_scholar.layer2.wiki import audit_vector_index_readonly

    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    record = WorkspaceService(session).get(ws.workspace_id)
    memberships = WorkspaceService(session).list_memberships(ws.workspace_id)
    context = derive_workspace_context(record, memberships)
    store = layout.wiki_store(context)
    assert audit_vector_index_readonly(store) == []


# ---------------------------------------------------------------------------
# REQ-001 / AC-001..AC-004: build consumes only governed, binding-compatible
# Workspace Schema runs
# ---------------------------------------------------------------------------


def _build_must_not_run_factory(workspace_id, layout):
    """A build-service factory that MUST never be invoked: reaching it proves
    L2S3 would consume an ungoverned Schema run (REQ-001)."""
    raise AssertionError(
        "L2S3 build composition must not run for invalid Workspace Schema "
        "inputs (REQ-001/AC-001..AC-003)"
    )


def rewrite_pointer(layout, paper_id, changes):
    """Rewrite ``current.json`` with the given field changes (tamper helper)."""
    path = layout.schema_storage().current_path(paper_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def rewrite_run_manifest(layout, paper_id, run_id, changes):
    """Rewrite ``run_manifest.json`` with the given field changes.

    The run manifest is not digest-protected by L2S2 (only instance, manifest
    and report are), so a changed identity stays a READABLE run — exactly the
    AC-001/AC-002 binding-incompatibility case for the Wiki build.
    """
    path = layout.schemas_dir / paper_id / "runs" / run_id / RUN_MANIFEST_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _schema_input_workspace(session, project_tmp_path, *, workspace_id, paper_id):
    """A bound Workspace with one member Paper and a materialized current run."""
    paper = add_paper(session, paper_id=paper_id, title="Build Governance Paper")
    ws = create_bound_workspace(session, workspace_id=workspace_id)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        ws.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    return ws


def test_build_rejects_pointer_schema_hash_mismatch(session, project_tmp_path):
    """AC-001: a current pointer whose schema_hash disagrees with the Workspace
    binding makes build() fail BEFORE the L2S3 build consumes the run, with
    the stable ``schema_binding_mismatch`` code and no Wiki artifacts."""
    ws = _schema_input_workspace(
        session, project_tmp_path, workspace_id="ws-bg-ptrhash", paper_id="bg-ptrhash-p"
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    rewrite_pointer(layout, "bg-ptrhash-p", {"schema_hash": "f" * 64})

    # The L2S3 build composition must never be constructed/consumed.
    wiki = wiki_service(session, project_tmp_path, factory=_build_must_not_run_factory)
    with pytest.raises(SchemaBindingMismatchError) as mismatch:
        wiki.build(ws.workspace_id)
    assert mismatch.value.code == "schema_binding_mismatch"
    # Nothing was built and no snapshot/provenance exists anywhere
    # (AC-001: fail before L2S3 consumes the SchemaInstance).
    assert not (layout.wiki_dir / "manifest.json").exists()
    assert not (layout.wiki_dir / "provenance.json").exists()


def test_build_rejects_run_manifest_schema_hash_mismatch(session, project_tmp_path):
    """AC-001: a READABLE persisted run whose run-manifest schema_hash
    disagrees with the Workspace binding rejects the Wiki build with the
    stable ``schema_binding_mismatch`` code before L2S3 consumption."""
    paper_id = "bg-runhash-p"
    ws = _schema_input_workspace(
        session, project_tmp_path, workspace_id="ws-bg-runhash", paper_id=paper_id
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    rewrite_run_manifest(layout, paper_id, run_id, {"schema_hash": "f" * 64})

    wiki = wiki_service(session, project_tmp_path, factory=_build_must_not_run_factory)
    with pytest.raises(SchemaBindingMismatchError) as mismatch:
        wiki.build(ws.workspace_id)
    assert mismatch.value.code == "schema_binding_mismatch"
    assert not (layout.wiki_dir / "manifest.json").exists()
    assert not (layout.wiki_dir / "provenance.json").exists()


def test_build_rejects_run_manifest_schema_version_mismatch(session, project_tmp_path):
    """AC-002: a READABLE persisted run whose run-manifest schema_version
    disagrees with the Workspace binding rejects the Wiki build with the
    stable ``schema_binding_mismatch`` code before L2S3 consumption."""
    paper_id = "bg-runver-p"
    ws = _schema_input_workspace(
        session, project_tmp_path, workspace_id="ws-bg-runver", paper_id=paper_id
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    rewrite_run_manifest(layout, paper_id, run_id, {"schema_version": "0.0-forged"})

    wiki = wiki_service(session, project_tmp_path, factory=_build_must_not_run_factory)
    with pytest.raises(SchemaBindingMismatchError) as version_error:
        wiki.build(ws.workspace_id)
    assert version_error.value.code == "schema_binding_mismatch"
    assert not (layout.wiki_dir / "manifest.json").exists()
    assert not (layout.wiki_dir / "provenance.json").exists()


def test_build_rejects_pointer_to_missing_referenced_run(session, project_tmp_path):
    """AC-003: current.json exists but the referenced run was removed ->
    build() fails explicitly with the stable ``schema_missing`` code; no
    fallback and no L2S3 consumption."""
    paper_id = "bg-missing-p"
    ws = _schema_input_workspace(
        session, project_tmp_path, workspace_id="ws-bg-missing", paper_id=paper_id
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    assert (layout.schemas_dir / paper_id / "current.json").is_file()
    shutil.rmtree(layout.schemas_dir / paper_id / "runs" / run_id)

    wiki = wiki_service(session, project_tmp_path, factory=_build_must_not_run_factory)
    with pytest.raises(SchemaMissingError) as missing:
        wiki.build(ws.workspace_id)
    assert missing.value.code == "schema_missing"
    assert not (layout.wiki_dir / "manifest.json").exists()
    assert not (layout.wiki_dir / "provenance.json").exists()


def test_build_rejects_corrupt_referenced_run(session, project_tmp_path):
    """AC-003: a corrupt referenced run (unreadable instance JSON) fails the
    Wiki build explicitly with the stable ``schema_missing`` code — the
    pointer alone never authorizes construction."""
    paper_id = "bg-corrupt-p"
    ws = _schema_input_workspace(
        session, project_tmp_path, workspace_id="ws-bg-corrupt", paper_id=paper_id
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    instance_path = (
        layout.schemas_dir / paper_id / "runs" / run_id / "schema_instance.json"
    )
    instance_path.write_text("{not json", encoding="utf-8")

    wiki = wiki_service(session, project_tmp_path, factory=_build_must_not_run_factory)
    with pytest.raises(SchemaMissingError) as corrupt:
        wiki.build(ws.workspace_id)
    assert corrupt.value.code == "schema_missing"
    assert not (layout.wiki_dir / "manifest.json").exists()
    assert not (layout.wiki_dir / "provenance.json").exists()


def test_compatible_runs_still_build_through_workspace_composition(
    session, project_tmp_path
):
    """AC-004: when every member's current run is readable and fully
    compatible with the immutable Workspace binding, build() keeps driving
    the existing L2S3 composition through the Workspace-specific storage
    boundary and records the validated input fingerprint provenance."""
    paper = add_paper(session, paper_id="bg-ok-p", title="Compatible Build Paper")
    ws = create_bound_workspace(session, workspace_id="ws-bg-ok")
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    schema = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schema.materialize(ws.workspace_id, paper.id, llm_client=FakeLLMProvider())

    wiki = wiki_service(session, project_tmp_path)
    outcome = wiki.build(ws.workspace_id)

    assert outcome.result.manifest.build_status == "complete"
    assert outcome.provenance.workspace_id == ws.workspace_id
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    # Built strictly inside this Workspace's Wiki boundary (AC-004).
    assert (layout.wiki_dir / "manifest.json").is_file()
    assert (layout.wiki_dir / "provenance.json").is_file()
    recorded = read_build_provenance(layout.wiki_dir)
    assert recorded is not None
    assert recorded.input_fingerprint == outcome.fingerprint
    # The recorded input identities are exactly the validated compatible
    # current run identities (REQ-001: fingerprint built from governed runs).
    pointer = layout.schema_storage().read_current(paper.id)
    assert recorded.schema_runs == {
        paper.id: {
            "run_id": pointer.run_id,
            "schema_hash": pointer.schema_hash,
            "status": pointer.status,
        }
    }
    assert wiki.status(ws.workspace_id).status == "ready"


def test_build_rejects_invalid_run_even_after_prior_successful_build(
    session, project_tmp_path
):
    """REQ-001: an existing valid Wiki never lets a later invalid Schema run
    through — a re-build over a tampered current pointer fails explicitly and
    leaves the previous snapshot untouched."""
    paper_id = "bg-regress-p"
    ws = _schema_input_workspace(
        session, project_tmp_path, workspace_id="ws-bg-regress", paper_id=paper_id
    )
    wiki = wiki_service(session, project_tmp_path)
    first = wiki.build(ws.workspace_id)
    assert wiki.status(ws.workspace_id).status == "ready"

    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    rewrite_pointer(layout, paper_id, {"schema_hash": "f" * 64})

    with pytest.raises(SchemaBindingMismatchError) as mismatch:
        wiki.build(ws.workspace_id)
    assert mismatch.value.code == "schema_binding_mismatch"
    # The previous valid snapshot and its provenance are preserved intact.
    assert (layout.wiki_dir / "manifest.json").is_file()
    recorded = read_build_provenance(layout.wiki_dir)
    assert recorded is not None
    assert recorded.input_fingerprint == first.fingerprint


# ---------------------------------------------------------------------------
# REQ-002 / AC-005..AC-007: freshness is derived from validated current runs
# ---------------------------------------------------------------------------


def _freshness_ready_wiki(session, project_tmp_path, *, workspace_id, paper_id):
    """A bound Workspace with one materialized member and a READY Base Wiki."""
    paper = add_paper(session, paper_id=paper_id, title="Freshness Paper")
    ws = create_bound_workspace(session, workspace_id=workspace_id)
    WorkspaceService(session).add_paper(ws.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        ws.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    wiki = wiki_service(session, project_tmp_path)
    outcome = wiki.build(ws.workspace_id)
    assert wiki.status(ws.workspace_id).status == "ready"
    return wiki, ws, paper.id, outcome


def _assert_schema_input_invalid(workspace_id, wiki, outcome, status):
    """Shared AC-005..AC-007 assertions: non-ready with the stable derived
    ``schema_input_invalid`` code and explicit degraded reads."""
    assert status.status != "ready"
    assert status.status == "stale"
    assert status.error_code == "schema_input_invalid"
    assert status.recorded_fingerprint == outcome.fingerprint
    assert status.fingerprint != outcome.fingerprint
    # The read path degrades explicitly; it never serves non-current facts.
    with pytest.raises(WikiStaleError) as stale:
        wiki.search(workspace_id, "freshness")
    assert stale.value.code == "wiki_stale"


def test_run_manifest_hash_tamper_invalidates_ready_wiki(session, project_tmp_path):
    """AC-005: after a valid Wiki is ready, changing ONLY the persisted
    run-manifest schema_hash (current.json byte-identical) makes status()
    non-ready with the stable ``schema_input_invalid`` code — the pointer
    alone never preserves freshness (REQ-002 / AC-007)."""
    wiki, ws, paper_id, outcome = _freshness_ready_wiki(
        session, project_tmp_path, workspace_id="ws-fr-hash", paper_id="fr-hash-p"
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    current_path = layout.schemas_dir / paper_id / "current.json"
    current_bytes = current_path.read_bytes()
    rewrite_run_manifest(layout, paper_id, run_id, {"schema_hash": "f" * 64})
    # The pointer never changed: raw pointer identity must not authorize
    # readiness (AC-007 / C-002).
    assert current_path.read_bytes() == current_bytes

    _assert_schema_input_invalid(
        ws.workspace_id, wiki, outcome, wiki.status(ws.workspace_id)
    )

    # The governed identity derivation itself refuses the pointer identity for
    # the binding-incompatible run (AC-007).
    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    assert schemas.current_run_identities(ws.workspace_id, [paper_id]) == {
        paper_id: None
    }

    # Provenance and snapshot artifacts are untouched; only the validated
    # input derivation changed the freshness outcome.
    recorded = read_build_provenance(layout.wiki_dir)
    assert recorded is not None
    assert recorded.input_fingerprint == outcome.fingerprint
    assert (layout.wiki_dir / "manifest.json").is_file()


def test_run_manifest_version_mismatch_invalidates_ready_wiki(
    session, project_tmp_path
):
    """AC-005/AC-006 (version repeat): a READABLE run whose run-manifest
    schema_version no longer matches the immutable Workspace binding
    invalidates readiness while current.json stays byte-identical."""
    wiki, ws, paper_id, outcome = _freshness_ready_wiki(
        session, project_tmp_path, workspace_id="ws-fr-ver", paper_id="fr-ver-p"
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    current_path = layout.schemas_dir / paper_id / "current.json"
    current_bytes = current_path.read_bytes()
    rewrite_run_manifest(layout, paper_id, run_id, {"schema_version": "0.0-forged"})
    assert current_path.read_bytes() == current_bytes

    _assert_schema_input_invalid(
        ws.workspace_id, wiki, outcome, wiki.status(ws.workspace_id)
    )


def test_missing_current_run_invalidates_ready_wiki(session, project_tmp_path):
    """AC-006: the persisted current run disappears while current.json remains
    present -> the previously ready Wiki is no longer ready (stable
    ``schema_input_invalid`` code; the pointer never preserves freshness)."""
    wiki, ws, paper_id, outcome = _freshness_ready_wiki(
        session, project_tmp_path, workspace_id="ws-fr-miss", paper_id="fr-miss-p"
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    assert (layout.schemas_dir / paper_id / "current.json").is_file()
    shutil.rmtree(layout.schemas_dir / paper_id / "runs" / run_id)

    _assert_schema_input_invalid(
        ws.workspace_id, wiki, outcome, wiki.status(ws.workspace_id)
    )

    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    assert schemas.current_run_identities(ws.workspace_id, [paper_id]) == {
        paper_id: None
    }


def test_corrupt_current_run_invalidates_ready_wiki(session, project_tmp_path):
    """AC-006: a corrupt/unreadable referenced run (invalid instance JSON)
    while current.json stays present -> the previously ready Wiki is no longer
    ready with the stable ``schema_input_invalid`` code."""
    wiki, ws, paper_id, outcome = _freshness_ready_wiki(
        session, project_tmp_path, workspace_id="ws-fr-cor", paper_id="fr-cor-p"
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    current_path = layout.schemas_dir / paper_id / "current.json"
    current_bytes = current_path.read_bytes()
    instance_path = (
        layout.schemas_dir / paper_id / "runs" / run_id / "schema_instance.json"
    )
    instance_path.write_text("{not json", encoding="utf-8")
    assert current_path.read_bytes() == current_bytes

    _assert_schema_input_invalid(
        ws.workspace_id, wiki, outcome, wiki.status(ws.workspace_id)
    )


def test_restored_compatible_run_returns_the_same_wiki_to_ready(
    session, project_tmp_path
):
    """REQ-002 round-trip: restoring the tampered run-manifest identity makes
    the unchanged snapshot ready again — freshness tracks the validated
    current run, and a complete/current/compatible Wiki stays ready (AC-011 /
    AC-010 normal-state regression)."""
    wiki, ws, paper_id, outcome = _freshness_ready_wiki(
        session, project_tmp_path, workspace_id="ws-fr-restore", paper_id="fr-rest-p"
    )
    layout = workspace_layout(ws.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper_id).run_id
    manifest_path = (
        layout.schemas_dir / paper_id / "runs" / run_id / RUN_MANIFEST_FILE
    )
    binding = WorkspaceService(session).get(ws.workspace_id).schema_binding
    assert binding is not None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["schema_hash"] = binding.schema_hash
    data["schema_version"] = binding.schema_version
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    status = wiki.status(ws.workspace_id)
    assert status.status == "ready"
    assert status.error_code is None
    assert status.fingerprint == outcome.fingerprint
    assert status.recorded_fingerprint == outcome.fingerprint