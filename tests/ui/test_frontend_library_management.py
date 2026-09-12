"""Extended Paper management UI behavior (T-006).

These tests pin the extended Library iteration to the implementation:

* metadata detail/correction submits the changed fields through
  ``PATCH /api/v1/papers/{id}/metadata`` and re-reads the Paper afterwards;
* metadata candidates, record details, and enrichment diagnostics stay behind
  advanced disclosures instead of leading the Paper view;
* bibliography records render from the citations endpoint;
* duplicate relations can be adjudicated through the resolve endpoint when the
  API reports a pending relation;
* global Library deletion always requires an explicit confirmation and remains a
  different action from Workspace membership removal;
* restoration is offered only from API-reported deleted state, and deleted
  papers can be listed again on request.

Method
------
The static tests pin the user-visible contract to the source. The final test
drives the real production bundle (``ui/dist``) inside jsdom against a
deterministic in-memory stand-in for the frozen ``/api/v1/*`` API, so the
required interactions are exercised through the built UI rather than asserted
only in source text.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
UI_SRC = UI_ROOT / "src"
UI_DIST = UI_ROOT / "dist"
SMOKE_SCRIPT = UI_ROOT / "scripts" / "smoke-library-management.mjs"
BACKEND_PAPER_SCHEMAS = REPO_ROOT / "src" / "transit_scholar" / "api" / "schemas" / "papers.py"

LIBRARY = "features/library"
PAPER_DETAIL = f"{LIBRARY}/PaperDetailView.tsx"
LIBRARY_VIEW = f"{LIBRARY}/LibraryView.tsx"
LABELS = f"{LIBRARY}/labels.ts"
METADATA_PANEL = f"{LIBRARY}/MetadataCorrectionPanel.tsx"
CANDIDATES_PANEL = f"{LIBRARY}/MetadataCandidatesPanel.tsx"
BIBLIOGRAPHY_PANEL = f"{LIBRARY}/BibliographyPanel.tsx"
DUPLICATE_PANEL = f"{LIBRARY}/DuplicateRelationsPanel.tsx"
ENRICHMENT_PANEL = f"{LIBRARY}/EnrichmentPanel.tsx"
LIFECYCLE_PANEL = f"{LIBRARY}/PaperLibraryLifecyclePanel.tsx"
MEMBERSHIP = f"{LIBRARY}/PaperWorkspaceMembership.tsx"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text(encoding="utf-8")


def _package() -> dict:
    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


# --------------------------------------------------------- metadata handling


def test_metadata_correction_submits_changed_fields_and_refreshes_the_paper():
    panel = _read(METADATA_PANEL)
    assert "api.papers.updateMetadata(" in panel
    for marker in (
        'data-testid="metadata-correction-form"',
        'data-testid="metadata-correction-submit"',
        'data-testid="metadata-correction-result"',
        'data-testid="metadata-title-input"',
        'data-testid="metadata-authors-input"',
    ):
        assert marker in panel, f"metadata correction panel is missing {marker}"
    # Only changed, non-empty values are submitted; the API applies only the
    # fields present in the request body.
    assert "buildMetadataChange" in panel
    assert "value.length > 0 && value !== (current ?? '').trim()" in panel
    assert "payload.authors = names" in panel

    detail = _read(PAPER_DETAIL)
    assert "MetadataCorrectionPanel" in detail
    assert "onUpdated={refreshPaper}" in detail
    assert "function refreshPaper" in detail
    assert "detail.reload()" in detail and "readiness.reload()" in detail
    assert 'data-testid="paper-metadata-correction"' in detail


def test_metadata_candidates_and_diagnostics_stay_behind_advanced_disclosures():
    candidates = _read(CANDIDATES_PANEL)
    assert "api.papers.metadataCandidates(" in candidates
    assert 'data-testid="metadata-candidates-panel"' in candidates
    assert 'data-testid="metadata-candidate-list"' in candidates

    detail = _read(PAPER_DETAIL)
    assert "MetadataCandidatesPanel" in detail
    assert 'summary="Metadata candidates"' in detail
    assert 'testId="metadata-candidates-disclosure"' in detail
    # Bibliography and duplicate review are researcher-facing, not diagnostics.
    assert 'data-testid="paper-bibliography-section"' in detail
    assert 'data-testid="paper-duplicate-review"' in detail
    assert "advanced__title" in detail
    assert 'summary="Record details"' in detail


# ---------------------------------------------------------- bibliography view


def test_bibliography_records_render_from_the_citations_endpoint():
    panel = _read(BIBLIOGRAPHY_PANEL)
    assert "api.papers.citations(" in panel
    assert 'data-testid="paper-bibliography"' in panel
    assert 'data-testid="bibliography-record-list"' in panel
    assert "bibliography-record-${record.id}" in panel
    for field in ("source_format", "raw_text", "structured_json", "parse_status", "parse_warnings"):
        assert field in panel, f"bibliography panel does not render {field}"
    assert "BibliographyPanel" in _read(PAPER_DETAIL)


# ----------------------------------------------------------- duplicate review


def test_duplicate_relations_can_be_adjudicated_only_while_pending():
    panel = _read(DUPLICATE_PANEL)
    assert "api.papers.duplicateRelations(" in panel
    assert "api.papers.resolveDuplicateRelation(" in panel
    assert "relation.status === 'pending'" in panel
    assert "DUPLICATE_DECISION_OPTIONS" in panel
    assert 'data-testid="duplicate-relation-list"' in panel
    assert "duplicate-relation-${relation.relation_id}" in panel
    assert "duplicate-resolve-${relation.relation_id}" in panel
    assert "DuplicateRelationsPanel" in _read(PAPER_DETAIL)


def test_duplicate_decision_values_match_the_frozen_api_contract():
    schema = BACKEND_PAPER_SCHEMAS.read_text(encoding="utf-8")
    literal = re.search(r"decision:\s*Literal\[(.*?)\]", schema, re.S)
    assert literal, "the duplicate resolution contract no longer declares decision values"
    api_values = re.findall(r'"([a-z_]+)"', literal.group(1))
    assert api_values, "no duplicate decision values were found in the API contract"

    labels = _read(LABELS)
    for value in api_values:
        assert f"value: '{value}'" in labels, f"the UI does not offer the API decision {value}"


# ----------------------------------------------------------------- enrichment


def test_enrichment_status_renders_and_refresh_uses_the_api():
    panel = _read(ENRICHMENT_PANEL)
    assert "api.papers.enrichment(" in panel
    assert "api.papers.refreshEnrichment(" in panel
    assert 'data-testid="paper-enrichment"' in panel
    assert 'data-testid="refresh-enrichment"' in panel
    assert 'data-testid="enrichment-provider-list"' in panel

    detail = _read(PAPER_DETAIL)
    assert "EnrichmentPanel" in detail
    assert 'summary="Enrichment status"' in detail
    assert 'testId="enrichment-disclosure"' in detail


# ------------------------------------------------- deletion vs. membership


def test_library_deletion_requires_confirmation_and_differs_from_removal():
    panel = _read(LIFECYCLE_PANEL)
    assert "api.papers.removeFromLibrary(" in panel
    assert 'data-testid="delete-library-paper"' in panel
    assert 'testId="delete-library-confirm-dialog"' in panel
    assert 'data-testid="confirm-delete-library-paper"' in panel
    assert "Delete from Library" in panel
    assert "Remove from workspace" in panel
    # The Library action is visually distinct from the workspace membership block.
    assert "warning-panel warning-panel--static" in panel

    # The delete request is wired only to the confirmed action: opening the
    # dialog cannot delete anything by itself.
    body = panel.split("return (", 1)[1]
    open_control = body.split('data-testid="delete-library-paper"')[0].rsplit("<Button", 1)[1]
    assert "setConfirmOpen(true)" in open_control
    assert "handleDelete" not in open_control
    assert "remove.run()" not in open_control
    confirm_control = body.split('data-testid="confirm-delete-library-paper"')[0].rsplit("<Button", 1)[1]
    assert "handleDelete" in confirm_control
    assert "setConfirmOpen(false)" in panel

    membership = _read(MEMBERSHIP)
    assert "Remove from workspace" in membership
    assert 'data-testid="remove-paper-from-workspace"' in membership
    # Membership removal never deletes a Paper from the global Library.
    assert "removeFromLibrary" not in membership
    assert "restoreToLibrary" not in membership


# ------------------------------------------------------ deleted paper state


def test_deleted_paper_restoration_is_driven_by_api_state():
    lifecycle = _read(LIFECYCLE_PANEL)
    assert "api.papers.restoreToLibrary(" in lifecycle
    assert "isLibraryRestorable(paper)" in lifecycle
    assert 'data-testid="restore-library-paper"' in lifecycle
    assert 'data-testid="library-deletion-deleted-state"' in lifecycle

    labels = _read(LABELS)
    assert "export function isLibraryRestorable" in labels
    assert "status === 'deleted'" in labels
    assert "status === 'archived'" in labels
    assert "deleted_at" in labels

    library = _read(LIBRARY_VIEW)
    assert 'data-testid="library-include-deleted"' in library
    assert "includeDeleted" in library
    assert "api.papers.list({ signal, limit: 100, includeDeleted })" in library
    assert "api.papers.restoreToLibrary(" in library
    assert "restore-library-paper-${paper.paper_id}" in library

    endpoint = _read("api/endpoints/papers.ts")
    assert "include_deleted: options.includeDeleted ? true : undefined" in endpoint


# ------------------------------------------------------------------ evidence


def test_extended_library_smoke_harness_covers_the_required_interactions():
    assert SMOKE_SCRIPT.is_file(), "the extended Library smoke harness is missing"
    body = SMOKE_SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "metadata-correction-submit",
        "metadata-correction-result",
        "duplicate-resolve-",
        "bibliography-record-",
        "delete-library-confirm-dialog",
        "confirm-delete-library-paper",
        "restore-library-paper",
        "refresh-enrichment",
        "remove-paper-from-workspace",
        "delete-library-paper",
    ):
        assert marker in body, f"the extended Library smoke does not exercise {marker}"

    # The harness may only speak the frozen product API and must not shell out
    # to the Product/Core Python layers.
    assert "pathname.startsWith('/api/v1/')" in body
    for forbidden in ("child_process", "spawnSync", "execSync", "transit_scholar"):
        assert forbidden not in body, f"the smoke harness references {forbidden}"

    scripts = _package()["scripts"]
    assert "smoke:library-management" in scripts
    assert "smoke-library-management.mjs" in scripts["smoke:library-management"]


def test_extended_library_smoke_runs_against_the_built_bundle():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to drive the built frontend")
    if not (UI_DIST / "index.html").is_file():
        pytest.skip("run `npm run build` in ui/ before the extended Library smoke")

    completed = subprocess.run(
        [node, str(SMOKE_SCRIPT), "--dist", str(UI_DIST)],
        cwd=str(UI_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, f"extended Library smoke failed:\n{output}"
    assert "PASS: extended Library management smoke completed" in completed.stdout
