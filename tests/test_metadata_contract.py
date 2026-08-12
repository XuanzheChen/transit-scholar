"""AC-META-001..005 contract tests for the metadata extraction slice.

Covers:
- frozen archive-copy provenance (SHA256) and real extraction of the
  Causal reinforcement learning PDF (AC-META-001);
- synthetic affiliation-marker removal and title-prefix protection
  (AC-META-001);
- new-style arXiv month validation 01..12 in first-page and filename paths
  (AC-META-002);
- contextual publication-year extraction with the named plausible range and
  rejection of arbitrary / received years (AC-META-003);
- normalize_title parity across every title candidate/materialization path
  (AC-META-004);
- heuristic source labels that are never relabeled as provider or manual data
  (AC-META-005).

All tests use isolated data roots and make no network calls.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
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
    extract_metadata_candidates,
    normalize_arxiv_id,
    normalize_title,
    parsers,
)
from transit_scholar.metadata.normalizers import (
    is_plausible_publication_year,
    is_valid_arxiv_id,
)
from transit_scholar.metadata.pdf_reader import PdfReadResult, read_pdf
from transit_scholar.metadata.selection import reselect_and_materialize

FROZEN_PDF_RELATIVE = Path("tests") / "fixtures" / "metadata" / (
    "causal_reinforcement_learning_train_scheduling.pdf"
)
FROZEN_PDF_SHA256 = "45bf0f6cf0e3a7b454b6c0d8f741001ef5b43f4bf9568898c48bfaa3f32b7b3a"
FROZEN_PDF_SIZE = 5378176
FROZEN_TITLE = (
    "Causal reinforcement learning for train scheduling on "
    "single-track railway networks"
)
FROZEN_AUTHORS = [
    "Feiyu Yang", "Jixue Liu", "Jiuyong Li", "Lin Liu",
    "Shengjie Wang", "Wenqing Li", "Shaoquan Ni",
]


# ---------------------------------------------------------------------------
# Fixtures / helpers
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


def _frozen_pdf_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / FROZEN_PDF_RELATIVE


def _year_candidates(text: str) -> list[str]:
    read = PdfReadResult(first_pages_text=text)
    return [
        c.value_text
        for c in parsers.parse_first_pages_text(read)
        if c.field_name == "publication_year"
    ]


# ---------------------------------------------------------------------------
# AC-META-001: frozen archive copy provenance + real extraction
# ---------------------------------------------------------------------------


def test_frozen_pdf_fixture_provenance_and_real_extraction():
    """The frozen archive copy is byte-for-byte identical (SHA256) and the
    real first page extracts the exact title and the seven ordered authors."""
    path = _frozen_pdf_path()
    if not path.is_file():
        pytest.skip(
            "frozen archive copy not present; run the approved Copy-Item "
            "command to tests/fixtures/metadata/ before verification"
        )
    assert path.stat().st_size == FROZEN_PDF_SIZE
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FROZEN_PDF_SHA256

    read = read_pdf(path)
    assert read.first_pages_text.strip()

    candidates = parsers.parse_all(read)

    # Title: exactly the visible paper title (whitespace normalization allowed).
    titles = [
        c for c in candidates
        if c.field_name == "title" and c.source_location == "first_page_title_block"
    ]
    assert len(titles) == 1, [c.value_text for c in titles]
    assert normalize_title(titles[0].value_text) == normalize_title(FROZEN_TITLE)

    # AC-META-004: the PDF metadata title (when present) normalizes identically.
    meta_titles = [
        c for c in candidates
        if c.field_name == "title" and c.source_type == "pdf_metadata"
    ]
    for c in meta_titles:
        assert normalize_title(c.value_text) == normalize_title(FROZEN_TITLE)

    # Authors: exactly these seven names, in order, markers removed.
    authors = [
        c for c in candidates
        if c.field_name == "author" and c.source_location == "first_page_author_block"
    ]
    assert [c.value_text for c in authors] == FROZEN_AUTHORS

    # No title fragment, affiliation marker, affiliation line, email, abstract
    # heading, or journal furniture may enter ANY author candidate.
    for c in candidates:
        if c.field_name == "author":
            assert c.value_text in FROZEN_AUTHORS, f"junk author candidate: {c.value_text!r}"


# ---------------------------------------------------------------------------
# AC-META-001: synthetic affiliation markers + title-prefix protection
# ---------------------------------------------------------------------------


def test_synthetic_per_line_affiliation_markers_removed():
    """Per-line author block with single-letter markers ('Feiyu Yang a')."""
    text = (
        "Causal reinforcement learning for train scheduling on single-track railway networks\n"
        "Feiyu Yang a\n"
        "Jixue Liu b\n"
        "Jiuyong Li b\n"
        "Lin Liu b\n"
        "Shengjie Wang c\n"
        "Wenqing Li d\n"
        "Shaoquan Ni e\n"
        "a School of Automation and Intelligence, Beijing Jiaotong University, Beijing, China\n"
        "b UniSA STEM, University of South Australia, Mawson Lakes, SA, Australia\n"
        "Abstract\n"
        "This paper studies causal reinforcement learning for train scheduling.\n"
    )
    read = PdfReadResult(first_pages_text=text)

    title = parsers.parse_first_page_title_block(read)
    assert title is not None
    assert normalize_title(title.value_text) == normalize_title(FROZEN_TITLE)
    assert "Feiyu" not in title.value_text

    authors = parsers.parse_first_page_author_block(read)
    assert [c.value_text for c in authors] == FROZEN_AUTHORS


def test_synthetic_comma_joined_affiliation_markers_removed():
    """Comma-joined author block with single-letter markers."""
    text = (
        "Causal reinforcement learning for train scheduling on single-track railway networks\n"
        "Feiyu Yang a, Jixue Liu b, Jiuyong Li b, Lin Liu b, Shengjie Wang c, "
        "Wenqing Li d, Shaoquan Ni e\n"
        "a School of Automation and Intelligence, Beijing Jiaotong University, Beijing, China\n"
        "Abstract\n"
        "This paper studies causal reinforcement learning for train scheduling.\n"
    )
    read = PdfReadResult(first_pages_text=text)

    title = parsers.parse_first_page_title_block(read)
    assert normalize_title(title.value_text) == normalize_title(FROZEN_TITLE)

    authors = parsers.parse_first_page_author_block(read)
    assert [c.value_text for c in authors] == FROZEN_AUTHORS


def test_corresponding_marker_variants_removed():
    """'a,*' corresponding-author markers and isolated marker fragments."""
    text = (
        "A Test Paper Title\n"
        "Feiyu Yang a,*, Jixue Liu b, Shaoquan Ni e\n"
        "a School of Automation and Intelligence, Beijing, China\n"
        "Abstract\n"
        "Body text here.\n"
    )
    read = PdfReadResult(first_pages_text=text)
    authors = parsers.parse_first_page_author_block(read)
    assert [c.value_text for c in authors] == ["Feiyu Yang", "Jixue Liu", "Shaoquan Ni"]


def test_title_text_never_prepended_to_first_author():
    """The first author candidate never carries a title prefix."""
    text = (
        "Causal reinforcement learning for train scheduling on single-track railway networks\n"
        "Feiyu Yang a\n"
        "Jixue Liu b\n"
        "Abstract\n"
        "Body text here.\n"
    )
    read = PdfReadResult(first_pages_text=text)
    authors = parsers.parse_first_page_author_block(read)
    assert len(authors) == 2
    assert authors[0].value_text == "Feiyu Yang"
    assert "Causal reinforcement" not in authors[0].value_text


# ---------------------------------------------------------------------------
# AC-META-002: arXiv month validation (01-12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arxiv_id", [
    "2301.01234",
    "2301.01234v2",
    "2301.0123",          # 4-digit sequence is accepted
    "2512.12345",
    "9912.00001",
    "2312.12345v9",
    "arXiv:2301.01234",
])
def test_valid_arxiv_ids_accepted(arxiv_id):
    assert is_valid_arxiv_id(arxiv_id) is True


@pytest.mark.parametrize("arxiv_id", [
    "2300.01234",         # month 00
    "0100.01234",         # month 00
    "2313.01234",         # month 13
    "1313.01234",         # month 13
    "2399.01234",         # month 99
    "2025.01234",         # month 25 (year-like ID)
    "2301.123",           # 3-digit sequence
    "2301.123456",        # 6-digit sequence
])
def test_invalid_arxiv_ids_rejected(arxiv_id):
    assert is_valid_arxiv_id(arxiv_id) is False


def test_arxiv_first_page_extraction_rejects_invalid_month():
    read = PdfReadResult(first_pages_text="Preprint: arXiv:2300.01234\nSome text.\n")
    assert not [
        c for c in parsers.parse_first_pages_text(read) if c.field_name == "arxiv_id"
    ]
    read = PdfReadResult(first_pages_text="Preprint: arXiv:2313.01234v1\nSome text.\n")
    assert not [
        c for c in parsers.parse_first_pages_text(read) if c.field_name == "arxiv_id"
    ]
    read = PdfReadResult(first_pages_text="Preprint: arXiv:2301.01234v2\nSome text.\n")
    arxivs = [c for c in parsers.parse_first_pages_text(read) if c.field_name == "arxiv_id"]
    assert [c.value_text for c in arxivs] == ["2301.01234v2"]


def test_arxiv_filename_rejects_invalid_month():
    assert parsers.parse_filename_candidates("2300.01234.pdf") == []
    assert parsers.parse_filename_candidates("2313.01234v1.pdf") == []
    valid = parsers.parse_filename_candidates("2301.01234.pdf")
    assert len(valid) == 1
    assert valid[0].value_text == "2301.01234"
    assert valid[0].source_type == "filename_parser"


def test_normalization_alone_does_not_accept_invalid_month():
    """normalize_arxiv_id is a pure normalizer; the extraction paths enforce
    the month rule, so normalization alone never makes an ID acceptable."""
    assert normalize_arxiv_id("2300.01234") == "2300.01234"
    assert is_valid_arxiv_id("2300.01234") is False
    read = PdfReadResult(first_pages_text="arXiv:2300.01234\n")
    assert not [
        c for c in parsers.parse_first_pages_text(read) if c.field_name == "arxiv_id"
    ]


# ---------------------------------------------------------------------------
# AC-META-003: contextual publication year + plausible range
# ---------------------------------------------------------------------------


def test_year_requires_publication_context():
    assert _year_candidates("Received 5 March 2024\n") == []
    assert _year_candidates("Accepted 10 April 2024\n") == []
    assert _year_candidates("The project started in 2021 and used 2024 rows of data.\n") == []
    assert _year_candidates("DOI: 10.1016/j.trc.2025.105215\n") == []
    assert _year_candidates("arXiv:2205.01234\n") == []


def test_year_publication_signals_win():
    assert _year_candidates("Published 15 June 2024\n") == ["2024"]
    assert _year_candidates("Available online 20 July 2024\n") == ["2024"]
    assert _year_candidates("Copyright 2024 Elsevier\n") == ["2024"]
    assert _year_candidates("VOL. 25, NO. 7, JULY 2024\n") == ["2024"]
    assert _year_candidates(
        "Transportation Research Part C: Emerging Technologies 174 (2025) 106310\n"
    ) == ["2025"]


def test_year_history_line_uses_available_online():
    text = (
        "Article history: Received 8 December 2022; Revised 20 February 2023; "
        "Accepted 10 March 2023; Available online 25 March 2023\n"
    )
    assert _year_candidates(text) == ["2023"]


def test_year_range_edge():
    now_year = datetime.now(timezone.utc).year
    assert is_plausible_publication_year(1899) is False
    assert is_plausible_publication_year(1900) is True
    assert is_plausible_publication_year(now_year) is True
    assert is_plausible_publication_year(now_year + 1) is True
    assert is_plausible_publication_year(now_year + 2) is False
    assert is_plausible_publication_year("2024") is True
    assert is_plausible_publication_year("not-a-year") is False
    assert _year_candidates("Published 1899\n") == []
    assert _year_candidates("Published 2099\n") == []


# ---------------------------------------------------------------------------
# AC-META-004: normalize_title parity across candidate/materialization paths
# ---------------------------------------------------------------------------


def test_title_normalization_parity_metadata_vs_first_page(project_tmp_path):
    """Semantically equivalent PDF-metadata and first-page titles normalize
    identically, and the materialized Paper keeps a human-readable display
    title while normalized_title is derived via normalize_title."""
    text = (
        "Causal reinforcement learning for train scheduling on single track railway networks\n"
        "Feiyu Yang a\n"
        "Abstract\n"
        "This is a long abstract body for the parity test.\n"
    )
    pdf = _make_pdf(
        project_tmp_path,
        title="Causal Reinforcement Learning for Train Scheduling on Single-Track Railway Networks",
        text=text,
    )
    _config.settings.data_root = project_tmp_path
    result = import_paper(pdf)
    assert result.status == "accepted", result.error_message
    extract_metadata_candidates(result.file_id)

    expected = normalize_title(
        "Causal reinforcement learning for train scheduling on single track railway networks"
    )
    with SessionLocal() as session:
        paper = session.get(Paper, result.paper_id)
        # Display title preserved verbatim (not lower-cased).
        assert paper.title == (
            "Causal Reinforcement Learning for Train Scheduling on "
            "Single-Track Railway Networks"
        )
        assert paper.normalized_title == expected
        candidates = session.execute(
            select(MetadataCandidate).where(
                MetadataCandidate.paper_id == paper.id,
                MetadataCandidate.field_name == "title",
            )
        ).scalars().all()
        assert len(candidates) >= 2  # pdf_metadata + first_page_title_block
        normalized = {normalize_title(c.value_text) for c in candidates}
        assert normalized == {expected}


def test_title_selection_materialization_parity():
    """The deterministic selection materialization path also derives
    normalized_title via normalize_title and preserves display text."""
    with SessionLocal() as session:
        paper = Paper(status="active")
        session.add(paper)
        session.flush()
        pf = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            relative_path="library/originals/parity-test/source.pdf",
        )
        session.add(pf)
        session.flush()
        session.add(MetadataCandidate(
            paper_id=paper.id,
            paper_file_id=pf.id,
            field_name="title",
            value_text="  Causal RL for Train Scheduling  ",
            source_type="pdf_metadata",
            source_location="pdf_metadata",
            confidence=0.8,
        ))
        session.flush()
        reselect_and_materialize(session, paper)
        session.commit()

        assert paper.title == "Causal RL for Train Scheduling"
        assert paper.normalized_title == normalize_title("Causal RL for Train Scheduling")


# ---------------------------------------------------------------------------
# AC-META-005: heuristic source labels are preserved
# ---------------------------------------------------------------------------


def test_heuristic_source_labels_preserved(project_tmp_path):
    """Heuristic candidates keep their truthful source labels and are never
    relabeled as provider or manual data."""
    text = (
        "A Realistic Paper Title\n"
        "Feiyu Yang a, Jixue Liu b\n"
        "Abstract\n"
        "A long enough abstract body.\n"
    )
    pf = _import_pdf(
        project_tmp_path,
        title="Meta Title",
        author="Jane Doe",
        filename="2301.01234.pdf",
        text=text,
    )
    extract_metadata_candidates(pf.id)

    with SessionLocal() as session:
        rows = session.execute(
            select(MetadataCandidate).where(MetadataCandidate.paper_id == pf.paper_id)
        ).scalars().all()
        by_source: dict[tuple[str, str], set] = {}
        for c in rows:
            by_source.setdefault((c.field_name, c.source_type), set()).add(c.source_location)

        # Truthful heuristic labels for each heuristic input path.
        assert ("title", "pdf_metadata") in by_source
        assert ("title", "first_pages_text") in by_source
        assert ("author", "pdf_metadata") in by_source
        assert ("author", "first_pages_text") in by_source
        assert ("arxiv_id", "filename_parser") in by_source
        assert ("page_count", "pdf_reader") in by_source

        # No heuristic candidate is ever relabeled as provider/manual data.
        for c in rows:
            assert c.source_type not in ("doi_provider", "manual_confirmed"), c.source_type

        # The filename arXiv candidate carries the exact valid ID.
        arxiv = [c for c in rows if c.field_name == "arxiv_id"]
        assert [c.value_text for c in arxiv] == ["2301.01234"]
