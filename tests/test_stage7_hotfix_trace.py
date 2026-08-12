"""Stage 7 hotfix Phase 2 — trace service + trace API unit tests.

Covers the read-only trace service and the ``GET /api/papers/{id}/trace``
route. Uses the isolated Alembic-head database from conftest.py.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import fitz
import pytest
from fastapi.exceptions import HTTPException

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    IngestionJob,
    MetadataCandidate,
    Paper,
    PaperFile,
)
from transit_scholar.ingestion.service import import_paper
from transit_scholar.metadata.service import extract_metadata_candidates
from transit_scholar.web.app import paper_trace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_database():
    with SessionLocal() as session:
        session.query(MetadataCandidate).delete()
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


def _make_pdf(project_tmp_path: Path, *, title=None, author=None, text=None) -> Path:
    filename = f"test_{uuid.uuid4().hex}.pdf"
    path = project_tmp_path / filename
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_textbox(fitz.Rect(50, 50, 550, 750), text, fontsize=11)
    if title or author:
        meta = {}
        if title:
            meta["title"] = title
        if author:
            meta["author"] = author
        doc.set_metadata(meta)
    doc.save(str(path))
    doc.close()
    return path


def _import_pdf(project_tmp_path: Path, **kwargs) -> PaperFile:
    pdf_path = _make_pdf(project_tmp_path, **kwargs)
    _config.settings.data_root = project_tmp_path
    result = import_paper(pdf_path)
    assert result.status == "accepted", result.error_message
    with SessionLocal() as session:
        pf = session.get(PaperFile, result.file_id)
        assert pf is not None
        return pf


# ---------------------------------------------------------------------------
# Trace service tests
# ---------------------------------------------------------------------------


def test_trace_returns_paper_trace_result(project_tmp_path):
    """Trace service returns a PaperTraceResult with all required fields."""
    from transit_scholar.workflow.trace import PaperTraceResult, get_paper_trace

    pf = _import_pdf(
        project_tmp_path,
        title="Traceable Paper",
        author="Alice Lee",
        text="DOI: 10.1234/trace\nAbstract\nSome abstract body long enough.\n",
    )
    extract_metadata_candidates(pf.id)

    result = get_paper_trace(pf.paper_id)
    assert result is not None
    assert isinstance(result, PaperTraceResult)
    assert result.paper_id == pf.paper_id
    assert result.paper_status == "active"
    assert result.primary_file_id == pf.id
    assert result.sha256 is not None
    assert result.stored_relative_path is not None
    assert result.file_exists is True
    assert len(result.steps) == 6
    step_names = [s.step for s in result.steps]
    assert step_names == [
        "upload", "hash", "store_original",
        "metadata_extract", "duplicate_check", "second_layer_gate",
    ]
    assert result.ingestion_jobs is not None
    assert result.metadata_summary is not None
    assert result.metadata_candidates is not None
    assert result.second_layer_gate is not None


def test_trace_paper_not_found_returns_none(project_tmp_path):
    """Trace service returns None for a non-existent paper."""
    from transit_scholar.workflow.trace import get_paper_trace

    assert get_paper_trace("doesnotexist1234567890abcdefghijk") is None


def test_trace_metadata_summary_counts(project_tmp_path):
    """Metadata summary reflects candidate counts and top confidence."""
    from transit_scholar.workflow.trace import get_paper_trace

    pf = _import_pdf(
        project_tmp_path,
        title="Summary Title",
        author="Bob",
        text="DOI: 10.9999/summary\n",
    )
    extract_metadata_candidates(pf.id)

    result = get_paper_trace(pf.paper_id)
    assert result is not None
    summary = result.metadata_summary
    assert summary.total_candidates > 0
    assert "title" in summary.fields
    assert summary.fields["title"].candidate_count >= 1
    assert summary.fields["title"].top_confidence is not None


def test_trace_is_readonly(project_tmp_path):
    """Calling the trace service does not mutate the database."""
    from transit_scholar.workflow.trace import get_paper_trace

    pf = _import_pdf(project_tmp_path, title="Readonly Title", author="Cara")
    extract_metadata_candidates(pf.id)

    before = SessionLocal().query(MetadataCandidate).count()
    get_paper_trace(pf.paper_id)
    get_paper_trace(pf.paper_id)
    after = SessionLocal().query(MetadataCandidate).count()
    assert after == before


def test_trace_second_layer_gate_ready_with_metadata_quality_flags(project_tmp_path):
    """A paper missing title/author/year/doi passes the gate: metadata gaps
    are quality flags now, not hard blockers (AC-GATE-01/03)."""
    from transit_scholar.workflow.trace import get_paper_trace

    # No title, no author, no year, no DOI in text.
    pf = _import_pdf(project_tmp_path, text="Some body text without identifiers.\n")
    extract_metadata_candidates(pf.id)

    result = get_paper_trace(pf.paper_id)
    assert result is not None
    gate_step = [s for s in result.steps if s.step == "second_layer_gate"][0]
    assert gate_step.status == "ready"
    assert gate_step.blockers == []


# ---------------------------------------------------------------------------
# Trace API route tests
# ---------------------------------------------------------------------------


def test_trace_api_returns_dict(project_tmp_path):
    """paper_trace handler returns a serializable dict with 6 steps."""
    pf = _import_pdf(project_tmp_path, title="API Trace", author="Dan")
    extract_metadata_candidates(pf.id)

    body = paper_trace(pf.paper_id)
    assert isinstance(body, dict)
    assert body["paper_id"] == pf.paper_id
    assert len(body["steps"]) == 6
    assert "metadata_summary" in body
    assert "metadata_candidates" in body
    assert "second_layer_gate" in body
    assert "ingestion_jobs" in body


def test_trace_api_paper_not_found_raises_404(project_tmp_path):
    """paper_trace handler raises HTTPException 404 for unknown paper."""
    with pytest.raises(HTTPException) as exc_info:
        paper_trace("00000000000000000000000000000000")
    assert exc_info.value.status_code == 404
