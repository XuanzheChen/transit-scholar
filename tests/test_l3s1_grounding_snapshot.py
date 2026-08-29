"""Layer3 Stage1 read-only Workspace Grounding snapshot tests (T-004).

Proves AC-012 (a normalized snapshot with identity/status/revision, visible
Papers, per-Paper asset availability, schema mode/binding, Schema coverage,
Base Wiki status and capabilities) and REQ-008 (read-only, deterministic
normalization) across every required Grounding state:

- ready: complete Schema coverage + fresh Base Wiki;
- missing: bound Workspace without Schema runs / without a Wiki snapshot;
- partial: only some member Papers have Workspace Schema runs;
- stale: recorded Wiki fingerprint no longer matches current inputs;
- unsupported-no-schema: none-mode Workspace (no Schema/Wiki capability);
- archived: memberships and files preserved, knowledge access unavailable;
- error: Base Wiki artifacts/provenance fail integrity checks;
- deleting/deleted: lifecycle tombstones with empty membership;
- workspace_not_found: explicit stable error, no snapshot.
"""

from __future__ import annotations

import json
import uuid

import pytest

from transit_scholar.db.models import Paper
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    get_schema_definition,
)
from transit_scholar.layer2.wiki import (
    PaperMetadata,
    WorkspaceWikiBuildService,
    create_production_wiki_composition,
)
from transit_scholar.layer3.grounding import (
    ACTION_BUILD_BASE_WIKI,
    ACTION_MATERIALIZE_SCHEMA_RUNS,
    ACTION_REBUILD_BASE_WIKI,
    ACTION_REPAIR_BASE_WIKI,
    GroundedPaper,
    GroundedWorkspace,
    SchemaCoverage,
    WorkspaceCapabilities,
    WorkspaceGroundingService,
)
from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer3.schema import WorkspaceSchemaService
from transit_scholar.layer3.storage import workspace_layout
from transit_scholar.layer3.workspace import (
    WorkspaceNotFoundError,
    WorkspaceService,
    compute_schema_hash,
)
from transit_scholar.layer3.wiki import WorkspaceWikiService

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


def add_paper(session, paper_id="p1", title="Grounding Paper") -> Paper:
    paper = Paper(id=paper_id, title=title, status="active")
    session.add(paper)
    session.flush()
    return paper


def create_bound_workspace(session, name="Bound", workspace_id="ws-bound"):
    return WorkspaceService(session).create(
        name=name, schema_definition=DEFINITION, workspace_id=workspace_id
    ).workspace


def create_none_workspace(session, name="No Schema", workspace_id="ws-none"):
    return WorkspaceService(session).create(name=name, workspace_id=workspace_id).workspace


def grounding(session, project_tmp_path, evidence=None) -> WorkspaceGroundingService:
    return WorkspaceGroundingService(
        session, data_root=project_tmp_path, evidence=evidence
    )


def _offline_build_factory(session):
    """Offline L2S3 build composition (fake LLM/embedding/metadata), mirroring
    the production composition through the Workspace-specific storage roots."""

    def factory(workspace_id, layout):
        from transit_scholar.layer2.schema_extraction.api import get_schema

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


def build_wiki(session, project_tmp_path, workspace_id: str):
    """Build a real (offline-composed) Base Wiki for a bound Workspace."""
    return wiki_service(session, project_tmp_path).build(workspace_id)


class FakeEvidence:
    """Read-only L2S1 seam: no global assets exist for the Papers."""

    def l2s1_ready(self, paper_id: str) -> bool:
        return False


# ---------------------------------------------------------------------------
# AC-012: normalized snapshot of a fully ready Workspace
# ---------------------------------------------------------------------------


