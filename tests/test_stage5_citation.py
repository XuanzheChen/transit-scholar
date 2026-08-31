"""Stage 5 automated tests for citation structured import + basic rendering.

Uses the isolated test database from conftest.py (Alembic head on a temp dir).
Papers are constructed directly via ORM — no PDFs, no network, no LLM.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import inspect, select

from transit_scholar.citation import (
    import_citation_record,
    list_citation_records,
    get_selected_citation_record,
    select_citation_record,
    update_citation_record,
    soft_delete_citation_record,
    render_citation,
    list_citation_renders,
)
from transit_scholar.citation.result import (
    CitationActionResult,
    CitationParseResult,
    CitationRecordView,
    CitationRenderResult,
    CitationRenderView,
)
from transit_scholar.citation.service import (
    CITATION_RECORD_NOT_FOUND,
    DATABASE_WRITE_FAILED,
    INVALID_CITATION_CONTENT,
    INVALID_SOURCE_FORMAT,
    INVALID_STATE,
    INVALID_STYLE,
    PAPER_NOT_FOUND,
    PARSE_FAILED,
)
from transit_scholar.db.engine import SessionLocal, engine as _engine
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


# ---------------------------------------------------------------------------
# T01 / T02 / T03 / T04: ORM + migration
# ---------------------------------------------------------------------------


def test_citation_models_in_orm():
    """T01: CitationRecord / CitationRender are registered ORM classes."""
    from transit_scholar.db import models
    assert hasattr(models, "CitationRecord")
    assert hasattr(models, "CitationRender")
    rec_cols = {c.name for c in models.CitationRecord.__table__.columns}
    for required in (
        "id", "paper_id", "source_format", "raw_text", "structured_json",
        "parse_status", "parse_warnings_json", "is_selected", "created_at",
        "updated_at", "deleted_at",
    ):
        assert required in rec_cols, f"missing CitationRecord column: {required}"
    rnd_cols = {c.name for c in models.CitationRender.__table__.columns}
    for required in (
        "id", "citation_record_id", "style", "locale", "rendered_text",
        "renderer_version", "created_at", "updated_at",
    ):
        assert required in rnd_cols, f"missing CitationRender column: {required}"


def test_alembic_creates_twenty_two_tables():
    """Alembic head includes citation, DOI, Workspace, and Stage2 state tables.

    The exact-set assertion remains a database-schema regression guard.
    """
    tables = set(inspect(_engine).get_table_names())
    for required in (
        "papers", "paper_files", "paper_authors", "ingestion_jobs",
        "metadata_candidates", "paper_relations", "audit_logs",
        "citation_records", "citation_renders", "alembic_version",
        "doi_enrichment_jobs", "doi_provider_results",
        "workspaces", "workspace_paper_memberships", "agent_runs", "research_sessions",
        "research_states", "agent_trace_events",
        "research_query_records",
        "evidence_records",
        "claim_records",
        "claim_evidence_links",
    ):
        assert required in tables, f"missing table: {required}"
    assert len(tables) == 22


def test_unique_constraint_on_citation_renders():
    """T03: uq_citation_renders_quad unique constraint is enforced."""
    from sqlalchemy.exc import IntegrityError

    paper = _make_paper()
    import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={
            "type": "article-journal",
            "title": "Sample",
            "author": [{"family": "Lee", "given": "A"}],
            "issued": {"date-parts": [[2024]]},
        },
    )
    with SessionLocal() as session:
        records = session.query(CitationRecord).all()
        assert len(records) == 1
        rec_id = records[0].id
        session.add(CitationRender(
            citation_record_id=rec_id,
            style="apa_7",
            locale="en-US",
            rendered_text="first",
            renderer_version="stage5-basic-v1",
        ))
        session.commit()
        # Insert a second row violating the unique constraint.
        session.add(CitationRender(
            citation_record_id=rec_id,
            style="apa_7",
            locale="en-US",
            rendered_text="second",
            renderer_version="stage5-basic-v1",
        ))
        with pytest.raises(IntegrityError):
            session.commit()


def test_relationships_queryable():
    """T04: Paper.citation_records and CitationRecord.citation_renders are queryable."""
    paper = _make_paper()
    import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={
            "type": "article-journal",
            "title": "Rel Test",
            "author": [{"family": "Wu", "given": "B"}],
        },
    )
    with SessionLocal() as session:
        p = session.get(Paper, paper.id)
        assert len(p.citation_records) == 1
        rec = p.citation_records[0]
        assert rec.paper.id == p.id
        assert rec.citation_renders == []


# ---------------------------------------------------------------------------
# T05-T12: import
# ---------------------------------------------------------------------------


def test_import_manual_structured():
    """T05: manual_structured import succeeds with parse_status=parsed."""
    paper = _make_paper()
    result = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={
            "type": "article-journal",
            "title": "Manual Entry",
            "author": [{"family": "Zhang", "given": "San"}],
            "issued": {"date-parts": [[2024]]},
            "container-title": "Test Journal",
            "volume": "10",
            "issue": "2",
            "page": "1-10",
            "DOI": "10.1234/test",
        },
    )
    assert result.status == "created", result.error_message
    assert result.citation_record_id is not None
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        rec = session.get(CitationRecord, result.citation_record_id)
        assert rec.parse_status == "parsed"
        assert rec.paper_id == paper.id
        structured = json.loads(rec.structured_json)
        assert structured["title"] == "Manual Entry"
        assert structured["container-title"] == "Test Journal"


def test_import_csl_json():
    """T06: csl_json import succeeds."""
    paper = _make_paper()
    csl = {
        "type": "book",
        "title": "CSL Book",
        "author": [{"family": "Smith", "given": "J"}],
        "issued": {"date-parts": [[2020]]},
        "publisher": "Acme",
    }
    result = import_citation_record(
        paper.id,
        source_format="csl_json",
        raw_text=json.dumps(csl),
    )
    assert result.status == "created", result.error_message
    with SessionLocal() as session:
        rec = session.get(CitationRecord, result.citation_record_id)
        assert rec.parse_status == "parsed"
        assert json.loads(rec.structured_json)["type"] == "book"


def test_import_bibtex():
    """T07: bibtex basic parsing succeeds."""
    paper = _make_paper()
    bibtex = (
        '@article{key,\n'
        '  author = {Lee, A. and Kim, B.},\n'
        '  title = {A BibTeX Paper},\n'
        '  journal = {Journal of Examples},\n'
        '  year = {2023},\n'
        '  volume = {5},\n'
        '  number = {1},\n'
        '  pages = {10-20},\n'
        '  doi = {10.5678/bib}\n'
        '}'
    )
    result = import_citation_record(
        paper.id, source_format="bibtex", raw_text=bibtex
    )
    assert result.status == "created", result.error_message
    with SessionLocal() as session:
        rec = session.get(CitationRecord, result.citation_record_id)
        structured = json.loads(rec.structured_json)
        assert structured["title"] == "A BibTeX Paper"
        assert structured["container-title"] == "Journal of Examples"
        assert structured["issued"] == {"date-parts": [[2023]]}


def test_import_ris():
    """T08: ris basic parsing succeeds."""
    paper = _make_paper()
    ris = (
        "TY  - JOUR\n"
        "AU  - Lee, A\n"
        "AU  - Kim, B\n"
        "TI  - An RIS Paper\n"
        "JO  - RIS Journal\n"
        "PY  - 2022\n"
        "VL  - 8\n"
        "IS  - 3\n"
        "SP  - 100\n"
        "EP  - 115\n"
        "DO  - 10.9999/ris\n"
        "ER  - \n"
    )
    result = import_citation_record(
        paper.id, source_format="ris", raw_text=ris
    )
    assert result.status == "created", result.error_message
    with SessionLocal() as session:
        rec = session.get(CitationRecord, result.citation_record_id)
        structured = json.loads(rec.structured_json)
        assert structured["title"] == "An RIS Paper"
        assert structured["container-title"] == "RIS Journal"
        assert structured["issued"] == {"date-parts": [[2022]]}


@pytest.mark.parametrize(
    "source_format,raw_text",
    [
        (
            "apa",
            "Smith, J., & Lee, A. (2024). A Sample APA Paper. "
            "Journal of APA Examples, 12(3), 100-115. https://doi.org/10.1234/apa",
        ),
    ],
)
def test_import_apa_basic_journal_article(source_format, raw_text):
    """T09: apa / mla / gb_t_7714_2025 basic journal article reverse-parse."""
    paper = _make_paper()
    result = import_citation_record(
        paper.id, source_format=source_format, raw_text=raw_text,
    )
    assert result.status == "created", result.error_message
    with SessionLocal() as session:
        rec = session.get(CitationRecord, result.citation_record_id)
        assert rec.parse_status in ("parsed", "partial")
        structured = json.loads(rec.structured_json)
        assert structured["title"] == "A Sample APA Paper"
        assert "issued" in structured


def test_import_mla_basic_journal_article():
    paper = _make_paper()
    mla = (
        'Smith, John, and Alice Lee. "A Sample MLA Paper." '
        "Journal of MLA Examples, vol. 7, no. 2, 2023, pp. 50-65."
    )
    result = import_citation_record(
        paper.id, source_format="mla", raw_text=mla,
    )
    assert result.status == "created", result.error_message
    with SessionLocal() as session:
        rec = session.get(CitationRecord, result.citation_record_id)
        structured = json.loads(rec.structured_json)
        assert structured["title"] == "A Sample MLA Paper"


def test_import_gb_t_basic_journal_article():
    paper = _make_paper()
    gb = (
        "张三, 李四. 一个GB/T论文题名[J]. 中文示例学报, 2024, 15(4): 10-25. "
        "10.1234/gbt"
    )
    result = import_citation_record(
        paper.id, source_format="gb_t_7714_2025", raw_text=gb,
    )
    assert result.status == "created", result.error_message
    with SessionLocal() as session:
        rec = session.get(CitationRecord, result.citation_record_id)
        structured = json.loads(rec.structured_json)
        assert structured["title"] == "一个GB/T论文题名[J]" or "GB/T" in structured["title"]


def test_import_rejects_empty_content():
    """T10: both raw_text and structured_data empty -> INVALID_CITATION_CONTENT."""
    paper = _make_paper()
    result = import_citation_record(paper.id, source_format="manual_structured")
    assert result.status == "failed"
    assert result.error_code == INVALID_CITATION_CONTENT


def test_import_missing_paper():
    """T11: missing paper -> PAPER_NOT_FOUND."""
    result = import_citation_record(
        "doesnotexist1234567890abcdefg",
        source_format="manual_structured",
        structured_data={"type": "book", "title": "x"},
    )
    assert result.status == "failed"
    assert result.error_code == PAPER_NOT_FOUND


def test_import_invalid_source_format():
    """T12: invalid source_format -> INVALID_SOURCE_FORMAT."""
    paper = _make_paper()
    result = import_citation_record(
        paper.id,
        source_format="not_a_format",
        structured_data={"type": "book", "title": "x"},
    )
    assert result.status == "failed"
    assert result.error_code == INVALID_SOURCE_FORMAT


def test_import_rejects_deleted_paper():
    """import_citation_record must reject deleted paper -> INVALID_STATE."""
    paper = _make_paper(status="deleted")
    result = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "book", "title": "x"},
    )
    assert result.status == "failed"
    assert result.error_code == INVALID_STATE


# ---------------------------------------------------------------------------
# T13-T16: list / select
# ---------------------------------------------------------------------------


def test_list_default_excludes_deleted():
    """T13: list_citation_records does not return deleted by default."""
    paper = _make_paper()
    r1 = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T1"},
    )
    r2 = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T2"},
    )
    soft_delete_citation_record(r2.citation_record_id)
    visible = list_citation_records(paper.id)
    assert len(visible) == 1
    assert visible[0].id == r1.citation_record_id


def test_list_include_deleted():
    """T14: include_deleted=True returns deleted records too."""
    paper = _make_paper()
    r1 = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T1"},
    )
    r2 = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T2"},
    )
    soft_delete_citation_record(r2.citation_record_id)
    all_records = list_citation_records(paper.id, include_deleted=True)
    assert len(all_records) == 2


def test_select_exclusive():
    """T15: selecting one record clears others' is_selected for the same paper."""
    paper = _make_paper()
    r1 = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T1"},
        is_selected=True,
    )
    r2 = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T2"},
    )
    select_citation_record(r2.citation_record_id)
    with SessionLocal() as session:
        recs = session.query(CitationRecord).filter(
            CitationRecord.paper_id == paper.id
        ).all()
        selected = [r for r in recs if r.is_selected]
        assert len(selected) == 1
        assert selected[0].id == r2.citation_record_id


