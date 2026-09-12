"""Richer Workspace knowledge UI behavior (T-007).

These tests pin the Workspace knowledge iteration to the implementation:

* Workspace/Paper Schema readiness is inspectable in the Workspace settings and
  beside the Schema Wiki content it feeds, and its labels come from the API
  response, never from a frontend-invented readiness rule;
* Schema materialization is requested through the existing materialization
  endpoint only for a Paper the API reports as not materialized, and the API's
  answer (including a workspace-busy conflict) is surfaced explicitly;
* a Workspace created without a Schema explains that paper Schema
  materialization does not apply as a normal state, not as an error (AC-012);
* Wiki page and Agent Learned entry details render from structured API data,
  including provenance references, source papers, and the superseded-by link;
* Wiki search offers every match mode the capabilities API reports and sends the
  selected mode to the search endpoint (REQ-010 / AC-014).

Method
------
The static tests pin the user-visible contract to the source and cross-check the
readiness vocabulary against the frozen API contract. The final test drives the
real production bundle (``ui/dist``) inside jsdom against a deterministic
in-memory stand-in for the frozen ``/api/v1/*`` API, so the required
interactions are exercised through the built UI rather than asserted only in
source text.
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
SMOKE_SCRIPT = UI_ROOT / "scripts" / "smoke-workspace-knowledge.mjs"
BACKEND_WORKSPACE_ROUTER = (
    REPO_ROOT / "src" / "transit_scholar" / "api" / "routers" / "workspaces.py"
)
BACKEND_SCHEMA_ERRORS = REPO_ROOT / "src" / "transit_scholar" / "layer3" / "schema" / "errors.py"

WIKI = "features/wiki"
WORKSPACES = "features/workspaces"
READINESS_PANEL = f"{WORKSPACES}/PaperSchemaReadinessPanel.tsx"
WORKSPACE_LABELS = f"{WORKSPACES}/labels.ts"
WORKSPACE_SETTINGS = f"{WORKSPACES}/WorkspaceSettingsView.tsx"
SCHEMA_WIKI_PANEL = f"{WIKI}/SchemaWikiPanel.tsx"
SEARCH_PANEL = f"{WIKI}/WikiSearchPanel.tsx"
PAGE_DETAIL = f"{WIKI}/WikiPageDetailView.tsx"
ENTRY_DETAIL = f"{WIKI}/AgentLearnedEntryDetailView.tsx"
WIKI_LABELS = f"{WIKI}/labels.ts"
PROSE = "components/Prose.tsx"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text(encoding="utf-8")


def _package() -> dict:
    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


# ------------------------------------------------- paper schema readiness


def test_paper_schema_readiness_is_read_from_the_schema_api():
    panel = _read(READINESS_PANEL)
    assert "api.workspaces.paperSchema(" in panel
    assert 'data-testid="paper-schema-readiness"' in panel
    assert 'data-testid="paper-schema-readiness-list"' in panel
    assert "paper-schema-readiness-row-${member.paper_id}" in panel
    assert "paper-schema-status-${paperId}" in panel
    # The Workspace record decides whether Schema materialization applies at all.
    assert "api.workspaces.get(" in panel
    assert "schema_mode === 'none'" in panel
    assert 'data-testid="paper-schema-readiness-not-applicable"' in panel


def test_paper_schema_labels_are_derived_from_the_api_response():
    labels = _read(WORKSPACE_LABELS)
    assert "export function describePaperSchemaStatus" in labels
    assert "export function paperSchemaStatusTone" in labels
    assert "export function describePaperSchemaCode" in labels
    assert "export function isPaperSchemaMaterializable" in labels
    # Only an API-reported missing Paper is offered materialization.
    materializable = labels.split("export function isPaperSchemaMaterializable")[1].split("\n}")[0]
    assert "status === 'missing'" in materializable
    assert "status === 'ready'" not in materializable

    # Every readiness status the API can report has a user-facing label.
    router = BACKEND_WORKSPACE_ROUTER.read_text(encoding="utf-8")
    assert 'status="disabled"' in router, "the API no longer reports a disabled Schema state"
    describe_block = labels.split("export function describePaperSchemaStatus")[1].split("\n}")[0]
    for status in ("ready", "missing", "disabled"):
        assert f"case '{status}'" in describe_block, f"the UI does not label the API status {status}"


def test_paper_schema_error_codes_match_the_frozen_contract():
    errors = BACKEND_SCHEMA_ERRORS.read_text(encoding="utf-8")
    api_codes = sorted(set(re.findall(r'code = "(schema_[a-z_]+)"', errors)))
    assert api_codes, "no Schema error codes were found in the API contract"

    labels = _read(WORKSPACE_LABELS)
    code_block = labels.split("export function describePaperSchemaCode")[1].split("\n}")[0]
    for code in api_codes:
        assert f"case '{code}'" in code_block, f"the UI does not explain the API code {code}"


def test_schema_materialization_uses_the_existing_endpoint():
    panel = _read(READINESS_PANEL)
    assert "api.workspaces.materializePaperSchema(" in panel
    assert "materialize-paper-schema-${paperId}" in panel
    assert "paper-schema-materialize-result-${paperId}" in panel
    # The request path must be the frozen materialization route.
    router = BACKEND_WORKSPACE_ROUTER.read_text(encoding="utf-8")
    assert '"/{workspace_id}/papers/{paper_id}/schema/materialize"' in router
    endpoint = _read("api/endpoints/workspaces.ts")
    assert "/papers/${encodeURIComponent(paperId)}/schema/materialize`" in endpoint


def test_readiness_and_materialization_are_offered_where_schema_content_is_used():
    schema_panel = _read(SCHEMA_WIKI_PANEL)
    assert "PaperSchemaReadinessPanel" in schema_panel
    assert 'data-testid="wiki-schema-readiness-section"' in schema_panel
    # Materializing a Paper can change the Schema Wiki inputs, so the Wiki
    # overview is re-read from the API afterwards.
    assert "onMaterialized={onWikiChanged}" in schema_panel

    settings = _read(WORKSPACE_SETTINGS)
    assert 'data-testid="workspace-paper-schema-readiness"' in settings
    assert "PaperSchemaReadinessPanel" in settings


def test_workspace_busy_conflict_is_explained_from_the_api_error():
    panel = _read(READINESS_PANEL)
    assert "toApiError(materialize.error)" in panel
    assert "code === 'WORKSPACE_BUSY'" in panel
    assert "paper-schema-materialize-busy-${paperId}" in panel
    router = BACKEND_WORKSPACE_ROUTER.read_text(encoding="utf-8")
    assert '"WORKSPACE_BUSY"' in router, "the API no longer reports a workspace-busy conflict"


# ------------------------------------------------------------- wiki details


def test_wiki_page_detail_renders_structured_page_data():
    detail = _read(PAGE_DETAIL)
    assert "api.wiki.page(" in detail
    assert 'data-testid="wiki-page-detail"' in detail
    assert 'testId="wiki-page-summary"' in detail
    assert 'data-testid="wiki-page-build-details"' in detail
    assert 'data-testid="wiki-page-source-paper"' in detail
    assert 'testId="wiki-page-identifiers"' in detail
    # The source paper is resolved through the Papers API, not invented locally.
    assert "usePaperTitles()" in detail
    assert "/library/${encodeURIComponent(record.paper_id)}" in detail
    for field in ("schema_id", "schema_version", "build_revision", "build_status", "paper_id"):
        assert field in detail, f"the Wiki page detail does not render {field}"


def test_agent_learned_entry_detail_renders_provenance_and_lifecycle():
    detail = _read(ENTRY_DETAIL)
    assert "api.wiki.agenticEntry(" in detail
    assert 'data-testid="wiki-entry-detail"' in detail
    assert 'testId="wiki-entry-content"' in detail
    assert 'data-testid="wiki-entry-provenance"' in detail
    assert "wiki-entry-claim-refs" in detail
    assert "wiki-entry-evidence-refs" in detail
    assert "wiki-entry-provenance-refs" in detail
    # A superseded entry stays navigable to the entry that replaced it.
    assert 'data-testid="wiki-entry-superseded"' in detail
    assert "/wiki/entries/${encodeURIComponent(record.superseded_by)}" in detail
    assert "record.status === 'stale'" in detail
    assert "record.paper_ids" in detail


def test_agent_learned_and_schema_wiki_stay_distinguishable():
    labels = _read(WIKI_LABELS)
    assert "'Schema Wiki'" in labels
    assert "'Agent Learned'" in labels
    assert "export function wikiSourceLabel" in labels
    assert "export function wikiSourceTone" in labels

    entry_detail = _read(ENTRY_DETAIL)
    assert 'tone="accent"' in entry_detail
    assert ">Agent Learned<" in entry_detail
    page_detail = _read(PAGE_DETAIL)
    assert 'tone="info"' in page_detail
    assert ">Schema Wiki<" in page_detail


# ------------------------------------------------------------- wiki search


def test_wiki_search_offers_both_reported_match_modes():
    panel = _read(SEARCH_PANEL)
    assert "api.wiki.search(" in panel
    assert "semantic_wiki_search" in panel
    assert "wiki-search-mode-${option}" in panel
    # Only the modes the capabilities API reports are selectable, and the
    # requested mode is what reaches the search endpoint.
    assert "const effectiveMode: WikiSearchMode = semanticAvailable ? mode : 'lexical'" in panel
    assert "mode: effectiveMode" in panel
    assert "disabled={disabled}" in panel
    assert "disabled = option === 'semantic' && !semanticAvailable" in panel


def test_wiki_search_surfaces_its_api_derived_state():
    panel = _read(SEARCH_PANEL)
    assert 'data-testid="wiki-search-summary"' in panel
    assert "describeSearchMode(effectiveMode)" in panel
    assert "describeSearchStatus(data.status)" in panel
    assert "describeSearchSourceErrors(data.source_errors)" in panel
    assert "describeSearchSourceStatus(status)" in panel
    assert "wikiSourceLabel(source)" in panel
    labels = _read(WIKI_LABELS)
    describe_block = labels.split("export function describeSearchSourceStatus")[1].split("\n}")[0]
    for state in ("ok", "degraded", "empty", "error", "unavailable"):
        assert f"case '{state}'" in describe_block, f"the UI does not label the search state {state}"


def test_api_prose_is_rendered_without_being_rewritten():
    prose = _read(PROSE)
    assert "export function Prose" in prose
    assert "split(/\\n\\s*\\n/)" in prose
    assert "prose__paragraph" in prose
    # No summarising or truncating of backend knowledge content.
    for forbidden in ("slice(", "substring(", "replace("):
        assert forbidden not in prose, f"Prose rewrites API content ({forbidden})"


# ------------------------------------------------------------------ evidence


def test_workspace_knowledge_smoke_harness_covers_the_required_interactions():
    assert SMOKE_SCRIPT.is_file(), "the Workspace knowledge smoke harness is missing"
    body = SMOKE_SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "paper-schema-readiness-list",
        "paper-schema-status-",
        "materialize-paper-schema-",
        "paper-schema-materialize-result-",
        "paper-schema-materialize-busy-",
        "paper-schema-readiness-not-applicable",
        "workspace-paper-schema-readiness",
        "wiki-page-detail",
        "wiki-page-source-paper",
        "wiki-entry-detail",
        "wiki-entry-evidence-refs",
        "wiki-entry-superseded",
        "wiki-search-mode-semantic",
        "wiki-search-mode-lexical",
        "wiki-hit-source",
    ):
        assert marker in body, f"the Workspace knowledge smoke does not exercise {marker}"

    # The harness may only speak the frozen product API and must not shell out
    # to the Product/Core Python layers.
    assert "pathname.startsWith('/api/v1/')" in body
    for forbidden in ("child_process", "spawnSync", "execSync", "transit_scholar"):
        assert forbidden not in body, f"the smoke harness references {forbidden}"

    scripts = _package()["scripts"]
    assert "smoke:workspace-knowledge" in scripts
    assert "smoke-workspace-knowledge.mjs" in scripts["smoke:workspace-knowledge"]


def test_workspace_knowledge_smoke_runs_against_the_built_bundle():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to drive the built frontend")
    if not (UI_DIST / "index.html").is_file():
        pytest.skip("run `npm run build` in ui/ before the Workspace knowledge smoke")

    completed = subprocess.run(
        [node, str(SMOKE_SCRIPT), "--dist", str(UI_DIST)],
        cwd=str(UI_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, f"Workspace knowledge smoke failed:\n{output}"
    assert "PASS: richer Workspace knowledge UI smoke completed" in completed.stdout