def test_grounding_ready_workspace_returns_complete_snapshot(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-ready")
    paper_one = add_paper(session, paper_id="pa", title="Paper A")
    paper_two = add_paper(session, paper_id="pb", title="Paper B")
    service.add_paper(workspace.workspace_id, paper_one.id)
    service.add_paper(workspace.workspace_id, paper_two.id)

    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    schemas.materialize(workspace.workspace_id, paper_one.id, llm_client=FakeLLMProvider())
    schemas.materialize(workspace.workspace_id, paper_two.id, llm_client=FakeLLMProvider())
    build_wiki(session, project_tmp_path, workspace.workspace_id)

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )

    # Workspace identity / lifecycle / revision.
    assert snapshot.workspace_id == workspace.workspace_id
    assert snapshot.name == workspace.name
    assert snapshot.status == "active"
    assert snapshot.revision == service.get(workspace.workspace_id).revision
    # Schema mode + the immutable bound identity persisted at creation (AC-004).
    assert snapshot.schema_mode == "bound"
    assert snapshot.schema_binding is not None
    assert snapshot.schema_binding.schema_id == DEFINITION.schema_id
    assert snapshot.schema_binding.schema_version == DEFINITION.version
    assert snapshot.schema_binding.schema_hash == compute_schema_hash(DEFINITION)

    # Visible Paper membership, deterministic order, per-Paper availability.
    assert snapshot.member_paper_ids == ["pa", "pb"]
    assert [paper.paper_id for paper in snapshot.visible_papers] == ["pa", "pb"]
    for paper in snapshot.visible_papers:
        assert paper.title in {"Paper A", "Paper B"}
        assert paper.paper_status == "active"
        assert paper.l2s1_ready is False
        assert paper.schema_status == "ready"

    # Schema coverage: complete.
    assert snapshot.schema_coverage.model_dump() == {
        "workspace_id": workspace.workspace_id,
        "total": 2,
        "ready": 2,
        "missing": 0,
        "status": "complete",
    }

    # Base Wiki: current/ready (AC-011).
    assert snapshot.base_wiki.status == "ready"
    assert snapshot.base_wiki.fingerprint == snapshot.base_wiki.recorded_fingerprint

    # Capability summary (AC-012).
    caps = snapshot.capabilities
    assert caps.knowledge_access is True
    assert caps.paper_access is True
    assert caps.evidence_access is True
    assert caps.schema_read is True
    assert caps.schema_materialization is True
    assert caps.wiki_build is True
    assert caps.wiki_read is True
    assert caps.evidence_ready_papers == 0
    assert caps.schema_ready_papers == 2

    # Nothing left to recommend for a fully ready Workspace.
    assert snapshot.recommended_actions == []