def test_select_deleted_rejected():
    """T16: deleted records cannot be selected -> INVALID_STATE."""
    paper = _make_paper()
    r = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T"},
    )
    soft_delete_citation_record(r.citation_record_id)
    result = select_citation_record(r.citation_record_id)
    assert result.status == "failed"
    assert result.error_code == INVALID_STATE


def test_get_selected_citation_record():
    """get_selected_citation_record returns the selected record or None."""
    paper = _make_paper()
    assert get_selected_citation_record(paper.id) is None
    r = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T"},
        is_selected=True,
    )
    selected = get_selected_citation_record(paper.id)
    assert selected is not None
    assert selected.id == r.citation_record_id


# ---------------------------------------------------------------------------
# T17-T19: update / soft delete
# ---------------------------------------------------------------------------


def test_update_reparses_and_audits():
    """T17: update_citation_record re-parses and writes an audit log."""
    paper = _make_paper()
    r = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "Old Title"},
    )
    result = update_citation_record(
        r.citation_record_id,
        structured_data={
            "type": "article-journal",
            "title": "New Title",
            "author": [{"family": "X", "given": "Y"}],
        },
    )
    assert result.status == "updated", result.error_message
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        rec = session.get(CitationRecord, r.citation_record_id)
        assert json.loads(rec.structured_json)["title"] == "New Title"


