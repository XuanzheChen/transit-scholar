"""Workspace Wiki UI behavior (T-004).

These tests pin the user-visible contract of the first Wiki iteration to the
implementation:

* the Wiki view reads capability/status from the Wiki API and shows Schema Wiki
  content and Agent Learned entries as two visually distinct knowledge sources;
* Wiki search submits to the existing search endpoint and renders structured
  hits tagged with their knowledge source;
* Wiki pages, topics, and Agent Learned entries each have a detail view;
* a Workspace whose Schema Wiki the API reports as unsupported shows an
  explanatory normal state instead of an application error (AC-012);
* the frontend only reaches the API through the shared transport module.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
UI_SRC = UI_ROOT / "src"
FEATURES = UI_SRC / "features"
WIKI = FEATURES / "wiki"

WIKI_VIEW = "features/wiki/WikiView.tsx"
STATUS_PANEL = "features/wiki/WikiStatusPanel.tsx"
SCHEMA_PANEL = "features/wiki/SchemaWikiPanel.tsx"
AGENT_PANEL = "features/wiki/AgentLearnedPanel.tsx"
SEARCH_PANEL = "features/wiki/WikiSearchPanel.tsx"
PAGE_DETAIL = "features/wiki/WikiPageDetailView.tsx"
ENTITY_DETAIL = "features/wiki/WikiEntityDetailView.tsx"
ENTRY_DETAIL = "features/wiki/AgentLearnedEntryDetailView.tsx"
LABELS = "features/wiki/labels.ts"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text(encoding="utf-8")


def _package() -> dict:
    import json

    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


# ------------------------------------------------------- capability & status


def test_wiki_view_reads_capability_and_status_from_the_wiki_api():
    view = _read(WIKI_VIEW)
    assert "api.wiki.overview(" in view
    # The section stays a Workspace-scoped product area.
    assert "RequiresWorkspace" in view

    status_panel = _read(STATUS_PANEL)
    assert 'data-testid="wiki-status"' in status_panel
    assert 'testId="wiki-status-badge"' in status_panel
    assert "base_wiki_capability" in status_panel
    assert "read_supported" in status_panel
    assert "build_supported" in status_panel
    # The status is labelled from the API value, never invented locally.
    assert "describeWikiStatus(" in status_panel
    assert "wikiStatusTone(" in status_panel


def test_schema_wiki_content_is_read_when_the_api_supports_it():
    panel = _read(SCHEMA_PANEL)
    assert "api.wiki.pages(" in panel
    assert "api.wiki.entities(" in panel
    # Content requests are gated on the API-reported read capability.
    assert "readSupported" in panel
    assert "read_supported" in panel
    assert 'data-testid="wiki-source-schema"' in panel
    assert 'data-testid="wiki-page-list"' in panel
    assert 'data-testid="wiki-entity-list"' in panel
    assert "`/wiki/pages/${" in panel
    assert "`/wiki/entities/${" in panel


def test_agent_learned_entries_are_listed_from_the_api():
    panel = _read(AGENT_PANEL)
    assert "api.wiki.agenticEntries(" in panel
    assert 'data-testid="wiki-source-agentic"' in panel
    assert 'data-testid="wiki-agentic-entry-list"' in panel
    assert "`/wiki/entries/${" in panel
    assert "describeAgenticEntryStatus(" in panel
    assert "Agent Learned" in panel


def test_wiki_search_uses_the_search_endpoint_and_renders_structured_hits():
    panel = _read(SEARCH_PANEL)
    assert "api.wiki.search(" in panel
    assert 'data-testid="wiki-search-input"' in panel
    assert 'data-testid="wiki-search-submit"' in panel
    assert 'data-testid="wiki-search-results"' in panel
    assert 'data-testid="wiki-search-hit"' in panel
    # Hits link to the matching detail route and are tagged by knowledge source.
    assert "wikiHitHref(" in panel
    assert "wikiSourceLabel(" in panel
    # The backend search status is surfaced rather than silently ignored.
    assert "describeSearchStatus(" in panel


# ------------------------------------------------------ visual distinction


def test_schema_wiki_and_agent_learned_are_visually_distinguishable():
    labels = _read(LABELS)
    assert "'Schema Wiki'" in labels
    assert "'Agent Learned'" in labels

    tone_block = labels.split("export function wikiSourceTone")[1].split("\n}")[0]
    assert "'accent'" in tone_block
    assert "'info'" in tone_block
    assert "agentic_wiki" in tone_block
    # Each knowledge source uses its own labelled panel style.
    assert "wiki-source--schema" in _read(SCHEMA_PANEL)
    assert "wiki-source--agentic" in _read(AGENT_PANEL)


def test_wiki_ui_uses_user_facing_knowledge_labels():
    for relative in (
        WIKI_VIEW,
        STATUS_PANEL,
        SCHEMA_PANEL,
        AGENT_PANEL,
        SEARCH_PANEL,
        PAGE_DETAIL,
        ENTITY_DETAIL,
        ENTRY_DETAIL,
        LABELS,
    ):
        text = _read(relative)
        assert "Base Wiki" not in text, f"{relative} leaks internal Wiki vocabulary"
        assert "Agentic Wiki" not in text, f"{relative} leaks internal Wiki vocabulary"


# ------------------------------------------------------- unavailable state


def test_unsupported_schema_wiki_is_an_explanatory_normal_state():
    panel = _read(SCHEMA_PANEL)
    labels = _read(LABELS)

    # The explanation is rendered through the shared normal-state component,
    # never through the error component.
    assert "explainSchemaWikiUnavailable(" in panel
    marker = 'data-testid="wiki-schema-unavailable"'
    assert marker in panel
    explanation_block = panel.split(marker)[1][:600]
    assert "UnavailableState" in explanation_block
    assert "ErrorState" not in explanation_block

    # A no-Schema Workspace is explained as a normal product state (AC-012).
    assert "created without a Schema" in labels
    assert "normal workspace state" in labels
    assert "no Schema-based Wiki content" in labels


# ------------------------------------------------------------- detail views


def test_wiki_pages_topics_and_entries_have_detail_views():
    view = _read(WIKI_VIEW)
    for marker in ("/wiki/pages/", "/wiki/entities/", "/wiki/entries/"):
        assert marker in view, f"the Wiki route shell does not resolve {marker}"

    page_detail = _read(PAGE_DETAIL)
    assert "api.wiki.page(" in page_detail
    assert 'data-testid="wiki-page-detail"' in page_detail

    entity_detail = _read(ENTITY_DETAIL)
    assert "api.wiki.entity(" in entity_detail
    assert 'data-testid="wiki-entity-detail"' in entity_detail

    entry_detail = _read(ENTRY_DETAIL)
    assert "api.wiki.agenticEntry(" in entry_detail
    assert 'data-testid="wiki-entry-detail"' in entry_detail


def test_wiki_feature_stays_on_the_shared_api_boundary():
    for source in WIKI.rglob("*.ts*"):
        text = source.read_text(encoding="utf-8")
        assert "fetch(" not in text, f"{source.name} bypasses the shared API module"
        assert "XMLHttpRequest" not in text, f"{source.name} bypasses the shared API module"


# ------------------------------------------------------------------ evidence


def test_live_wiki_smoke_harness_is_available():
    script = UI_ROOT / "scripts" / "smoke-wiki.mjs"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    for marker in (
        "wiki-status",
        "wiki-schema-unavailable",
        "wiki-tab-agentic",
        "wiki-source-agentic",
        "wiki-tab-search",
        "wiki-search-submit",
        "wiki-search-results",
        "wiki-entry-detail",
        "schema-mode-none",
        "create-workspace-submit",
        "import-paper-submit",
    ):
        assert marker in body, f"live smoke does not exercise {marker}"
    assert "smoke:wiki" in _package()["scripts"]