def test_grounding_snapshot_is_deterministic_for_unchanged_state(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-det")
    paper = add_paper(session)
    service.add_paper(workspace.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    build_wiki(session, project_tmp_path, workspace.workspace_id)

    ground = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground
    first = ground(workspace.workspace_id)
    second = ground(workspace.workspace_id)
    assert first == second
    assert first.model_dump() == second.model_dump()
    assert first.model_dump_json() == second.model_dump_json()


def test_grounding_required_fields_present_in_model(session, project_tmp_path):
    workspace = create_none_workspace(session, workspace_id="ws-fields")
    add_paper(session)
    WorkspaceService(session).add_paper(workspace.workspace_id, "p1")

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )

    # REQ-008 / AC-012: every required snapshot field is present.
    required = {
        "workspace_id",
        "name",
        "revision",
        "status",
        "schema_mode",
        "schema_binding",
        "member_paper_ids",
        "visible_papers",
        "schema_coverage",
        "base_wiki",
        "capabilities",
        "recommended_actions",
    }
    assert required <= set(GroundedWorkspace.model_fields)
    assert required <= set(snapshot.model_dump(exclude_none=False))

    # Nested required fields.
    assert {"workspace_id", "paper_id", "title", "paper_status", "l2s1_ready",
            "schema_status"} <= set(GroundedPaper.model_fields)
    assert {"workspace_id", "total", "ready", "missing", "status"} <= set(
        SchemaCoverage.model_fields
    )
    assert {
        "workspace_id",
        "knowledge_access",
        "paper_access",
        "evidence_access",
        "schema_read",
        "schema_materialization",
        "wiki_build",
        "wiki_read",
        "evidence_ready_papers",
        "schema_ready_papers",
    } <= set(WorkspaceCapabilities.model_fields)

    # Schema mode invariant: none mode exposes no binding (REQ-003).
    assert snapshot.schema_mode == "none"
    assert snapshot.schema_binding is None

    # Pydantic validation round-trip proves the payload is model-valid.
    reloaded = GroundedWorkspace.model_validate(snapshot.model_dump())
    assert reloaded == snapshot


# ---------------------------------------------------------------------------
# missing / partial derived states (AC-014 exposure, never rejection)
# ---------------------------------------------------------------------------


def test_grounding_missing_derived_state_reported_with_actions(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-missing")
    paper = add_paper(session, paper_id="p-raw")
    service.add_paper(workspace.workspace_id, paper.id)

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )

    assert snapshot.visible_papers[0].schema_status == "missing"
    assert snapshot.visible_papers[0].l2s1_ready is False
    assert snapshot.schema_coverage.status == "missing"
    assert snapshot.schema_coverage.total == 1
    assert snapshot.schema_coverage.ready == 0
    assert snapshot.schema_coverage.missing == 1
    assert snapshot.base_wiki.status == "missing"
    assert snapshot.base_wiki.error_code == "snapshot_missing"
    assert snapshot.capabilities.wiki_read is False

    codes = [action.code for action in snapshot.recommended_actions]
    assert codes == [ACTION_MATERIALIZE_SCHEMA_RUNS, ACTION_BUILD_BASE_WIKI]
    materialize = snapshot.recommended_actions[0]
    assert materialize.target_paper_ids == ["p-raw"]
    assert materialize.message
    assert snapshot.recommended_actions[1].message


def test_grounding_partial_schema_coverage_targets_only_missing_papers(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-partial")
    paper_one = add_paper(session, paper_id="p-ready")
    paper_two = add_paper(session, paper_id="p-lagging")
    service.add_paper(workspace.workspace_id, paper_one.id)
    service.add_paper(workspace.workspace_id, paper_two.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper_one.id, llm_client=FakeLLMProvider()
    )

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )

    assert snapshot.schema_coverage.status == "partial"
    assert snapshot.schema_coverage.ready == 1
    assert snapshot.schema_coverage.missing == 1
    statuses = {paper.paper_id: paper.schema_status for paper in snapshot.visible_papers}
    assert statuses == {"p-ready": "ready", "p-lagging": "missing"}
    assert snapshot.capabilities.schema_ready_papers == 1
    action = snapshot.recommended_actions[0]
    assert action.code == ACTION_MATERIALIZE_SCHEMA_RUNS
    assert action.target_paper_ids == ["p-lagging"]


# ---------------------------------------------------------------------------
# stale (AC-010: fingerprint-derived, no persisted flag)
# ---------------------------------------------------------------------------


def test_grounding_stale_wiki_after_membership_change(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-stale")
    paper_one = add_paper(session, paper_id="p1")
    service.add_paper(workspace.workspace_id, paper_one.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper_one.id, llm_client=FakeLLMProvider()
    )
    build_wiki(session, project_tmp_path, workspace.workspace_id)
    assert grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    ).base_wiki.status == "ready"

    # Authoritative membership change without a rebuild -> derived stale.
    paper_two = add_paper(session, paper_id="p2")
    service.add_paper(workspace.workspace_id, paper_two.id)

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )
    assert snapshot.base_wiki.status == "stale"
    assert snapshot.base_wiki.error_code == "input_fingerprint_mismatch"
    assert snapshot.base_wiki.fingerprint != snapshot.base_wiki.recorded_fingerprint
    codes = [action.code for action in snapshot.recommended_actions]
    assert codes == [ACTION_MATERIALIZE_SCHEMA_RUNS, ACTION_REBUILD_BASE_WIKI]
    # The rebuilt Wiki is derived current again once inputs stabilize (AC-011).
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper_two.id, llm_client=FakeLLMProvider()
    )
    rebuilt = wiki_service(session, project_tmp_path).build(workspace.workspace_id)
    after = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )
    assert after.base_wiki.status == "ready"
    assert after.base_wiki.recorded_fingerprint == rebuilt.fingerprint
    assert [action.code for action in after.recommended_actions] == []


