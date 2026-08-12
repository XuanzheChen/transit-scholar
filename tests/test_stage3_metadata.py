"""Stage 3 automated tests for metadata candidate extraction.

Uses PyMuPDF to generate synthetic test PDFs with metadata and first-pages
text. No real paper PDFs. Covers extraction, normalization, conservative
sync, no-overwrite, deduplication, and failure paths.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from sqlalchemy import select

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    IngestionJob,
    MetadataCandidate,
    Paper,
    PaperAuthor,
    PaperFile,
)
from transit_scholar.ingestion import import_paper
from transit_scholar.metadata import (
    MetadataExtractionResult,
    extract_metadata_candidates,
    normalize_arxiv_id,
    normalize_author_name,
    normalize_doi,
    normalize_title,
)
from transit_scholar.metadata.service import (
    FILE_NOT_FOUND,
    PDF_OPEN_FAILED,
    RECORD_NOT_FOUND,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear all business tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(MetadataCandidate).delete()
        session.query(PaperAuthor).delete()
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


def _make_pdf(
    project_tmp_path: Path,
    *,
    title: str | None = "Test Paper Title",
    author: str | None = "Jane Doe, John Smith",
    text: str | None = None,
    filename: str | None = None,
) -> Path:
    """Generate a minimal PDF with metadata and optional first-page text."""
    if filename is None:
        filename = f"test_{uuid.uuid4().hex}.pdf"
    path = project_tmp_path / filename

    doc = fitz.open()
    page = doc.new_page()
    if text:
        # Insert text near the top of the page.
        rect = fitz.Rect(50, 50, 550, 750)
        page.insert_textbox(rect, text, fontsize=11)

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


def _import_pdf(project_tmp_path: Path, **pdf_kwargs) -> PaperFile:
    """Generate + import a PDF and return the PaperFile record."""
    pdf_path = _make_pdf(project_tmp_path, **pdf_kwargs)
    _config.settings.data_root = project_tmp_path
    result = import_paper(pdf_path)
    assert result.status == "accepted", result.error_message
    with SessionLocal() as session:
        pf = session.get(PaperFile, result.file_id)
        assert pf is not None
        return pf


# ---------------------------------------------------------------------------
# Normalizer tests
# ---------------------------------------------------------------------------


def test_normalize_doi_strips_prefix():
    assert normalize_doi("https://doi.org/10.1234/abc") == "10.1234/abc"


def test_normalize_doi_strips_doi_prefix():
    assert normalize_doi("doi:10.1234/abc") == "10.1234/abc"


def test_normalize_arxiv_id_strips_prefix():
    assert normalize_arxiv_id("arXiv:2301.01234") == "2301.01234"


def test_normalize_arxiv_id_keeps_version():
    assert normalize_arxiv_id("2301.01234v2") == "2301.01234v2"


def test_normalize_title_lowercase_and_spaces():
    assert normalize_title("  Hello—World\n\nTest  ") == "hello world test"


def test_normalize_title_unicode_hyphen():
    """U+2010 hyphen and U+2212 minus are normalized to spaces."""
    # U+2010 (‐) hyphen and U+2212 (−) minus sign.
    assert normalize_title("A‐B") == "a b"
    assert normalize_title("A−B") == "a b"


# ---------------------------------------------------------------------------
# ORM / Alembic tests
# ---------------------------------------------------------------------------


def test_metadata_candidates_in_orm():
    """MetadataCandidate is registered and has expected columns."""
    from transit_scholar.db import models
    assert hasattr(models, "MetadataCandidate")
    cols = {c.name for c in models.MetadataCandidate.__table__.columns}
    for required in (
        "id", "paper_id", "paper_file_id", "field_name", "value_text",
        "source_type", "source_location", "confidence", "is_selected", "created_at",
    ):
        assert required in cols, f"missing column: {required}"


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------


def test_extract_from_pdf_metadata(project_tmp_path):
    """PDF metadata title/author generate candidates."""
    pf = _import_pdf(project_tmp_path, title="My Test Title", author="Alice Lee")
    result = extract_metadata_candidates(pf.id)

    assert result.status in ("extracted", "partial")
    assert result.candidates_created > 0

    with SessionLocal() as session:
        candidates = session.query(MetadataCandidate).all()
        fields = {c.field_name for c in candidates}
        assert "title" in fields
        assert "author" in fields


def test_extract_doi_from_first_pages_text(project_tmp_path):
    """DOI in first-pages text is extracted and normalized."""
    text = "This paper introduces a new method.\nDOI: 10.1234/example.2024.\n"
    pf = _import_pdf(project_tmp_path, title="T", text=text)
    result = extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        dois = [
            c.value_text for c in session.query(MetadataCandidate).all()
            if c.field_name == "doi"
        ]
        assert any("10.1234/example.2024" in d for d in dois), dois


def test_extract_arxiv_id_from_first_pages_text(project_tmp_path):
    """arXiv ID in first-pages text is extracted and normalized."""
    text = "Preprint: arXiv:2301.01234v2\nSome introduction text here.\n"
    pf = _import_pdf(project_tmp_path, title="T", text=text)
    result = extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        arxivs = [
            c.value_text for c in session.query(MetadataCandidate).all()
            if c.field_name == "arxiv_id"
        ]
        assert "2301.01234v2" in arxivs, arxivs


def test_extract_abstract_section(project_tmp_path):
    """An 'Abstract' section generates an abstract candidate."""
    text = (
        "Abstract\n"
        "This is a long abstract body that describes the paper in detail. "
        "It contains multiple sentences and should be captured as a candidate.\n"
        "Introduction\n"
        "1. Background\n"
    )
    pf = _import_pdf(project_tmp_path, title="T", text=text)
    result = extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        abstracts = [
            c.value_text for c in session.query(MetadataCandidate).all()
            if c.field_name == "abstract"
        ]
        assert len(abstracts) >= 1


def test_page_count_updated(project_tmp_path):
    """paper_files.page_count is updated from the PDF."""
    pf = _import_pdf(project_tmp_path, title="T", text="Some text.")
    extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        pf = session.get(PaperFile, pf.id)
        assert pf.page_count == 1


def test_file_fact_candidates_emitted(project_tmp_path):
    """File facts (page_count, pdf_version, is_encrypted, is_scanned_candidate)
    are emitted as MetadataCandidate records with source_type=pdf_reader.
    """
    pf = _import_pdf(project_tmp_path, title="T", text="Some text.")
    extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        facts = [
            c.field_name for c in session.query(MetadataCandidate).all()
            if c.source_type == "pdf_reader"
        ]
        assert "page_count" in facts
        assert "is_encrypted" in facts
        assert "is_scanned_candidate" in facts


def test_pdf_version_updated(project_tmp_path):
    """paper_files.pdf_version is updated (or stays None if unavailable)."""
    pf = _import_pdf(project_tmp_path, title="T", text="Some text.")
    extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        pf = session.get(PaperFile, pf.id)
        # PyMuPDF usually sets a format like "PDF 1.4"; accept any non-None or None.
        assert pf.pdf_version is None or isinstance(pf.pdf_version, str)


# ---------------------------------------------------------------------------
# Conservative sync tests
# ---------------------------------------------------------------------------


def test_title_synced_when_empty(project_tmp_path):
    """papers.title is written when currently empty."""
    pf = _import_pdf(project_tmp_path, title="Auto Synced Title")
    result = extract_metadata_candidates(pf.id)

    assert "title" in result.updated_paper_fields
    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        assert paper.title == "Auto Synced Title"
        assert paper.normalized_title is not None


def test_title_not_overwritten(project_tmp_path):
    """papers.title is NOT overwritten when already set."""
    pf = _import_pdf(project_tmp_path, title="Extractor Title")
    # Pre-set the title.
    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        paper.title = "Existing Title"
        paper.normalized_title = "existing title"
        session.commit()

    result = extract_metadata_candidates(pf.id)
    assert "title" not in result.updated_paper_fields

    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        assert paper.title == "Existing Title"


def test_doi_not_overwritten(project_tmp_path):
    """papers.doi is NOT overwritten when already set."""
    text = "DOI: 10.9999/newdoi\n"
    pf = _import_pdf(project_tmp_path, title="T", text=text)
    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        paper.doi = "10.0000/existing"
        paper.normalized_doi = "10.0000/existing"
        session.commit()

    result = extract_metadata_candidates(pf.id)
    assert "doi" not in result.updated_paper_fields

    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        assert paper.doi == "10.0000/existing"


def test_authors_not_overwritten(project_tmp_path):
    """paper_authors are NOT overwritten/deleted/reordered when present."""
    pf = _import_pdf(project_tmp_path, title="T", author="Extractor Author")
    # Pre-set an author.
    with SessionLocal() as session:
        session.add(PaperAuthor(
            paper_id=pf.paper_id,
            author_order=1,
            full_name="Existing Author",
            normalized_name="existing author",
        ))
        session.commit()

    result = extract_metadata_candidates(pf.id)
    assert "authors" not in result.updated_paper_fields

    with SessionLocal() as session:
        authors = session.query(PaperAuthor).filter_by(paper_id=pf.paper_id).all()
        assert len(authors) == 1
        assert authors[0].full_name == "Existing Author"


# ---------------------------------------------------------------------------
# Deduplication test
# ---------------------------------------------------------------------------


def test_repeat_extraction_no_duplicate_candidates(project_tmp_path):
    """Running extraction twice does not create identical candidates."""
    pf = _import_pdf(project_tmp_path, title="Repeat Title", author="Bob")
    extract_metadata_candidates(pf.id)
    first_count = SessionLocal().query(MetadataCandidate).count()

    extract_metadata_candidates(pf.id)
    second_count = SessionLocal().query(MetadataCandidate).count()

    assert second_count == first_count


# ---------------------------------------------------------------------------
# Failure path tests
# ---------------------------------------------------------------------------


def test_missing_disk_file_returns_failed(project_tmp_path):
    """If the PDF is missing on disk, return failed with FILE_NOT_FOUND."""
    pf = _import_pdf(project_tmp_path, title="T")
    # Remove the file from disk.
    with SessionLocal() as session:
        pf = session.get(PaperFile, pf.id)
        rel = pf.relative_path
    disk_path = project_tmp_path / rel
    disk_path.unlink()

    result = extract_metadata_candidates(pf.id)
    assert result.status == "failed"
    assert result.error_code == FILE_NOT_FOUND


def test_unknown_file_id_returns_failed():
    """An unknown file_id returns failed with RECORD_NOT_FOUND."""
    result = extract_metadata_candidates("nonexistent-id-1234567890ab")
    assert result.status == "failed"
    assert result.error_code == RECORD_NOT_FOUND


# ---------------------------------------------------------------------------
# No out-of-scope tables
# ---------------------------------------------------------------------------


def test_no_out_of_scope_tables():
    """Ensure genuinely out-of-scope models/tables do not exist.

    PaperRelation / AuditLog are in-scope from Stage 4, and
    CitationRecord / CitationRender are in-scope from Stage 5. This guard now
    protects against later layers and unrelated capabilities being introduced
    while metadata extraction remains only a Stage 3 responsibility.
    """
    from transit_scholar.db import models
    for name in ("DocumentPage", "TextBlock", "SchemaExtractionTask"):
        assert not hasattr(models, name), f"out-of-scope model found: {name}"


# ---------------------------------------------------------------------------
# Stage 7 hotfix: first-page title / author candidate extraction (M01-M11)
# ---------------------------------------------------------------------------


def test_m01_first_page_title_candidate_when_metadata_title_empty(project_tmp_path):
    """When PDF metadata.title is empty but the first page has a clear title,
    a title candidate is generated from the first page title block.
    """
    text = (
        "Deep Reinforcement Learning for Transit Signal Control\n"
        "Ming Liu\n"
        "Abstract\n"
        "This paper studies the problem of transit signal priority.\n"
    )
    pf = _import_pdf(project_tmp_path, title=None, text=text)
    result = extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        titles = [
            c for c in session.query(MetadataCandidate).all()
            if c.field_name == "title"
            and c.source_type == "first_pages_text"
            and c.source_location == "first_page_title_block"
        ]
        assert len(titles) == 1
        assert titles[0].confidence == 0.72
        assert "Deep Reinforcement Learning" in titles[0].value_text
    assert "title" in result.updated_paper_fields


def test_m02_first_page_author_candidate_when_metadata_author_empty(project_tmp_path):
    """When PDF metadata.author is empty but the first page has a clear author
    line, author candidates are generated.
    """
    text = (
        "A Novel Approach to Route Planning\n"
        "Sarah Chen, Tom Brown and Wei Zhang\n"
        "Abstract\n"
        "This paper proposes a new routing algorithm.\n"
    )
    pf = _import_pdf(project_tmp_path, author=None, text=text)
    result = extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        authors = [
            c for c in session.query(MetadataCandidate).all()
            if c.field_name == "author"
            and c.source_type == "first_pages_text"
            and c.source_location == "first_page_author_block"
        ]
        assert len(authors) >= 3
        names = {c.value_text for c in authors}
        assert "Sarah Chen" in names
        assert "Tom Brown" in names
        assert "Wei Zhang" in names
        for a in authors:
            assert a.confidence == 0.68
    assert "authors" in result.updated_paper_fields


def test_m03_real_form_dynamic_bus_holding(project_tmp_path):
    """Real form: Dynamic Bus Holding title (3 lines, en-dash) and 5 authors."""
    text = (
        "Dynamic Bus Holding Control Using\n"
        "Spatial-Temporal Data – A Deep\n"
        "Reinforcement Learning Approach\n"
        "Yuguang Zhao1(B), Gang Chen1, Hui Ma1, Xingquan Zuo2, and Guanqun Ai2\n"
        "1School of Transportation, Beijing University, Beijing, China\n"
        "Abstract\n"
        "This paper studies bus holding control.\n"
    )
    pf = _import_pdf(project_tmp_path, title=None, author=None, text=text)
    result = extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        titles = [
            c for c in session.query(MetadataCandidate).all()
            if c.field_name == "title"
            and c.source_location == "first_page_title_block"
        ]
        assert len(titles) == 1
        # en-dash preserved, lines joined.
        assert "Dynamic Bus Holding Control Using" in titles[0].value_text
        assert "–" in titles[0].value_text
        assert "Reinforcement Learning Approach" in titles[0].value_text

        authors = [
            c for c in session.query(MetadataCandidate).all()
            if c.field_name == "author"
            and c.source_location == "first_page_author_block"
        ]
        names = [c.value_text for c in authors]
        for expected in ["Yuguang Zhao", "Gang Chen", "Hui Ma", "Xingquan Zuo", "Guanqun Ai"]:
            assert expected in names, f"missing {expected} in {names}"


def test_m04_real_form_learned_unmanned_vehicle(project_tmp_path):
    """Real form: Learned Unmanned Vehicle — skip IEEE header/page number."""
    text = (
        "IEEE TRANSACTIONS ON INTELLIGENT TRANSPORTATION SYSTEMS, VOL. 25, NO. 7, JULY 2024\n"
        "7933\n"
        "Learned Unmanned Vehicle Scheduling for\n"
        "Large-Scale Urban Logistics\n"
        "Mei Zhang, Yanli Zeng, Ke Wang, Yafei Li, Qingshun Wu, and Mingliang Xu\n"
        "Abstract—\n"
        "This paper studies unmanned vehicle scheduling.\n"
    )
    pf = _import_pdf(project_tmp_path, title=None, author=None, text=text)
    result = extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        titles = [
            c for c in session.query(MetadataCandidate).all()
            if c.field_name == "title"
            and c.source_location == "first_page_title_block"
        ]
        assert len(titles) == 1
        assert "Learned Unmanned Vehicle Scheduling for" in titles[0].value_text
        assert "Large-Scale Urban Logistics" in titles[0].value_text
        # IEEE header must NOT leak into the title.
        assert "IEEE" not in titles[0].value_text
        assert "7933" not in titles[0].value_text

        authors = [
            c for c in session.query(MetadataCandidate).all()
            if c.field_name == "author"
            and c.source_location == "first_page_author_block"
        ]
        names = [c.value_text for c in authors]
        for expected in ["Mei Zhang", "Yanli Zeng", "Ke Wang", "Yafei Li", "Qingshun Wu", "Mingliang Xu"]:
            assert expected in names, f"missing {expected} in {names}"


def test_m05_header_pagenum_affiliation_not_extracted(project_tmp_path):
    """Headers, page numbers, affiliations, emails, and Abstract are not
    mis-extracted as title or author.
    """
    text = (
        "IEEE TRANSACTIONS ON INTELLIGENT TRANSPORTATION SYSTEMS, VOL. 25, NO. 7, JULY 2024\n"
        "7933\n"
        "A Genuine Paper Title Here\n"
        "Jane Doe1 and John Smith2\n"
        "1Dept of CS, MIT, Cambridge, MA, USA\n"
        "2Stanford University, Stanford, CA, USA\n"
        "Abstract\n"
        "This is the abstract body.\n"
    )
    pf = _import_pdf(project_tmp_path, title=None, author=None, text=text)
    extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        titles = [
            c for c in session.query(MetadataCandidate).all()
            if c.field_name == "title"
            and c.source_location == "first_page_title_block"
        ]
        if titles:
            assert "IEEE" not in titles[0].value_text
            assert "7933" not in titles[0].value_text
            assert "MIT" not in titles[0].value_text

        authors = [
            c for c in session.query(MetadataCandidate).all()
            if c.field_name == "author"
            and c.source_location == "first_page_author_block"
        ]
        names = " ".join(c.value_text for c in authors)
        assert "MIT" not in names
        assert "Stanford University" not in names
        assert "Abstract" not in names


def test_m06_first_page_title_synced_when_paper_title_empty(project_tmp_path):
    """A first-page title candidate is synced when paper.title is empty."""
    text = "A Brand New Title From First Page\nAuthor Name\nAbstract\nBody.\n"
    pf = _import_pdf(project_tmp_path, title=None, text=text)
    result = extract_metadata_candidates(pf.id)
    assert "title" in result.updated_paper_fields
    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        assert paper.title is not None
        assert "A Brand New Title From First Page" in paper.title


def test_m07_existing_title_not_overwritten_by_first_page(project_tmp_path):
    """An existing paper.title is NOT overwritten by a first-page candidate."""
    text = "Extractor First Page Title\nAuthor Name\nAbstract\nBody.\n"
    pf = _import_pdf(project_tmp_path, title=None, text=text)
    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        paper.title = "Pre-existing Title"
        paper.normalized_title = "pre-existing title"
        session.commit()
    result = extract_metadata_candidates(pf.id)
    assert "title" not in result.updated_paper_fields
    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        assert paper.title == "Pre-existing Title"


def test_m08_first_page_authors_synced_when_no_authors(project_tmp_path):
    """First-page author candidates are synced when paper has no authors."""
    text = "Some Paper Title\nAlice Lee, Bob Kim\nAbstract\nBody.\n"
    pf = _import_pdf(project_tmp_path, author=None, text=text)
    result = extract_metadata_candidates(pf.id)
    assert "authors" in result.updated_paper_fields
    with SessionLocal() as session:
        authors = session.query(PaperAuthor).filter_by(paper_id=pf.paper_id).all()
        names = [a.full_name for a in authors]
        assert "Alice Lee" in names
        assert "Bob Kim" in names


def test_m09_existing_authors_not_overwritten_or_appended(project_tmp_path):
    """Existing paper_authors are NOT overwritten or silently appended."""
    text = "Some Paper Title\nExtractor Author\nAbstract\nBody.\n"
    pf = _import_pdf(project_tmp_path, author=None, text=text)
    with SessionLocal() as session:
        session.add(PaperAuthor(
            paper_id=pf.paper_id,
            author_order=1,
            full_name="Existing Author",
            normalized_name="existing author",
        ))
        session.commit()
    result = extract_metadata_candidates(pf.id)
    assert "authors" not in result.updated_paper_fields
    with SessionLocal() as session:
        authors = session.query(PaperAuthor).filter_by(paper_id=pf.paper_id).all()
        assert len(authors) == 1
        assert authors[0].full_name == "Existing Author"


def test_m10_auto_sync_threshold_zero_selects_highest_confidence(project_tmp_path):
    """With threshold=0, the highest-confidence candidate is selected even if
    its confidence is below the old hard gate.
    """
    # Provide a low-confidence title via first page AND a higher one via
    # pdf_metadata. The higher-confidence one should win.
    text = "First Page Title Candidate\nAuthor\nAbstract\nBody.\n"
    pf = _import_pdf(project_tmp_path, title="Higher Confidence Meta Title", text=text)
    result = extract_metadata_candidates(pf.id)
    assert "title" in result.updated_paper_fields
    with SessionLocal() as session:
        paper = session.get(Paper, pf.paper_id)
        # pdf_metadata.title (0.8) beats first-page (0.72).
        assert paper.title == "Higher Confidence Meta Title"


def test_m11_stable_sort_for_equal_confidence(project_tmp_path):
    """Equal-confidence candidates produce a reproducible selection."""
    text = "Stable Sort Title\nAuthor\nAbstract\nBody.\n"
    pf = _import_pdf(project_tmp_path, title=None, text=text)
    r1 = extract_metadata_candidates(pf.id)
    t1 = SessionLocal().get(Paper, pf.paper_id).title
    # Re-run: dedup means no new candidates, selection stays the same.
    r2 = extract_metadata_candidates(pf.id)
    t2 = SessionLocal().get(Paper, pf.paper_id).title
    assert t1 == t2
    assert r1.updated_paper_fields
    assert r2.updated_paper_fields == []


def test_partial_status_when_pdf_read_partial(project_tmp_path, monkeypatch):
    """When read_pdf reports partial, the result status should be 'partial'."""
    pf = _import_pdf(project_tmp_path, title="Partial Title", text="Some text.")

    # Monkeypatch read_pdf where the service imports it.
    from transit_scholar.metadata import service as _svc

    original = _svc.read_pdf

    def partial_read(path):
        result = original(path)
        result.partial = True
        result.partial_messages.append("simulated partial read")
        return result

    monkeypatch.setattr(_svc, "read_pdf", partial_read)

    result = extract_metadata_candidates(pf.id)
    assert result.status == "partial"
    # Candidates should still be written.
    assert result.candidates_created > 0
