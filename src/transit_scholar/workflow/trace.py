"""Read-only trace service for a paper's first-layer data flow.

Answers: this PDF / paper went through which steps, produced which records,
where the file lives, which metadata candidates appeared, and why the second
layer is ready or blocked. Purely read-only: never writes the database, moves
files, or triggers extraction / dedup.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from transit_scholar.config import settings
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    IngestionJob,
    MetadataCandidate,
    Paper,
    PaperFile,
    PaperRelation,
)
from transit_scholar.workflow.result import SecondLayerInputResult
from transit_scholar.workflow.service import get_second_layer_input


# --- trace dataclasses --------------------------------------------------------


@dataclass
class TraceStep:
    step: str
    status: str
    records: list[str]
    details: str | None
    paths: list[str]
    blockers: list[str]


@dataclass
class TraceIngestionJob:
    job_id: str
    status: str
    current_stage: str | None
    is_exact_duplicate: bool


@dataclass
class MetadataFieldSummary:
    field_name: str
    candidate_count: int
    selected: bool
    synced_to_paper: bool | None
    top_confidence: float | None


@dataclass
class MetadataSummary:
    fields: dict[str, MetadataFieldSummary]
    total_candidates: int
    selected_count: int


@dataclass
class PaperTraceResult:
    paper_id: str
    paper_status: str
    primary_file_id: str | None
    original_filename: str | None
    sha256: str | None
    stored_relative_path: str | None
    stored_abs_path: str | None
    file_exists: bool
    ingestion_jobs: list[TraceIngestionJob]
    metadata_summary: MetadataSummary
    metadata_candidates: list[dict]
    duplicate_relations: list[dict]
    second_layer_gate: dict
    steps: list[TraceStep]
    error_code: str | None
    error_message: str | None


# --- synced-to-paper heuristic fields ----------------------------------------

_SYNCED_FIELDS = {
    "title", "author", "doi", "arxiv_id", "publication_year", "abstract",
}


def _build_metadata_summary(
    paper: Paper,
    candidates: list[MetadataCandidate],
) -> MetadataSummary:
    """Build per-field candidate statistics for the trace."""
    by_field: dict[str, list[MetadataCandidate]] = {}
    for c in candidates:
        by_field.setdefault(c.field_name, []).append(c)

    fields: dict[str, MetadataFieldSummary] = {}
    for field_name, items in sorted(by_field.items()):
        top = max(c.confidence for c in items)
        selected = any(c.is_selected for c in items)
        synced = _synced_for_field(paper, field_name, items)
        fields[field_name] = MetadataFieldSummary(
            field_name=field_name,
            candidate_count=len(items),
            selected=selected,
            synced_to_paper=synced,
            top_confidence=top,
        )

    selected_count = sum(1 for c in candidates if c.is_selected)
    return MetadataSummary(
        fields=fields,
        total_candidates=len(candidates),
        selected_count=selected_count,
    )


def _synced_for_field(
    paper: Paper, field_name: str, items: list[MetadataCandidate],
) -> bool | None:
    """Heuristic: has this field's value been synced into papers/paper_authors?"""
    if field_name not in _SYNCED_FIELDS:
        return None
    if field_name == "title":
        return bool(paper.title)
    if field_name == "author":
        return len([c for c in items if c.field_name == "author"]) > 0 and bool(
            paper.authors
        )
    if field_name == "doi":
        return bool(paper.doi)
    if field_name == "arxiv_id":
        return bool(paper.arxiv_id)
    if field_name == "publication_year":
        return paper.publication_year is not None
    if field_name == "abstract":
        return bool(paper.abstract)
    return None


def _build_steps(
    paper: Paper,
    primary: PaperFile | None,
    jobs: list[IngestionJob],
    candidates: list[MetadataCandidate],
    relations: list[PaperRelation],
    gate: SecondLayerInputResult,
) -> list[TraceStep]:
    """Build the six-step process view for the UI."""
    steps: list[TraceStep] = []

    # 1. upload
    if jobs:
        upload_status = "done"
        upload_records = [f"ingestion_jobs:{j.id}" for j in jobs]
    else:
        upload_status = "failed"
        upload_records = []
    steps.append(TraceStep(
        step="upload",
        status=upload_status,
        records=upload_records,
        details=None,
        paths=[],
        blockers=[],
    ))

    # 2. hash
    sha = primary.sha256 if primary else None
    steps.append(TraceStep(
        step="hash",
        status="done",
        records=[f"paper_files.sha256={sha}"] if sha else [],
        details=None,
        paths=[],
        blockers=[],
    ))

    # 3. store_original
    store_paths: list[str] = []
    if primary and primary.relative_path:
        disk = Path(settings.data_root) / primary.relative_path
        store_paths.append(str(disk.resolve()) if disk.exists() else primary.relative_path)
    steps.append(TraceStep(
        step="store_original",
        status="done" if primary and primary.relative_path else "failed",
        records=[],
        details=None,
        paths=store_paths,
        blockers=[],
    ))

    # 4. metadata_extract
    accepted_completed = any(
        j.status == "accepted" and j.current_stage == "completed" for j in jobs
    )
    if not jobs:
        meta_status = "skipped"
    elif accepted_completed:
        meta_status = "done" if len(candidates) > 0 else "partial"
    elif any(j.current_stage == "metadata_failed" for j in jobs):
        meta_status = "failed"
    else:
        meta_status = "partial"
    meta_records = [f"metadata_candidates:{c.id}" for c in candidates[:5]]
    steps.append(TraceStep(
        step="metadata_extract",
        status=meta_status,
        records=meta_records,
        details=f"{len(candidates)} candidate(s)" if candidates else None,
        paths=[],
        blockers=[],
    ))

    # 5. duplicate_check
    dup_status = "done" if (accepted_completed or relations) else "skipped"
    steps.append(TraceStep(
        step="duplicate_check",
        status=dup_status,
        records=[f"paper_relations:{r.id}" for r in relations],
        details=None,
        paths=[],
        blockers=[],
    ))

    # 6. second_layer_gate
    steps.append(TraceStep(
        step="second_layer_gate",
        status=gate.status,
        records=[],
        details=None,
        paths=[],
        blockers=list(gate.blockers),
    ))

    return steps