# ---------------------------------------------------------------------------
# unsupported no-schema (REQ-005 / AC-009)
# ---------------------------------------------------------------------------


def test_grounding_no_schema_workspace_is_unsupported_not_fallback(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = create_none_workspace(session)
    paper = add_paper(session)
    service.add_paper(workspace.workspace_id, paper.id)

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )

    assert snapshot.schema_mode == "none"
    assert snapshot.schema_binding is None
    # Paper visibility is preserved; Schema status is disabled (AC-007).
    assert snapshot.member_paper_ids == [paper.id]
    assert snapshot.visible_papers[0].schema_status == "disabled"
    assert snapshot.schema_coverage.status == "disabled"
    assert snapshot.schema_coverage.ready == 0
    assert snapshot.schema_coverage.missing == 0
    # Base Wiki capability is unsupported, never a foreign/fabricated Wiki.
    assert snapshot.base_wiki.status == "unsupported"
    assert snapshot.capabilities.schema_read is False
    assert snapshot.capabilities.schema_materialization is False
    assert snapshot.capabilities.wiki_build is False
    assert snapshot.capabilities.wiki_read is False
    assert snapshot.capabilities.knowledge_access is True
    assert snapshot.recommended_actions == []
    # No Schema/Wiki storage was ever created for the no-schema Workspace.
    assert not workspace_layout(
        workspace.workspace_id, data_root=project_tmp_path
    ).derived_dir.exists()


# ---------------------------------------------------------------------------
# archived (REQ-009 / AC-016: preserved but not accessible)
# ---------------------------------------------------------------------------