def test_soft_delete_audits_and_keeps_record():
    """T18: soft delete writes audit and keeps the row."""
    paper = _make_paper()
    r = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T"},
    )
    result = soft_delete_citation_record(r.citation_record_id)
    assert result.status == "deleted", result.error_message
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        rec = session.get(CitationRecord, r.citation_record_id)
        assert rec is not None
        assert rec.deleted_at is not None


def test_update_deleted_rejected():
    """T19: deleted records cannot be updated -> INVALID_STATE."""
    paper = _make_paper()
    r = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "T"},
    )
    soft_delete_citation_record(r.citation_record_id)
    result = update_citation_record(
        r.citation_record_id,
        structured_data={"type": "article-journal", "title": "X"},
    )
    assert result.status == "failed"
    assert result.error_code == INVALID_STATE


# ---------------------------------------------------------------------------
# T20-T26: render
# ---------------------------------------------------------------------------


def _import_sample(paper_id: str) -> str:
    r = import_citation_record(
        paper_id,
        source_format="manual_structured",
        structured_data={
            "type": "article-journal",
            "title": "Transit Signal Priority via Deep Learning",
            "author": [
                {"family": "Zhang", "given": "Wei"},
                {"family": "Li", "given": "Hao"},
            ],
            "issued": {"date-parts": [[2024]]},
            "container-title": "Transportation Research Part C",
            "volume": "156",
            "issue": "3",
            "page": "100-115",
            "DOI": "10.1016/j.trc.2024.01.001",
        },
    )
    return r.citation_record_id


