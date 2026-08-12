"""Citation correctness and update-contract tests (AC-CITE-001..005).

Covers the four citation fixes plus the frozen update_citation_record input
matrix. Uses the isolated test database from conftest.py (Alembic head on a
temp dir). Papers are constructed directly via ORM — no PDFs, no network,
no LLM.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from transit_scholar.citation.parser import parse_citation
from transit_scholar.citation.renderer import render
from transit_scholar.citation.service import (
    SOURCE_FORMAT_WITHOUT_RAW_TEXT,
    STRUCTURED_DATA_MIXED_WITH_RAW_TEXT,
    import_citation_record,
    render_citation,
    update_citation_record,
)
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    AuditLog,
    CitationRecord,
    CitationRender,
    Paper,
    PaperRelation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_tables():
    """Clear all Stage 5 + related tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(CitationRender).delete()
        session.query(CitationRecord).delete()
        session.query(AuditLog).delete()
        session.query(PaperRelation).delete()
        session.query(Paper).delete()
        session.commit()
    yield


def _make_paper(
    *,
    title: str | None = "Sample Paper",
    doi: str | None = None,
    status: str = "active",
) -> Paper:
    paper = Paper(title=title, doi=doi, status=status)
    with SessionLocal() as session:
        session.add(paper)
        session.commit()
        session.refresh(paper)
        return paper


def _record(rec_id: str) -> CitationRecord:
    with SessionLocal() as session:
        return session.get(CitationRecord, rec_id)


def _audit_rows(entity_id: str) -> list[AuditLog]:
    with SessionLocal() as session:
        return session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "citation_record",
                AuditLog.entity_id == entity_id,
            ).order_by(AuditLog.created_at)
        ).scalars().all()


def _import_bibtex_record(paper_id: str, title: str = "Original BibTeX") -> str:
    r = import_citation_record(
        paper_id,
        source_format="bibtex",
        raw_text=(
            "@article{key,\n"
            f"  author = {{Lee, A.}},\n"
            f"  title = {{{title}}},\n"
            "  year = {2023}\n"
            "}"
        ),
    )
    assert r.status == "created", r.error_message
    return r.citation_record_id


# ---------------------------------------------------------------------------
# AC-CITE-001: single-author APA 7
# ---------------------------------------------------------------------------


def test_apa_single_author_renders_without_connector_or_prefix():
    """One author renders as 'Family, I.' — no '&' and no empty prefix."""
    structured = {
        "type": "article-journal",
        "title": "Single Author Paper",
        "author": [{"family": "Zhang", "given": "Wei"}],
        "issued": {"date-parts": [[2024]]},
        "container-title": "Test Journal",
        "volume": "10",
        "issue": "2",
        "page": "1-10",
        "DOI": "10.1234/single",
    }
    text, _ = render(structured, style="apa_7")
    assert text.startswith("Zhang, W.")
    assert "&" not in text
    assert not text.startswith(", &")
    assert not text.startswith(" &")


def test_apa_single_author_via_service():
    """Single-author APA render through render_citation is also clean."""
    paper = _make_paper()
    r = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={
            "type": "article-journal",
            "title": "Single Author Paper",
            "author": [{"family": "Zhang", "given": "Wei"}],
            "issued": {"date-parts": [[2024]]},
        },
    )
    rendered = render_citation(r.citation_record_id, style="apa_7")
    assert rendered.status == "rendered", rendered.error_message
    assert rendered.rendered_text.startswith("Zhang, W.")
    assert "&" not in rendered.rendered_text


def test_apa_two_authors_keeps_connector():
    """Existing multi-author behavior is preserved (regression guard)."""
    structured = {
        "type": "article-journal",
        "title": "Two Author Paper",
        "author": [
            {"family": "Zhang", "given": "Wei"},
            {"family": "Li", "given": "Hao"},
        ],
        "issued": {"date-parts": [[2024]]},
    }
    text, _ = render(structured, style="apa_7")
    assert text.startswith("Zhang, W., & Li, H.")


# ---------------------------------------------------------------------------
# AC-CITE-002: BibTeX internal _year must not leak
# ---------------------------------------------------------------------------