def test_grounding_archived_workspace_preserves_state_but_blocks_access(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-arch")
    paper = add_paper(session)
    service.add_paper(workspace.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    build_wiki(session, project_tmp_path, workspace.workspace_id)
    service.archive(workspace.workspace_id)

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )

    assert snapshot.status == "archived"
    # Memberships and derived snapshots are preserved (AC-016) ...
    assert snapshot.member_paper_ids == [paper.id]
    assert snapshot.visible_papers[0].schema_status == "ready"
    assert snapshot.schema_coverage.status == "complete"
    assert snapshot.base_wiki.status == "ready"
    # ... but normal active knowledge access is unavailable.
    caps = snapshot.capabilities
    assert caps.knowledge_access is False
    assert caps.paper_access is False
    assert caps.evidence_access is False
    assert caps.schema_read is False
    assert caps.schema_materialization is False
    assert caps.wiki_build is False
    assert caps.wiki_read is False
    # No repair actions for a non-active Workspace (control-plane decision).
    assert snapshot.recommended_actions == []


# ---------------------------------------------------------------------------
# error (integrity/provenance failure)
# ---------------------------------------------------------------------------


def test_grounding_error_state_for_corrupt_wiki(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-corrupt")
    paper = add_paper(session)
    service.add_paper(workspace.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    build_wiki(session, project_tmp_path, workspace.workspace_id)
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    (layout.wiki_dir / "entities.jsonl").unlink()

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )
    assert snapshot.base_wiki.status == "error"
    assert snapshot.base_wiki.error_code == "wiki_corrupt"
    assert [action.code for action in snapshot.recommended_actions] == [
        ACTION_REPAIR_BASE_WIKI
    ]


def test_grounding_error_state_for_unreadable_provenance(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-prov")
    paper = add_paper(session)
    service.add_paper(workspace.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    build_wiki(session, project_tmp_path, workspace.workspace_id)
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    (layout.wiki_dir / "provenance.json").write_text("{not json", encoding="utf-8")

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )
    assert snapshot.base_wiki.status == "error"
    assert snapshot.base_wiki.error_code == "build_provenance_unreadable"
    assert [action.code for action in snapshot.recommended_actions] == [
        ACTION_REPAIR_BASE_WIKI
    ]


# ---------------------------------------------------------------------------
# lifecycle tombstones
# ---------------------------------------------------------------------------


def test_grounding_deleting_workspace_reports_non_accessible_state(
    session, project_tmp_path
):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-deleting")
    # ``delete()`` commits durably, so globally unique ids are required to
    # avoid collisions with later tests in the shared migrated DB.
    paper = add_paper(session, paper_id=uuid.uuid4().hex)
    service.add_paper(workspace.workspace_id, paper.id)

    def interrupted_cleanup(workspace_id, layout):
        raise RuntimeError("simulated interruption before cleanup completes")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        service.delete(workspace.workspace_id, file_cleanup=interrupted_cleanup)

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )
    assert snapshot.status == "deleting"
    assert snapshot.member_paper_ids == []
    assert snapshot.visible_papers == []
    # Memberships were revoked at the durable boundary; coverage is empty and
    # the derived Wiki has no inputs -> explicit missing/empty outcome.
    assert snapshot.schema_coverage.status == "empty"
    assert snapshot.schema_coverage.total == 0
    assert snapshot.base_wiki.status == "missing"
    assert snapshot.base_wiki.error_code == "empty_membership"
    assert snapshot.capabilities.knowledge_access is False
    assert snapshot.recommended_actions == []


def test_grounding_deleted_workspace_is_a_tombstone(session, project_tmp_path):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id="ws-tomb")
    # ``delete()`` commits durably, so globally unique ids are required.
    paper = add_paper(session, paper_id=uuid.uuid4().hex)
    service.add_paper(workspace.workspace_id, paper.id)
    service.delete(workspace.workspace_id, data_root=project_tmp_path)

    snapshot = grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace.workspace_id
    )
    assert snapshot.status == "deleted"
    assert snapshot.member_paper_ids == []
    assert snapshot.capabilities.knowledge_access is False
    # The global Paper survives Workspace deletion (AC-017 / C-009).
    assert session.get(Paper, paper.id) is not None


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_grounding_unknown_workspace_raises_stable_error(session, project_tmp_path):
    with pytest.raises(WorkspaceNotFoundError) as exc_info:
        grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
            "does-not-exist"
        )
    assert exc_info.value.code == "workspace_not_found"


# ---------------------------------------------------------------------------
# REQ-002 / AC-007: wiki_read requires a production-complete Base Wiki
# ---------------------------------------------------------------------------