def get_paper_trace(paper_id: str) -> PaperTraceResult | None:
    """Return a read-only trace for a paper, or None if it does not exist."""
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            return None

        primary = session.execute(
            select(PaperFile).where(
                PaperFile.paper_id == paper_id,
                PaperFile.is_primary == True,  # noqa: E712
            )
        ).scalar_one_or_none()

        jobs = list(session.execute(
            select(IngestionJob).where(IngestionJob.paper_id == paper_id)
        ).scalars().all())

        candidates = list(session.execute(
            select(MetadataCandidate).where(
                MetadataCandidate.paper_id == paper_id,
            ).order_by(MetadataCandidate.field_name, MetadataCandidate.confidence.desc())
        ).scalars().all())

        relations = list(session.execute(
            select(PaperRelation).where(
                (PaperRelation.source_paper_id == paper_id)
                | (PaperRelation.target_paper_id == paper_id)
            )
        ).scalars().all())

        primary_id = primary.id if primary else None
        original_filename = primary.original_filename if primary else None
        sha256 = primary.sha256 if primary else None
        relative_path = primary.relative_path if primary else None

        stored_abs_path: str | None = None
        file_exists = False
        if primary and primary.relative_path:
            disk = Path(settings.data_root) / primary.relative_path
            file_exists = disk.is_file()
            if file_exists:
                stored_abs_path = str(disk.resolve())

        metadata_summary = _build_metadata_summary(paper, candidates)

        metadata_candidates = [
            {
                "id": c.id,
                "paper_id": c.paper_id,
                "paper_file_id": c.paper_file_id,
                "field_name": c.field_name,
                "value_text": c.value_text,
                "source_type": c.source_type,
                "source_location": c.source_location,
                "confidence": c.confidence,
                "is_selected": c.is_selected,
            }
            for c in candidates
        ]

        duplicate_relations = [
            {
                "relation_id": r.id,
                "source_paper_id": r.source_paper_id,
                "target_paper_id": r.target_paper_id,
                "relation_type": r.relation_type,
                "confidence": r.confidence,
                "status": r.status,
            }
            for r in relations
        ]

    # Gate is computed outside the session (it opens its own).
    gate = get_second_layer_input(paper_id)
    second_layer_gate = {
        "status": gate.status,
        "paper_id": gate.paper_id,
        "primary_file_id": gate.primary_file_id,
        "source_pdf_path": gate.source_pdf_path,
        "relative_path": gate.relative_path,
        "title": gate.title,
        "authors": list(gate.authors),
        "year": gate.year,
        "doi": gate.doi,
        "arxiv_id": gate.arxiv_id,
        "page_count": gate.page_count,
        "identity_status": gate.identity_status,
        "duplicate_status": gate.duplicate_status,
        "blockers": list(gate.blockers),
        "error_code": gate.error_code,
        "error_message": gate.error_message,
    }

    steps = _build_steps(paper, primary, jobs, candidates, relations, gate)

    return PaperTraceResult(
        paper_id=paper.id,
        paper_status=paper.status,
        primary_file_id=primary_id,
        original_filename=original_filename,
        sha256=sha256,
        stored_relative_path=relative_path,
        stored_abs_path=stored_abs_path,
        file_exists=file_exists,
        ingestion_jobs=[
            TraceIngestionJob(
                job_id=j.id,
                status=j.status,
                current_stage=j.current_stage,
                is_exact_duplicate=(
                    j.current_stage == "exact_duplicate_check"
                    or j.status == "rejected"
                ),
            )
            for j in jobs
        ],
        metadata_summary=metadata_summary,
        metadata_candidates=metadata_candidates,
        duplicate_relations=duplicate_relations,
        second_layer_gate=second_layer_gate,
        steps=steps,
        error_code=gate.error_code,
        error_message=gate.error_message,
    )