def test_render_apa_7():
    """T20: render_citation produces APA 7 output."""
    paper = _make_paper()
    rec_id = _import_sample(paper.id)
    result = render_citation(rec_id, style="apa_7")
    assert result.status == "rendered", result.error_message
    assert result.rendered_text is not None
    assert "Zhang" in result.rendered_text
    assert "2024" in result.rendered_text
    assert result.audit_log_id is not None


def test_render_mla_9():
    """T21: render_citation produces MLA 9 output."""
    paper = _make_paper()
    rec_id = _import_sample(paper.id)
    result = render_citation(rec_id, style="mla_9")
    assert result.status == "rendered", result.error_message
    assert result.rendered_text is not None
    assert "Zhang" in result.rendered_text


def test_render_gb_t_7714_2025():
    """T22: render_citation produces GB/T 7714-2025 output."""
    paper = _make_paper()
    rec_id = _import_sample(paper.id)
    result = render_citation(rec_id, style="gb_t_7714_2025")
    assert result.status == "rendered", result.error_message
    assert result.rendered_text is not None
    assert "Zhang" in result.rendered_text


def test_render_upserts():
    """T23: re-rendering updates the same CitationRender row instead of adding another."""
    paper = _make_paper()
    rec_id = _import_sample(paper.id)
    render_citation(rec_id, style="apa_7")
    render_citation(rec_id, style="apa_7")
    with SessionLocal() as session:
        count = session.query(CitationRender).filter(
            CitationRender.citation_record_id == rec_id,
            CitationRender.style == "apa_7",
        ).count()
        assert count == 1


def test_render_deleted_rejected():
    """T24: deleted records cannot be rendered -> INVALID_STATE."""
    paper = _make_paper()
    rec_id = _import_sample(paper.id)
    soft_delete_citation_record(rec_id)
    result = render_citation(rec_id, style="apa_7")
    assert result.status == "failed"
    assert result.error_code == INVALID_STATE


def test_render_failed_parse_rejected():
    """T25: parse_status=failed records cannot be rendered."""
    paper = _make_paper()
    # Force a failed record by importing csl_json with empty dict.
    r = import_citation_record(
        paper.id,
        source_format="csl_json",
        raw_text="{}",
    )
    # Empty dict -> no title -> parse_status=failed
    with SessionLocal() as session:
        rec = session.get(CitationRecord, r.citation_record_id)
        assert rec.parse_status == "failed"
    result = render_citation(r.citation_record_id, style="apa_7")
    assert result.status == "failed"
    assert result.error_code == INVALID_STATE