def test_bibtex_structured_has_no_internal_year_key():
    """Parser-level: public structured JSON contains no '_year' key."""
    bibtex = (
        "@article{key,\n"
        "  author = {Lee, A.},\n"
        "  title = {A BibTeX Paper},\n"
        "  journal = {Journal of Examples},\n"
        "  year = {2023},\n"
        "  volume = {5},\n"
        "  pages = {10-20},\n"
        "  doi = {10.5678/bib}\n"
        "}"
    )
    parsed = parse_citation(
        source_format="bibtex", raw_text=bibtex, structured_data=None
    )
    assert parsed.parse_status == "parsed"
    assert "_year" not in parsed.structured
    assert parsed.structured["issued"] == {"date-parts": [[2023]]}


def test_bibtex_persisted_structured_json_has_no_internal_year_key():
    """DB-level: the persisted structured_json has no '_year' key either."""
    paper = _make_paper()
    bibtex = (
        "@article{key,\n"
        "  author = {Lee, A. and Kim, B.},\n"
        "  title = {A BibTeX Paper},\n"
        "  year = {2023}\n"
        "}"
    )
    r = import_citation_record(paper.id, source_format="bibtex", raw_text=bibtex)
    assert r.status == "created", r.error_message
    structured = json.loads(_record(r.citation_record_id).structured_json)
    assert "_year" not in structured
    assert structured["issued"] == {"date-parts": [[2023]]}


# ---------------------------------------------------------------------------
# AC-CITE-003: invalid type warning carries the actual value
# ---------------------------------------------------------------------------


def test_invalid_citation_type_warning_contains_received_value():
    """Warning names the received type value, not the built-in type object."""
    parsed = parse_citation(
        source_format="manual_structured",
        raw_text=None,
        structured_data={"type": "totally-bogus-type", "title": "T"},
    )
    assert parsed.parse_status == "partial"
    assert parsed.structured["type"] == "unknown"
    assert any("totally-bogus-type" in w for w in parsed.warnings)
    assert not any("<class" in w for w in parsed.warnings)


# ---------------------------------------------------------------------------
# AC-CITE-004: raw_text-only / source_format+raw_text / source_format-only
# ---------------------------------------------------------------------------


def test_update_raw_text_only_reparses_with_stored_format():
    """raw_text-only reparses using the stored source_format."""
    paper = _make_paper()
    rec_id = _import_bibtex_record(paper.id, title="Original BibTeX")
    new_bibtex = (
        "@article{key,\n"
        "  author = {Lee, A. and Kim, B.},\n"
        "  title = {Updated BibTeX},\n"
        "  year = {2024}\n"
        "}"
    )
    before = _record(rec_id)
    assert before.source_format == "bibtex"
    result = update_citation_record(rec_id, raw_text=new_bibtex)
    assert result.status == "updated", result.error_message
    after = _record(rec_id)
    assert after.source_format == "bibtex"  # stored format kept
    assert after.raw_text == new_bibtex
    structured = json.loads(after.structured_json)
    assert structured["title"] == "Updated BibTeX"
    assert structured["issued"] == {"date-parts": [[2024]]}
    assert "_year" not in structured


def test_update_source_format_and_raw_text_reparses_with_new_format():
    """source_format + raw_text reparses with the new format and stores both."""
    paper = _make_paper()
    rec_id = _import_bibtex_record(paper.id)
    apa_text = (
        "Smith, J. (2024). A Sample APA Paper. "
        "Journal of APA Examples, 12(3), 100-115. https://doi.org/10.1234/apa"
    )
    result = update_citation_record(rec_id, source_format="apa", raw_text=apa_text)
    assert result.status == "updated", result.error_message
    after = _record(rec_id)
    assert after.source_format == "apa"
    assert after.raw_text == apa_text
    structured = json.loads(after.structured_json)
    assert structured["title"] == "A Sample APA Paper"
    assert structured["type"] == "article-journal"


def test_update_source_format_only_rejected_without_mutation_or_audit():
    """source_format-only returns SOURCE_FORMAT_WITHOUT_RAW_TEXT; no DB/audit change."""
    paper = _make_paper()
    rec_id = _import_bibtex_record(paper.id, title="Keep Me")
    before = _record(rec_id)
    audits_before = len(_audit_rows(rec_id))
    result = update_citation_record(rec_id, source_format="ris")
    assert result.status == "failed"
    assert result.error_code == SOURCE_FORMAT_WITHOUT_RAW_TEXT
    after = _record(rec_id)
    assert after.source_format == before.source_format == "bibtex"
    assert after.raw_text == before.raw_text
    assert after.structured_json == before.structured_json
    assert after.parse_status == before.parse_status
    assert len(_audit_rows(rec_id)) == audits_before