def _ready_workspace(session, project_tmp_path, *, workspace_id, paper_id):
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id=workspace_id)
    paper = add_paper(session, paper_id=paper_id, title="Completeness Grounding")
    service.add_paper(workspace.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    build_wiki(session, project_tmp_path, workspace.workspace_id)
    return workspace


def _ground_snapshot(session, project_tmp_path, workspace_id):
    return grounding(session, project_tmp_path, evidence=FakeEvidence()).ground(
        workspace_id
    )


def _rewrite_file(path, changes):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_grounding_wiki_read_false_for_partial_manifest(session, project_tmp_path):
    """AC-007: a partial Manifest build never exposes wiki_read=true."""
    workspace = _ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-partial", paper_id="gp1"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    _rewrite_file(layout.wiki_dir / "manifest.json", {"build_status": "partial"})

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    assert snapshot.base_wiki.status == "error"
    assert snapshot.base_wiki.error_code == "manifest_build_partial"
    assert snapshot.capabilities.wiki_read is False
    assert [a.code for a in snapshot.recommended_actions] == [ACTION_REPAIR_BASE_WIKI]


def test_grounding_wiki_read_false_for_failed_manifest(session, project_tmp_path):
    """AC-007: a failed Manifest build never exposes wiki_read=true."""
    workspace = _ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-failed", paper_id="gp2"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    _rewrite_file(layout.wiki_dir / "manifest.json", {"build_status": "failed"})

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    assert snapshot.base_wiki.status == "error"
    assert snapshot.base_wiki.error_code == "manifest_build_failed"
    assert snapshot.capabilities.wiki_read is False


def test_grounding_wiki_read_false_for_incomplete_provenance(
    session, project_tmp_path
):
    """AC-007: non-complete recorded provenance never exposes wiki_read=true."""
    workspace = _ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-prov", paper_id="gp3"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    _rewrite_file(layout.wiki_dir / "provenance.json", {"build_status": "partial"})

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    assert snapshot.base_wiki.status == "error"
    assert snapshot.base_wiki.error_code == "build_provenance_incomplete"
    assert snapshot.capabilities.wiki_read is False


def test_grounding_wiki_read_false_for_missing_vector_index(
    session, project_tmp_path
):
    """AC-007: a missing mandatory vector index never exposes wiki_read=true."""
    workspace = _ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-index", paper_id="gp4"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    index_path = layout.wiki_dir / "index" / "package_b_index.json"
    assert index_path.is_file()
    index_path.unlink()

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    assert snapshot.base_wiki.status == "error"
    assert snapshot.base_wiki.error_code == "vector_index_missing"
    assert snapshot.capabilities.wiki_read is False


def test_grounding_wiki_read_false_for_stale_vector_index(
    session, project_tmp_path
):
    """AC-007: a stale mandatory vector index never exposes wiki_read=true."""
    workspace = _ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-stale", paper_id="gp5"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    _rewrite_file(
        layout.wiki_dir / "index" / "package_b_index.json",
        {"source_fingerprint": "forged-stale"},
    )

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    assert snapshot.base_wiki.status == "error"
    assert snapshot.base_wiki.error_code == "vector_index_stale"
    assert snapshot.capabilities.wiki_read is False


def test_grounding_wiki_read_false_for_incompatible_vector_index(
    session, project_tmp_path
):
    """AC-007: an incompatible mandatory vector index (invalid metadata or
    dimensions) never exposes wiki_read=true."""
    workspace = _ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-incomp", paper_id="gp6"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    _rewrite_file(
        layout.wiki_dir / "index" / "package_b_index.json",
        {"vector_metadata": {"provider": "test", "model": "test", "dimension": 7}},
    )

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    assert snapshot.base_wiki.status == "error"
    assert snapshot.base_wiki.error_code == "vector_index_incompatible"
    assert snapshot.capabilities.wiki_read is False


def test_grounding_wiki_read_true_only_for_complete_current_valid_wiki(
    session, project_tmp_path
):
    """REQ-002/AC-006/AC-007: the complete, current, structurally valid
    snapshot with a valid current vector index is the ONLY wiki_read=true
    state."""
    workspace = _ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-ready", paper_id="gp7"
    )
    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    assert snapshot.base_wiki.status == "ready"
    assert snapshot.base_wiki.error_code is None
    assert snapshot.capabilities.wiki_read is True
    assert snapshot.capabilities.wiki_build is True


# ---------------------------------------------------------------------------
# REQ-004: Grounding schema_status=ready requires a readable, binding-compatible run
# ---------------------------------------------------------------------------