def test_renderer_does_not_read_paper_fields():
    """T26: two papers with different Paper identities but identical structured_json
    produce identical rendered output — proving the renderer reads only structured_json."""
    p1 = _make_paper(title="Completely Different Title A", doi="10.0000/a")
    p2 = _make_paper(title="Completely Different Title B", doi="10.0000/b")
    rec1 = _import_sample(p1.id)
    rec2 = _import_sample(p2.id)
    r1 = render_citation(rec1, style="apa_7", persist=False)
    r2 = render_citation(rec2, style="apa_7", persist=False)
    assert r1.rendered_text == r2.rendered_text
    # And the rendered text must not include the papers' distinct titles.
    assert "Completely Different Title A" not in (r1.rendered_text or "")
    assert "Completely Different Title B" not in (r2.rendered_text or "")


def test_render_invalid_style():
    """render_citation with an invalid style -> INVALID_STYLE."""
    paper = _make_paper()
    rec_id = _import_sample(paper.id)
    result = render_citation(rec_id, style="chicago")
    assert result.status == "failed"
    assert result.error_code == INVALID_STYLE


def test_list_citation_renders():
    """list_citation_renders returns all cached renders for a record."""
    paper = _make_paper()
    rec_id = _import_sample(paper.id)
    render_citation(rec_id, style="apa_7")
    render_citation(rec_id, style="mla_9")
    renders = list_citation_renders(rec_id)
    assert len(renders) == 2
    styles = {r.style for r in renders}
    assert styles == {"apa_7", "mla_9"}


# ---------------------------------------------------------------------------
# T27-T31: isolation / audit / no network / no PDF
# ---------------------------------------------------------------------------


def test_citation_does_not_create_paper_relation():
    """T27: citation operations must not create PaperRelation rows."""
    paper = _make_paper()
    rec_id = _import_sample(paper.id)
    render_citation(rec_id, style="apa_7")
    with SessionLocal() as session:
        assert session.query(PaperRelation).count() == 0


def test_citation_does_not_modify_paper_fields():
    """T28: citation operations must not modify papers.title/doi/normalized_title."""
    paper = _make_paper(title="Original Title", doi="10.0000/original")
    original_title = paper.title
    original_doi = paper.doi
    rec_id = _import_sample(paper.id)
    render_citation(rec_id, style="apa_7")
    update_citation_record(
        rec_id,
        structured_data={
            "type": "article-journal",
            "title": "New Citation Title",
            "author": [{"family": "X", "given": "Y"}],
        },
    )
    with SessionLocal() as session:
        p = session.get(Paper, paper.id)
        assert p.title == original_title
        assert p.doi == original_doi
        assert p.normalized_title != "new citation title"


def test_all_manual_actions_write_audit():
    """T29: import / select / update / soft-delete / render all write audit_logs."""
    paper = _make_paper()
    r = import_citation_record(
        paper.id,
        source_format="manual_structured",
        structured_data={"type": "article-journal", "title": "Audit Test"},
    )
    assert r.audit_log_id is not None
    select_citation_record(r.citation_record_id)
    update_citation_record(
        r.citation_record_id,
        structured_data={"type": "article-journal", "title": "Audit Test 2"},
    )
    render_citation(r.citation_record_id, style="apa_7")
    soft_delete_citation_record(r.citation_record_id)
    with SessionLocal() as session:
        # Five manual actions -> at least 5 audit rows about citation_record.
        rows = session.query(AuditLog).filter(
            AuditLog.entity_type == "citation_record",
        ).all()
        assert len(rows) >= 5


def test_no_network_call_in_parser():
    """T30: parser does not perform any network I/O."""
    import socket

    paper = _make_paper()
    real_socket = socket.socket

    class SocketGuard:
        """Raise if anyone tries to open a real socket."""

        def __init__(self, *args, **kwargs):  # noqa: D401
            raise RuntimeError("network socket opened during parsing")

    socket.socket = SocketGuard  # type: ignore[assignment]
    try:
        import_citation_record(
            paper.id,
            source_format="apa",
            raw_text=(
                "Smith, J. (2024). Network Test. Journal, 1(1), 1-2. "
                "https://doi.org/10.1234/x"
            ),
        )
    finally:
        socket.socket = real_socket  # type: ignore[assignment]


def test_no_pdf_auto_citation_generation():
    """T31: there is no PDF-based citation auto-generation entry point."""
    import transit_scholar.citation as _cit_pkg
    import transit_scholar.citation.service as _cit_svc

    for name in (
        "generate_citation_from_pdf",
        "extract_citation_from_pdf",
        "auto_generate_citation",
    ):
        assert not hasattr(_cit_pkg, name)
        assert not hasattr(_cit_svc, name)