# ---------------------------------------------------------------------------
# AC-CITE-005: structured_data-only manual override + mixing rejections
# ---------------------------------------------------------------------------


def test_update_structured_data_only_is_audited_manual_override():
    """structured_data-only overrides structured JSON, preserves the original
    source_format/raw_text, and writes an audit fact identifying the override."""
    paper = _make_paper()
    rec_id = _import_bibtex_record(paper.id, title="Original BibTeX")
    before = _record(rec_id)
    override = {
        "type": "book",
        "title": "Manually Overridden",
        "author": [{"family": "Manual", "given": "Author"}],
        "issued": {"date-parts": [[2025]]},
        "publisher": "Manual Press",
    }
    result = update_citation_record(rec_id, structured_data=override)
    assert result.status == "updated", result.error_message
    after = _record(rec_id)
    # Original source evidence preserved.
    assert after.source_format == before.source_format == "bibtex"
    assert after.raw_text == before.raw_text
    # Canonical structured JSON from the manual normalization path.
    structured = json.loads(after.structured_json)
    assert structured["title"] == "Manually Overridden"
    assert structured["type"] == "book"
    assert structured["issued"] == {"date-parts": [[2025]]}
    assert "_year" not in structured
    # Audit fact identifies the manual override and carries old/new values.
    updates = [
        r for r in _audit_rows(rec_id)
        if json.loads(r.new_value_json or "{}").get("manual_structured_override") is True
    ]
    assert len(updates) == 1
    fact = updates[0]
    old_value = json.loads(fact.old_value_json or "{}")
    new_value = json.loads(fact.new_value_json or "{}")
    assert old_value["structured_json"]["title"] == "Original BibTeX"
    assert new_value["structured_json"]["title"] == "Manually Overridden"
    assert new_value["source_format"] == "bibtex"
    assert new_value["raw_text"] == before.raw_text
    assert new_value["manual_structured_override"] is True


def test_update_structured_data_with_raw_text_rejected():
    """Mixing structured_data with raw_text -> STRUCTURED_DATA_MIXED_WITH_RAW_TEXT."""
    paper = _make_paper()
    rec_id = _import_bibtex_record(paper.id)
    before = _record(rec_id)
    audits_before = len(_audit_rows(rec_id))
    result = update_citation_record(
        rec_id,
        raw_text="@article{k, title={X}}",
        structured_data={"type": "book", "title": "X"},
    )
    assert result.status == "failed"
    assert result.error_code == STRUCTURED_DATA_MIXED_WITH_RAW_TEXT
    after = _record(rec_id)
    assert after.structured_json == before.structured_json
    assert after.source_format == before.source_format
    assert after.raw_text == before.raw_text
    assert after.parse_status == before.parse_status
    assert len(_audit_rows(rec_id)) == audits_before


def test_update_structured_data_with_source_format_rejected():
    """Mixing structured_data with source_format -> STRUCTURED_DATA_MIXED_WITH_RAW_TEXT."""
    paper = _make_paper()
    rec_id = _import_bibtex_record(paper.id)
    before = _record(rec_id)
    audits_before = len(_audit_rows(rec_id))
    result = update_citation_record(
        rec_id,
        source_format="ris",
        structured_data={"type": "book", "title": "X"},
    )
    assert result.status == "failed"
    assert result.error_code == STRUCTURED_DATA_MIXED_WITH_RAW_TEXT
    after = _record(rec_id)
    assert after.structured_json == before.structured_json
    assert after.source_format == before.source_format
    assert after.raw_text == before.raw_text
    assert after.parse_status == before.parse_status
    assert len(_audit_rows(rec_id)) == audits_before


def test_update_no_input_is_noop_with_audit():
    """Existing no-input no-op update may remain: audit written, no field change."""
    paper = _make_paper()
    rec_id = _import_bibtex_record(paper.id)
    before = _record(rec_id)
    result = update_citation_record(rec_id)
    assert result.status == "updated", result.error_message
    after = _record(rec_id)
    assert after.structured_json == before.structured_json
    assert after.source_format == before.source_format
    assert after.raw_text == before.raw_text
    assert len(_audit_rows(rec_id)) == 2  # import + no-op update