def _rewrite_pointer(layout, paper_id, changes):
    """Rewrite ``current.json`` with the given field changes (tamper helper)."""
    path = layout.schema_storage().current_path(paper_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _rewrite_run_manifest(layout, paper_id, run_id, changes):
    """Rewrite ``run_manifest.json`` with the given field changes.

    The run manifest is not digest-protected by L2S2, so the run stays fully
    READABLE while its recorded Schema identity becomes binding-incompatible
    (AC-013/AC-014).
    """
    path = layout.schemas_dir / paper_id / "runs" / run_id / "run_manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _schema_only_workspace(session, project_tmp_path, *, workspace_id, paper_id):
    """A bound Workspace with ONE materialized compatible Schema run."""
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id=workspace_id)
    paper = add_paper(session, paper_id=paper_id, title="Schema Grounding")
    service.add_paper(workspace.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    return workspace, paper


def test_grounding_non_ready_when_current_pointer_references_missing_run(
    session, project_tmp_path
):
    """AC-012: current.json exists but the referenced run is missing ->
    Grounding NEVER reports schema_status=ready and exposes the explicit
    ``schema_missing`` outcome."""
    import shutil

    workspace, paper = _schema_only_workspace(
        session, project_tmp_path, workspace_id="ws-gr-ptr", paper_id="gs1"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    assert (layout.schemas_dir / paper.id / "current.json").is_file()
    run_id = layout.schema_storage().read_current(paper.id).run_id
    shutil.rmtree(layout.schemas_dir / paper.id / "runs" / run_id)

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    paper_view = snapshot.visible_papers[0]
    assert paper_view.schema_status == "missing"
    assert paper_view.schema_error_code == "schema_missing"
    assert snapshot.schema_coverage.status == "missing"
    assert snapshot.schema_coverage.ready == 0
    assert snapshot.capabilities.schema_ready_papers == 0
    assert ACTION_MATERIALIZE_SCHEMA_RUNS in [
        action.code for action in snapshot.recommended_actions
    ]


def test_grounding_non_ready_for_readable_run_with_version_mismatch(
    session, project_tmp_path
):
    """AC-013: a READABLE persisted run whose schema_version disagrees with
    the Workspace binding -> explicit non-ready with the stable
    ``schema_binding_mismatch`` error code."""
    workspace, paper = _schema_only_workspace(
        session, project_tmp_path, workspace_id="ws-gr-runver", paper_id="gs2"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    run_id = layout.schema_storage().read_current(paper.id).run_id
    _rewrite_run_manifest(layout, paper.id, run_id, {"schema_version": "0.0-forged"})

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    paper_view = snapshot.visible_papers[0]
    assert paper_view.schema_status == "missing"
    assert paper_view.schema_error_code == "schema_binding_mismatch"
    assert snapshot.schema_coverage.status == "missing"
    assert snapshot.capabilities.schema_ready_papers == 0
    # The mismatch is NEVER silently mapped to ready (AC-016).
    assert paper_view.schema_status != "ready"


def test_grounding_non_ready_for_pointer_hash_mismatch(session, project_tmp_path):
    """AC-014: the normal L2S2 current pointer exposes schema_hash; a hash
    incompatible with the Workspace binding -> explicit non-ready with the
    stable ``schema_binding_mismatch`` error code."""
    workspace, paper = _schema_only_workspace(
        session, project_tmp_path, workspace_id="ws-gr-ptrhash", paper_id="gs3"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    _rewrite_pointer(layout, paper.id, {"schema_hash": "f" * 64})

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    paper_view = snapshot.visible_papers[0]
    assert paper_view.schema_status == "missing"
    assert paper_view.schema_error_code == "schema_binding_mismatch"
    assert snapshot.schema_coverage.ready == 0


def test_grounding_ready_for_readable_binding_compatible_run(
    session, project_tmp_path
):
    """AC-015: a readable, fully binding-compatible persisted run keeps every
    surface usable — get_instance(), get_field(), current-run identity
    derivation and Grounding schema_status=ready (with no error code)."""
    workspace, paper = _schema_only_workspace(
        session, project_tmp_path, workspace_id="ws-gr-ok", paper_id="gs4"
    )
    schemas = WorkspaceSchemaService(session, data_root=project_tmp_path)
    instance = schemas.get_instance(workspace.workspace_id, paper.id)
    assert instance.paper_id == paper.id
    field = schemas.get_field(
        workspace.workspace_id, paper.id, "research_problem.control_type"
    )
    assert field is not None
    identities = schemas.current_run_identities(workspace.workspace_id, [paper.id])
    assert identities[paper.id] is not None

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    paper_view = snapshot.visible_papers[0]
    assert paper_view.schema_status == "ready"
    assert paper_view.schema_error_code is None
    assert snapshot.schema_coverage.status == "complete"
    assert snapshot.capabilities.schema_ready_papers == 1


# ---------------------------------------------------------------------------
# REQ-002 / AC-008..AC-009: Grounding never contradicts Schema governance
# ---------------------------------------------------------------------------


def _schema_wiki_ready_workspace(session, project_tmp_path, *, workspace_id, paper_id):
    """A bound Workspace with one materialized member, a READY Base Wiki and a
    Grounding-verified ready snapshot."""
    service = WorkspaceService(session)
    workspace = create_bound_workspace(session, workspace_id=workspace_id)
    paper = add_paper(session, paper_id=paper_id, title="Consistency Paper")
    service.add_paper(workspace.workspace_id, paper.id)
    WorkspaceSchemaService(session, data_root=project_tmp_path).materialize(
        workspace.workspace_id, paper.id, llm_client=FakeLLMProvider()
    )
    build_wiki(session, project_tmp_path, workspace.workspace_id)
    assert (
        grounding(session, project_tmp_path, evidence=FakeEvidence())
        .ground(workspace.workspace_id)
        .base_wiki.status
        == "ready"
    )
    return workspace, paper


def test_grounding_wiki_read_false_when_contributing_run_binding_mismatched(
    session, project_tmp_path
):
    """AC-008: when a member Paper that contributed to a previously ready Wiki
    derives schema_status missing with ``schema_binding_mismatch``, the SAME
    Grounding snapshot MUST report base_wiki.status != ready and
    wiki_read=false — never the old Wiki as current/ready (REQ-003)."""
    workspace, paper = _schema_wiki_ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-ac8", paper_id="gac8"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    # current.json stays byte-identical; only the persisted run manifest is
    # made binding-incompatible (AC-005 tamper pattern).
    current_path = layout.schemas_dir / paper.id / "current.json"
    current_bytes = current_path.read_bytes()
    run_id = layout.schema_storage().read_current(paper.id).run_id
    _rewrite_run_manifest(layout, paper.id, run_id, {"schema_hash": "f" * 64})
    assert current_path.read_bytes() == current_bytes

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    paper_view = snapshot.visible_papers[0]
    assert paper_view.schema_status == "missing"
    assert paper_view.schema_error_code == "schema_binding_mismatch"
    assert snapshot.base_wiki.status != "ready"
    assert snapshot.base_wiki.error_code == "schema_input_invalid"
    assert snapshot.capabilities.wiki_read is False


def test_grounding_wiki_read_false_when_contributing_run_missing(
    session, project_tmp_path
):
    """AC-009: when a member Paper's current run is absent/corrupt/unreadable
    (schema_status missing with ``schema_missing``) while current.json remains
    present, the SAME Grounding snapshot MUST report base_wiki.status != ready
    and wiki_read=false (REQ-003)."""
    import shutil

    workspace, paper = _schema_wiki_ready_workspace(
        session, project_tmp_path, workspace_id="ws-gr-ac9", paper_id="gac9"
    )
    layout = workspace_layout(workspace.workspace_id, data_root=project_tmp_path)
    assert (layout.schemas_dir / paper.id / "current.json").is_file()
    run_id = layout.schema_storage().read_current(paper.id).run_id
    shutil.rmtree(layout.schemas_dir / paper.id / "runs" / run_id)

    snapshot = _ground_snapshot(session, project_tmp_path, workspace.workspace_id)
    paper_view = snapshot.visible_papers[0]
    assert paper_view.schema_status == "missing"
    assert paper_view.schema_error_code == "schema_missing"
    assert snapshot.schema_coverage.status == "missing"
    assert snapshot.base_wiki.status != "ready"
    assert snapshot.base_wiki.error_code == "schema_input_invalid"
    assert snapshot.capabilities.wiki_read is False
    # The recommended actions drive repair in the same direction: materialize
    # the missing run, then rebuild the Wiki — never read the old one.
    codes = [action.code for action in snapshot.recommended_actions]
    assert ACTION_MATERIALIZE_SCHEMA_RUNS in codes
    assert ACTION_REBUILD_BASE_WIKI in codes