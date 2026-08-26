"""Metadata candidate extraction service.

extract_metadata_candidates(file_id) locates an ingested PDF, reads it with
PyMuPDF, generates candidates via deterministic rules, writes them to
metadata_candidates, updates file facts, and conservatively syncs high-
confidence candidates to papers/paper_authors without overwriting existing values.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from transit_scholar.config import settings
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import IngestionJob, MetadataCandidate, Paper, PaperAuthor, PaperFile
from transit_scholar.metadata import parsers
from transit_scholar.metadata.normalizers import (
    is_plausible_publication_year,
    is_valid_arxiv_id,
    normalize_arxiv_id,
    normalize_author_name,
    normalize_doi,
    normalize_title,
)
from transit_scholar.metadata.pdf_reader import read_pdf
from transit_scholar.metadata.result import MetadataExtractionResult


# Error codes
FILE_NOT_FOUND = "FILE_NOT_FOUND"
RECORD_NOT_FOUND = "RECORD_NOT_FOUND"
PDF_OPEN_FAILED = "PDF_OPEN_FAILED"
DATABASE_WRITE_FAILED = "DATABASE_WRITE_FAILED"


def read_paper_metadata(paper_id: str) -> "PaperMetadata | None":
    """Read the persisted metadata required to build a Wiki paper page."""
    from transit_scholar.layer2.wiki.models import PaperMetadata

    if not isinstance(paper_id, str) or not paper_id:
        return None
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        if paper is None or not isinstance(paper.title, str) or not paper.title.strip():
            return None
        authors = session.execute(
            select(PaperAuthor).where(PaperAuthor.paper_id == paper.id).order_by(PaperAuthor.author_order)
        ).scalars().all()
        return PaperMetadata(
            paper_id=paper.id,
            title=paper.title,
            authors=[author.full_name for author in authors if isinstance(author.full_name, str) and author.full_name.strip()],
            year=paper.publication_year,
        )


def extract_metadata_candidates(
    file_id: str, *, apply_selected: bool = True
) -> MetadataExtractionResult:
    """Extract metadata candidates for an ingested paper file."""
    result = MetadataExtractionResult(
        paper_id=None,
        file_id=file_id,
        status="failed",
        candidates_created=0,
        candidates_seen=0,
        selected_candidate_ids=[],
        updated_paper_fields=[],
        updated_file_fields=[],
        error_code=None,
        error_message=None,
    )

    # --- Step 1: locate the PaperFile --------------------------------------
    with SessionLocal() as session:
        paper_file = session.get(PaperFile, file_id)
        if paper_file is None:
            result.error_code = RECORD_NOT_FOUND
            result.error_message = f"PaperFile not found: {file_id}"
            return result

        if paper_file.paper_id is None or paper_file.relative_path is None:
            result.error_code = RECORD_NOT_FOUND
            result.error_message = f"PaperFile missing paper_id or relative_path: {file_id}"
            return result

        paper_id = paper_file.paper_id
        result.paper_id = paper_id

    # --- Step 2: locate the PDF on disk ------------------------------------
    pdf_path = Path(settings.data_root) / paper_file.relative_path
    if not pdf_path.is_file():
        result.error_code = FILE_NOT_FOUND
        result.error_message = f"PDF not found on disk: {pdf_path}"
        return result

    # --- Step 3: read the PDF ----------------------------------------------
    try:
        read = read_pdf(pdf_path)
    except Exception as exc:  # noqa: BLE001
        result.error_code = PDF_OPEN_FAILED
        result.error_message = f"Failed to read PDF: {exc}"
        return result

    if read.partial and not read.metadata and not read.first_pages_text:
        result.error_code = PDF_OPEN_FAILED
        result.error_message = f"PDF could not be read: {read.partial_messages}"
        return result

    # --- Step 4: generate candidates ----------------------------------------
    candidates = parsers.parse_all(read)
    # Stage 6 supplement: strict arXiv ID from the original file name.
    candidates.extend(
        parsers.parse_filename_candidates(paper_file.original_filename)
    )

    # --- Step 5-8: write candidates, update facts, conservative sync -------
    try:
        with SessionLocal() as session:
            paper_file = session.get(PaperFile, file_id)
            paper = session.get(Paper, paper_id)

            # Step 5: deduplicate and write candidates.
            created = _write_candidates(session, paper_id, file_id, candidates)

            # Step 6: update file facts (also emits candidates).
            file_fact_fields = _update_file_facts(session, paper_file, read)
            result.updated_file_fields = file_fact_fields

            # Flush so _mark_selected can see the just-written candidates
            # (engine uses autoflush=False).
            session.flush()

            # Total candidates seen/created includes both parsed and file facts.
            result.candidates_seen = session.query(MetadataCandidate).filter(
                MetadataCandidate.paper_file_id == file_id
            ).count()
            result.candidates_created = created + sum(
                1 for f in ("page_count", "pdf_version", "is_encrypted", "is_scanned_candidate")
                if _candidate_exists(session, paper_file.id, f, "pdf_reader")
            )

            # Step 7: conservative sync to papers/paper_authors.
            if apply_selected:
                paper_fields, selected_ids = _conservative_sync(
                    session, paper, paper_file, candidates
                )
                result.updated_paper_fields = paper_fields
                result.selected_candidate_ids = selected_ids

            session.commit()
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Database write failed: {exc}"
        return result

    # --- Step 8: set status -------------------------------------------------
    result.status = "partial" if read.partial else "extracted"
    return result


def _write_candidates(
    session, paper_id: str, file_id: str, candidates: list
) -> int:
    """Write candidates, skipping exact duplicates. Returns count created."""
    created = 0
    for c in candidates:
        # Deduplicate on (paper_file_id, field_name, value_text, source_type, source_location).
        existing = session.execute(
            select(MetadataCandidate).where(
                MetadataCandidate.paper_file_id == file_id,
                MetadataCandidate.field_name == c.field_name,
                MetadataCandidate.value_text == c.value_text,
                MetadataCandidate.source_type == c.source_type,
                MetadataCandidate.source_location == c.source_location,
            )
        ).scalar_one_or_none()

        if existing is not None:
            continue

        session.add(MetadataCandidate(
            paper_id=paper_id,
            paper_file_id=file_id,
            field_name=c.field_name,
            value_text=c.value_text,
            source_type=c.source_type,
            source_location=c.source_location,
            confidence=c.confidence,
        ))
        created += 1
    return created


def _update_file_facts(session, paper_file, read) -> list[str]:
    """Update page_count, pdf_version, is_encrypted, is_scanned_candidate.

    Also emits MetadataCandidate records for each file fact so they are
    traceable alongside other candidates. File facts are always emitted;
    deduplication is handled by _emit_file_fact_candidate.
    """
    updated: list[str] = []

    if read.page_count is not None:
        if paper_file.page_count != read.page_count:
            paper_file.page_count = read.page_count
            updated.append("page_count")
        _emit_file_fact_candidate(
            session, paper_file, "page_count", str(read.page_count),
            "pdf_reader", "page_count",
        )

    if read.pdf_version is not None:
        if paper_file.pdf_version != read.pdf_version:
            paper_file.pdf_version = read.pdf_version
            updated.append("pdf_version")
        _emit_file_fact_candidate(
            session, paper_file, "pdf_version", read.pdf_version,
            "pdf_reader", "format",
        )

    if paper_file.is_encrypted != read.is_encrypted:
        paper_file.is_encrypted = read.is_encrypted
        updated.append("is_encrypted")
    _emit_file_fact_candidate(
        session, paper_file, "is_encrypted", str(read.is_encrypted),
        "pdf_reader", "needs_pass",
    )

    if paper_file.is_scanned_candidate != read.is_scanned_candidate:
        paper_file.is_scanned_candidate = read.is_scanned_candidate
        updated.append("is_scanned_candidate")
    _emit_file_fact_candidate(
        session, paper_file, "is_scanned_candidate", str(read.is_scanned_candidate),
        "pdf_reader", "text_heuristic",
    )

    return updated


def _emit_file_fact_candidate(
    session, paper_file, field_name: str, value_text: str,
    source_type: str, source_location: str,
) -> None:
    """Write a file-fact candidate, skipping exact duplicates."""
    existing = session.execute(
        select(MetadataCandidate).where(
            MetadataCandidate.paper_file_id == paper_file.id,
            MetadataCandidate.field_name == field_name,
            MetadataCandidate.value_text == value_text,
            MetadataCandidate.source_type == source_type,
            MetadataCandidate.source_location == source_location,
        )
    ).scalar_one_or_none()

    if existing is not None:
        return

    session.add(MetadataCandidate(
        paper_id=paper_file.paper_id,
        paper_file_id=paper_file.id,
        field_name=field_name,
        value_text=value_text,
        source_type=source_type,
        source_location=source_location,
        confidence=1.0,
    ))


def _conservative_sync(session, paper, paper_file, candidates) -> tuple[list[str], list[str]]:
    """Apply the best-quality candidate per field without overwriting.

    Auto-sync threshold is treated as 0: a candidate enters the candidate set
    based only on basic quality filtering, never on a confidence hard gate.
    Among the candidates that pass quality filtering for a field, the one with
    the highest confidence is selected (stable sort makes ties reproducible).
    Existing values are never overwritten.
    """
    paper_fields: list[str] = []
    selected_ids: list[str] = []

    # Group candidates by field, preserving insertion order for stable ties.
    by_field: dict[str, list] = {}
    for c in candidates:
        by_field.setdefault(c.field_name, []).append(c)

    def best(field: str, quality_fn) -> object | None:
        """Return the highest-confidence candidate passing the quality filter."""
        ranked = [c for c in by_field.get(field, []) if quality_fn(c.value_text)]
        if not ranked:
            return None
        # Stable sort: Python's sort is stable, so equal confidence keeps
        # insertion order, making ties reproducible.
        ranked.sort(key=lambda c: c.confidence, reverse=True)
        return ranked[0]

    # --- DOI (strict rule: normalizable) ---
    if not paper.doi:
        c = best("doi", lambda v: bool(normalize_doi(v)))
        if c is not None:
            normalized = normalize_doi(c.value_text)
            paper.doi = c.value_text.strip()
            paper.normalized_doi = normalized
            paper_fields.extend(["doi", "normalized_doi"])
            rid = _mark_selected(session, paper_file.id, c)
            if rid:
                selected_ids.append(rid)

    # --- arXiv ID (strict rule: normalizable and a valid arXiv ID) ---
    if not paper.arxiv_id:
        c = best(
            "arxiv_id",
            lambda v: bool(normalize_arxiv_id(v)) and is_valid_arxiv_id(v),
        )
        if c is not None:
            normalized = normalize_arxiv_id(c.value_text)
            paper.arxiv_id = normalized
            paper_fields.append("arxiv_id")
            rid = _mark_selected(session, paper_file.id, c)
            if rid:
                selected_ids.append(rid)

    # --- Title (only if empty, passes quality filter) ---
    if not paper.title:
        c = best("title", _passes_title_quality)
        if c is not None:
            paper.title = c.value_text.strip()
            paper.normalized_title = normalize_title(c.value_text)
            paper_fields.extend(["title", "normalized_title"])
            rid = _mark_selected(session, paper_file.id, c)
            if rid:
                selected_ids.append(rid)

    # --- Abstract (only if empty, from explicit Abstract section) ---
    if not paper.abstract:
        c = best("abstract", lambda v: len(v) > 50)
        if c is not None:
            paper.abstract = c.value_text.strip()
            paper_fields.append("abstract")
            rid = _mark_selected(session, paper_file.id, c)
            if rid:
                selected_ids.append(rid)

    # --- Publication year (only if empty, plausible range) ---
    current_year = paper.publication_year
    if not current_year:
        year_candidates = [
            c for c in by_field.get("publication_year", [])
            if is_plausible_publication_year(c.value_text)
        ]
        if year_candidates:
            year_candidates.sort(key=lambda c: c.confidence, reverse=True)
            c = year_candidates[0]
            paper.publication_year = int(c.value_text)
            paper_fields.append("publication_year")
            rid = _mark_selected(session, paper_file.id, c)
            if rid:
                selected_ids.append(rid)

    # --- Authors (only if paper has no authors yet) ---
    existing_authors = session.execute(
        select(PaperAuthor).where(PaperAuthor.paper_id == paper.id)
    ).scalars().all()
    if not existing_authors:
        author_candidates = [c for c in by_field.get("author", []) if _passes_author_quality(c.value_text)]
        if author_candidates:
            # Pick the highest-confidence author source group. All authors in
            # that group are written in their original order.
            author_candidates.sort(key=lambda c: c.confidence, reverse=True)
            best_author = author_candidates[0]
            authors_written = 0
            for c in author_candidates:
                if c.confidence < best_author.confidence:
                    break
                session.add(PaperAuthor(
                    paper_id=paper.id,
                    author_order=authors_written + 1,
                    full_name=c.value_text.strip(),
                    normalized_name=normalize_author_name(c.value_text),
                ))
                authors_written += 1
                rid = _mark_selected(session, paper_file.id, c)
                if rid:
                    selected_ids.append(rid)
            paper_fields.append("authors")

    return paper_fields, selected_ids


def _candidate_exists(
    session, file_id: str, field_name: str, source_type: str
) -> bool:
    """Check whether a file-fact candidate already exists."""
    return session.execute(
        select(MetadataCandidate).where(
            MetadataCandidate.paper_file_id == file_id,
            MetadataCandidate.field_name == field_name,
            MetadataCandidate.source_type == source_type,
        )
    ).scalar_one_or_none() is not None


def _mark_selected(session, file_id: str, candidate) -> str | None:
    """Find the matching candidate record and mark it selected. Returns its id."""
    record = session.execute(
        select(MetadataCandidate).where(
            MetadataCandidate.paper_file_id == file_id,
            MetadataCandidate.field_name == candidate.field_name,
            MetadataCandidate.value_text == candidate.value_text,
            MetadataCandidate.source_type == candidate.source_type,
            MetadataCandidate.source_location == candidate.source_location,
        )
    ).scalar_one_or_none()
    if record is not None:
        record.is_selected = True
        return record.id
    return None


def _passes_title_quality(text: str) -> bool:
    """Basic quality filter for title candidates."""
    t = text.strip()
    if len(t) < 5:
        return False
    if len(t) > 500:
        return False
    # Reject things that look like file paths or URLs.
    if t.startswith(("http://", "https://", "file://")):
        return False
    # Every title materialization path derives normalized_title via
    # normalize_title; candidates that normalize to nothing are not usable.
    if normalize_title(t) is None:
        return False
    return True


def _passes_author_quality(text: str) -> bool:
    """Basic quality filter for author candidates."""
    t = text.strip()
    if len(t) < 2:
        return False
    if len(t) > 200:
        return False
    return True
