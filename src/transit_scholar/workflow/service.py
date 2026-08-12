"""Stage 6 aggregation layer service.

Public functions compose the already-frozen Stage 2-6 services into a
first-layer main entry point and stable read interfaces. The import pipeline
runs ingestion -> metadata extraction -> DOI metadata enrichment -> duplicate
detection, and get_second_layer_input implements the read-only second-layer
gate. Hard blockers are file/process-state facts; missing or low-quality
metadata is expressed as non-blocking ``metadata_quality_flags`` so the gate
stays a file-availability + state-convergence + identity-risk check.
reconcile_paper(paper_id) resumes the first-layer tail of an already-imported
paper after manual corrections without re-importing any file. No service logic
is reimplemented here: every call forwards to ingestion / metadata /
doi_enrichment / identity.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from transit_scholar.config import settings
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    IngestionJob,
    Paper,
    PaperFile,
    PaperRelation,
)
from transit_scholar.doi_enrichment.service import (
    collect_provider_results,  # stable test seam: monkeypatched by test_web_contract
    enrich_paper_by_doi,
)

from transit_scholar.identity.service import detect_duplicate_candidates
from transit_scholar.ingestion.service import import_paper
from transit_scholar.metadata.service import extract_metadata_candidates
from transit_scholar.workflow.result import (
    ImportPipelineResult,
    PaperDetail,
    PaperSummary,
    SecondLayerInputResult,
)


# --- frozen metadata quality flag order (AC-GATE-02) --------------------------
# Non-blocking metadata gaps, emitted in exactly this order. These facts are
# quality/risk hints for the second layer, not hard blockers (AC-GATE-001).
METADATA_QUALITY_FLAG_ORDER = (
    "metadata_missing:title",
    "metadata_missing:author",
    "metadata_missing:year",
    "stable_identifier_missing:doi",
    "metadata_missing:abstract",
    "metadata_missing:venue",
    "metadata_missing:arxiv_id",
)

# --- frozen critical duplicate relation types (AC-DUP-01) ---------------------
# Only pending relations of these types keep the second-layer gate blocked.
CRITICAL_DUPLICATE_RELATION_TYPES = ("exact_duplicate", "probable_duplicate")


def run_import_pipeline(file_path: str | Path) -> ImportPipelineResult:
    """End-to-end PDF ingestion pipeline.

    Orchestrates import_paper -> extract_metadata_candidates -> DOI metadata
    enrichment (enrich_paper_by_doi) -> detect_duplicate_candidates, then
    consults the read-only second-layer gate for the final ready/blocked
    verdict. A paper without a DOI stops at current_stage=doi_required before
    enrichment and duplicate detection. Never rewrites the inner logic of any
    stage.
    """
    warnings: list[str] = []

    # --- Step 1: import -------------------------------------------------------
    import_result = import_paper(file_path)

    if import_result.status == "failed":
        return ImportPipelineResult(
            status="failed",
            job_id=import_result.job_id,
            paper_id=None,
            file_id=None,
            is_exact_duplicate=False,
            import_status=import_result.status,
            metadata_status=None,
            duplicate_status=None,
            relations_created=0,
            relations_existing=0,
            relation_ids=[],
            current_stage=None,
            error_code=import_result.error_code,
            error_message=import_result.error_message,
            warnings=warnings,
            second_layer_ready=False,
            second_layer_blockers=["import_failed"],
            metadata_quality_flags=[],
        )

    if import_result.is_exact_duplicate:
        return ImportPipelineResult(
            status="duplicate",
            job_id=import_result.job_id,
            paper_id=import_result.paper_id,
            file_id=import_result.file_id,
            is_exact_duplicate=True,
            import_status=import_result.status,
            metadata_status=None,
            duplicate_status=None,
            relations_created=0,
            relations_existing=0,
            relation_ids=[],
            current_stage="exact_duplicate_check",
            error_code=None,
            error_message=None,
            warnings=warnings,
            second_layer_ready=False,
            second_layer_blockers=["exact_duplicate"],
            metadata_quality_flags=[],
        )

    # --- Step 2: metadata extraction -----------------------------------------
    _update_stage(import_result.job_id, "metadata_extracting")
    meta_result = extract_metadata_candidates(import_result.file_id)

    if meta_result.status in ("failed", "partial"):
        _update_stage(import_result.job_id, "metadata_failed")
        blockers = ["metadata_extraction_failed"]
        if meta_result.status == "partial":
            blockers.append("metadata_processing_pending")
        return ImportPipelineResult(
            status="partial",
            job_id=import_result.job_id,
            paper_id=meta_result.paper_id,
            file_id=meta_result.file_id,
            is_exact_duplicate=False,
            import_status=import_result.status,
            metadata_status=meta_result.status,
            duplicate_status=None,
            relations_created=0,
            relations_existing=0,
            relation_ids=[],
            current_stage="metadata_failed",
            error_code=meta_result.error_code,
            error_message=meta_result.error_message,
            warnings=warnings,
            second_layer_ready=False,
            second_layer_blockers=blockers,
            metadata_quality_flags=[],
        )

    # --- Step 2b: DOI metadata enrichment ------------------------------------
    # After metadata extraction succeeds and before duplicate detection: if the
    # paper has no DOI the identity layer is blocked, so we return partial
    # without creating an enrichment job, running the cascade, or dedup. If a
    # DOI exists we run the cascade and continue to dedup regardless of the
    # enrichment outcome (file import already succeeded; enrichment is best
    # effort). See Phase 1 spec §14/§21.
    # No current_stage update is emitted here: the frozen stage vocabulary has
    # no enrichment stage, so the job keeps its last truthful frozen stage
    # (metadata_extracting) until doi_required or duplicate_checking.
    metadata_enrichment_status: str | None = None
    enrichment_provider_results: list[dict[str, object]] | None = None

    with SessionLocal() as session:
        paper = session.get(Paper, meta_result.paper_id)
        paper_normalized_doi = paper.normalized_doi if paper else None

    if not paper_normalized_doi:
        _update_stage(import_result.job_id, "doi_required")
        return ImportPipelineResult(
            status="partial",
            job_id=import_result.job_id,
            paper_id=meta_result.paper_id,
            file_id=meta_result.file_id,
            is_exact_duplicate=False,
            import_status=import_result.status,
            metadata_status=meta_result.status,
            duplicate_status=None,
            relations_created=0,
            relations_existing=0,
            relation_ids=[],
            current_stage="doi_required",
            error_code=None,
            error_message=None,
            warnings=warnings,
            second_layer_ready=False,
            second_layer_blockers=[],
            metadata_quality_flags=["stable_identifier_missing:doi"],
            metadata_enrichment_status="skipped",
            enrichment_provider_results=[],
        )

    enrichment = enrich_paper_by_doi(meta_result.paper_id)
    metadata_enrichment_status = enrichment.status
    enrichment_provider_results = [
        {
            "provider": p.provider,
            "status": p.status,
            "http_status": p.http_status,
            "fetched_at": p.fetched_at,
            "attempt_count": p.attempt_count,
            "next_retry_at": p.next_retry_at,
            "error_code": p.error_code,
            "fields": p.fields,
        }
        for p in enrichment.providers
    ]

    # --- Step 3: duplicate detection -----------------------------------------
    _update_stage(import_result.job_id, "duplicate_checking")
    dup_result = detect_duplicate_candidates(meta_result.paper_id)

    if dup_result.status == "failed":
        # The paper exists, so its metadata quality flags stay visible
        # alongside the pipeline-level duplicate-detection blocker.
        quality_flags = _paper_quality_flags(meta_result.paper_id)
        return ImportPipelineResult(
            status="partial",
            job_id=import_result.job_id,
            paper_id=meta_result.paper_id,
            file_id=meta_result.file_id,
            is_exact_duplicate=False,
            import_status=import_result.status,
            metadata_status=meta_result.status,
            duplicate_status=dup_result.status,
            relations_created=0,
            relations_existing=0,
            relation_ids=[],
            current_stage="duplicate_checking",
            error_code=dup_result.error_code,
            error_message=dup_result.error_message,
            warnings=warnings,
            second_layer_ready=False,
            second_layer_blockers=["duplicate_detection_failed"],
            metadata_quality_flags=quality_flags,
            metadata_enrichment_status=metadata_enrichment_status,
            enrichment_provider_results=enrichment_provider_results,
        )

    if dup_result.relations_created > 0:
        _update_stage(import_result.job_id, "awaiting_user_review")
        return ImportPipelineResult(
            status="awaiting_user_review",
            job_id=import_result.job_id,
            paper_id=meta_result.paper_id,
            file_id=meta_result.file_id,
            is_exact_duplicate=False,
            import_status=import_result.status,
            metadata_status=meta_result.status,
            duplicate_status=dup_result.status,
            relations_created=dup_result.relations_created,
            relations_existing=dup_result.relations_existing,
            relation_ids=dup_result.relation_ids,
            current_stage="awaiting_user_review",
            error_code=None,
            error_message=None,
            warnings=warnings,
            second_layer_ready=False,
            second_layer_blockers=["pending_duplicate_review"],
            metadata_quality_flags=[],
            metadata_enrichment_status=metadata_enrichment_status,
            enrichment_provider_results=enrichment_provider_results,
        )

    _update_stage(import_result.job_id, "completed")

    # Honor the second-layer gate: pipeline status stays completed, but
    # second_layer_ready follows the independent gate verdict.
    gate = get_second_layer_input(meta_result.paper_id)
    second_layer_ready = gate.status == "ready"
    second_layer_blockers = list(gate.blockers)
    metadata_quality_flags = list(gate.metadata_quality_flags)

    return ImportPipelineResult(
        status="completed",
        job_id=import_result.job_id,
        paper_id=meta_result.paper_id,
        file_id=meta_result.file_id,
        is_exact_duplicate=False,
        import_status=import_result.status,
        metadata_status=meta_result.status,
        duplicate_status=dup_result.status,
        relations_created=0,
        relations_existing=dup_result.relations_existing,
        relation_ids=dup_result.relation_ids,
        current_stage="completed",
        error_code=None,
        error_message=None,
        warnings=warnings,
        second_layer_ready=second_layer_ready,
        second_layer_blockers=second_layer_blockers,
        metadata_quality_flags=metadata_quality_flags,
        metadata_enrichment_status=metadata_enrichment_status,
        enrichment_provider_results=enrichment_provider_results,
    )


def reconcile_paper(paper_id: str) -> ImportPipelineResult:
    """Resume the first-layer tail of an already-imported paper (AC-RECONCILE).

    Entry point for the manual-correction loop: after a user fixes metadata,
    refreshes DOI providers or adjudicates duplicate relations, this resumes
    from the persisted paper instead of re-importing the file. It never calls
    ``import_paper``, never recomputes SHA256, and never copies, moves or
    deletes any PDF nor creates ``Paper`` / ``PaperFile`` rows. Metadata
    convergence is allowed only when the primary file has no
    accepted/completed ingestion job: in that case
    ``extract_metadata_candidates(file_id)`` runs and an accepted/completed
    job records the convergence. ``current_stage`` always stays inside the
    frozen ingestion vocabulary; the transient ``doi_enrichment`` stage is
    never emitted.

    Steps (AC-RECONCILE-003):

    1. validate the paper and its primary file (state, deletion, disk source);
    2. converge metadata candidates only when not already converged;
    3. run DOI enrichment when a DOI exists; without a DOI enrichment is
       skipped and reported via the ``stable_identifier_missing:doi`` flag;
    4. run duplicate detection; existing relations are counted, never rebuilt;
    5. return the latest ``get_second_layer_input()`` verdict.
    """
    warnings: list[str] = []
    enrichment_provider_results: list[dict[str, object]] | None = None
    flags: list[str] = []

    # --- Step 1: validate the paper and its primary file -------------------
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            return _reconcile_result(
                status="failed",
                paper_id=paper_id,
                import_status=None,
                current_stage=None,
                error_code="PAPER_NOT_FOUND",
                error_message=f"Paper not found: {paper_id}",
                second_layer_blockers=["paper_not_found"],
            )
        flags = _compute_metadata_quality_flags(session, paper)

        if paper.status != "active":
            return _reconcile_result(
                status="failed",
                paper_id=paper_id,
                current_stage=None,
                error_code="PAPER_NOT_ACTIVE",
                error_message=f"Paper is not active: {paper.status}",
                second_layer_blockers=[f"paper_not_active:{paper.status}"],
                metadata_quality_flags=flags,
            )

        primary = session.execute(
            select(PaperFile).where(
                PaperFile.paper_id == paper_id,
                PaperFile.is_primary == True,  # noqa: E712
            )
        ).scalar_one_or_none()
        if primary is None:
            return _reconcile_result(
                status="failed",
                paper_id=paper_id,
                current_stage=None,
                error_code="NO_PRIMARY_FILE",
                error_message="Paper has no primary file",
                second_layer_blockers=["no_primary_file"],
                metadata_quality_flags=flags,
            )
        if primary.deleted_at is not None:
            return _reconcile_result(
                status="failed",
                paper_id=paper_id,
                file_id=primary.id,
                current_stage=None,
                error_code="PRIMARY_FILE_DELETED",
                error_message="Primary file is soft-deleted",
                second_layer_blockers=["primary_file_deleted"],
                metadata_quality_flags=flags,
            )
        if not primary.relative_path or not (
            Path(settings.data_root) / primary.relative_path
        ).is_file():
            return _reconcile_result(
                status="failed",
                paper_id=paper_id,
                file_id=primary.id,
                current_stage=None,
                error_code="SOURCE_FILE_MISSING",
                error_message=(
                    "Source PDF missing on disk"
                    if primary.relative_path
                    else "Primary file has no relative path"
                ),
                second_layer_blockers=["source_file_missing"],
                metadata_quality_flags=flags,
            )
        file_id = primary.id
        metadata_converged = _metadata_blocker(session, primary) is None

    # --- Step 2: converge metadata candidates when needed -------------------
    metadata_status: str | None = "completed"
    if not metadata_converged:
        meta_result = extract_metadata_candidates(file_id)
        if meta_result.status in ("failed", "partial"):
            blockers = ["metadata_extraction_failed"]
            if meta_result.status == "partial":
                blockers.append("metadata_processing_pending")
            return _reconcile_result(
                status="failed",
                paper_id=paper_id,
                file_id=file_id,
                metadata_status=meta_result.status,
                current_stage="metadata_failed",
                error_code="METADATA_EXTRACTION_FAILED",
                error_message=(
                    f"Metadata extraction failed: "
                    f"{meta_result.error_message or meta_result.status}"
                ),
                second_layer_blockers=blockers,
            )
        # Record the convergence as an accepted/completed ingestion job so the
        # gate sees a converged metadata path on later calls (idempotent).
        with SessionLocal() as session:
            session.add(IngestionJob(
                uploaded_filename=(
                    session.get(PaperFile, file_id).original_filename
                    if session.get(PaperFile, file_id)
                    else None
                ),
                file_id=file_id,
                paper_id=paper_id,
                status="accepted",
                current_stage="completed",
            ))
            session.commit()
        metadata_status = meta_result.status

    # --- Step 3: DOI enrichment (skipped without a DOI) ---------------------
    metadata_enrichment_status: str | None
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper_has_doi = bool(paper and paper.normalized_doi)
    if not paper_has_doi:
        metadata_enrichment_status = "skipped"
        warnings.append(
            "No DOI present; DOI enrichment skipped "
            "(stable_identifier_missing:doi quality flag)"
        )
    else:
        enrichment = enrich_paper_by_doi(paper_id)
        metadata_enrichment_status = enrichment.status
        enrichment_provider_results = [
            {
                "provider": p.provider,
                "status": p.status,
                "http_status": p.http_status,
                "fetched_at": p.fetched_at,
                "attempt_count": p.attempt_count,
                "next_retry_at": p.next_retry_at,
                "error_code": p.error_code,
                "fields": p.fields,
            }
            for p in enrichment.providers
        ]

    # --- Step 4: duplicate detection ----------------------------------------
    dup_result = detect_duplicate_candidates(paper_id)

    if dup_result.status == "failed":
        return _reconcile_result(
            status="partial",
            paper_id=paper_id,
            file_id=file_id,
            metadata_status=metadata_status,
            metadata_enrichment_status=metadata_enrichment_status,
            duplicate_status=dup_result.status,
            current_stage="duplicate_checking",
            error_code=dup_result.error_code,
            error_message=(
                f"Duplicate detection failed: {dup_result.error_message}"
            ),
            second_layer_blockers=["duplicate_detection_failed"],
            metadata_quality_flags=_paper_quality_flags(paper_id),
            warnings=warnings,
            enrichment_provider_results=enrichment_provider_results,
        )

    # --- Step 5: pending critical relations -> awaiting user review ---------
    with SessionLocal() as session:
        pending_critical = session.execute(
            select(PaperRelation).where(
                PaperRelation.status == "pending",
                PaperRelation.relation_type.in_(CRITICAL_DUPLICATE_RELATION_TYPES),
                (PaperRelation.source_paper_id == paper_id)
                | (PaperRelation.target_paper_id == paper_id),
            )
        ).scalars().first()
    if pending_critical is not None or dup_result.relations_created > 0:
        return _reconcile_result(
            status="awaiting_user_review",
            paper_id=paper_id,
            file_id=file_id,
            metadata_status=metadata_status,
            metadata_enrichment_status=metadata_enrichment_status,
            duplicate_status=dup_result.status,
            relations_created=dup_result.relations_created,
            relations_existing=dup_result.relations_existing,
            relation_ids=dup_result.relation_ids,
            current_stage="awaiting_user_review",
            error_code=None,
            error_message="Awaiting user review of pending duplicate relations",
            second_layer_blockers=["pending_duplicate_review"],
            warnings=warnings,
            enrichment_provider_results=enrichment_provider_results,
        )

    # --- Step 6: completed; the latest gate verdict is written truthfully ---
    gate = get_second_layer_input(paper_id)
    return _reconcile_result(
        status="completed",
        paper_id=paper_id,
        file_id=file_id,
        metadata_status=metadata_status,
        metadata_enrichment_status=metadata_enrichment_status,
        duplicate_status=dup_result.status,
        relations_existing=dup_result.relations_existing,
        relation_ids=dup_result.relation_ids,
        current_stage="completed",
        error_code=None,
        error_message="Reconcile completed",
        second_layer_ready=gate.status == "ready",
        second_layer_blockers=list(gate.blockers),
        metadata_quality_flags=list(gate.metadata_quality_flags),
        warnings=warnings,
        enrichment_provider_results=enrichment_provider_results,
    )


def list_papers(
    *,
    status: str | None = None,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[PaperSummary]:
    """Read-only list of papers. Does not mutate any state."""
    with SessionLocal() as session:
        stmt = select(Paper)
        if status is not None:
            stmt = stmt.where(Paper.status == status)
        if not include_deleted:
            stmt = stmt.where(Paper.deleted_at.is_(None))
        stmt = stmt.order_by(Paper.created_at.desc()).limit(limit).offset(offset)
        rows = session.execute(stmt).scalars().all()

        summaries: list[PaperSummary] = []
        for paper in rows:
            primary_file_id = _primary_file_id(session, paper.id)
            summaries.append(PaperSummary(
                paper_id=paper.id,
                title=paper.title,
                publication_year=paper.publication_year,
                venue=paper.venue,
                doi=paper.doi,
                arxiv_id=paper.arxiv_id,
                status=paper.status,
                primary_file_id=primary_file_id,
                created_at=paper.created_at,
                updated_at=paper.updated_at,
            ))
        return summaries


def get_paper(paper_id: str) -> PaperDetail | None:
    """Read-only paper detail. Returns None when not found. No state change."""
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            return None

        authors = [
            {
                "author_order": a.author_order,
                "full_name": a.full_name,
                "affiliation": a.affiliation,
                "orcid": a.orcid,
            }
            for a in paper.authors
        ]

        files = [
            {
                "file_id": f.id,
                "original_filename": f.original_filename,
                "is_primary": f.is_primary,
                "relative_path": f.relative_path,
                "sha256": f.sha256,
                "deleted_at": f.deleted_at,
            }
            for f in paper.files
        ]

        relations = session.execute(
            select(PaperRelation).where(
                (PaperRelation.source_paper_id == paper_id)
                | (PaperRelation.target_paper_id == paper_id)
            )
        ).scalars().all()
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

        return PaperDetail(
            paper_id=paper.id,
            title=paper.title,
            normalized_title=paper.normalized_title,
            abstract=paper.abstract,
            publication_year=paper.publication_year,
            venue=paper.venue,
            doi=paper.doi,
            normalized_doi=paper.normalized_doi,
            arxiv_id=paper.arxiv_id,
            status=paper.status,
            authors=authors,
            files=files,
            duplicate_relations=duplicate_relations,
            created_at=paper.created_at,
            updated_at=paper.updated_at,
            deleted_at=paper.deleted_at,
        )


def get_second_layer_input(paper_id: str) -> SecondLayerInputResult:
    """Read-only gate for the second layer.

    Hard blockers come only from the frozen gate vocabulary in acceptance.json
    and are emitted in canonical vocabulary order: paper state, file state,
    source existence, metadata processing, then duplicate convergence. Metadata
    gaps (DOI, title, author, year, abstract, venue, arXiv) are NOT blockers:
    whenever the paper row exists they are computed into
    ``metadata_quality_flags`` in the frozen order and returned alongside the
    blockers, so a paper with only quality issues is ``ready``. A missing DOI
    is always ``stable_identifier_missing:doi`` (a quality flag) — arXiv never
    substitutes and the check is independent of the other metadata fields. The
    gate is read-only and never mutates, clears, or replaces persisted
    enrichment provider records.
    """
    blockers: list[str] = []
    source_pdf_path: str | None = None
    relative_path: str | None = None

    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            return _blocked(paper_id, blockers=["paper_not_found"], flags=[])

        # Quality flags are computed for every existing paper row and returned
        # alongside the blockers (AC-GATE-04).
        flags = _compute_metadata_quality_flags(session, paper)

        if paper.status != "active":
            blockers.append(f"paper_not_active:{paper.status}")

        primary = session.execute(
            select(PaperFile).where(
                PaperFile.paper_id == paper_id,
                PaperFile.is_primary == True,  # noqa: E712
            )
        ).scalar_one_or_none()

        if primary is None:
            blockers.append("no_primary_file")
        else:
            if primary.deleted_at is not None:
                blockers.append("primary_file_deleted")

            # Source-file existence: the selected primary file must resolve to
            # a file that exists on disk. A record without a relative_path
            # cannot locate a source, so it blocks with the same frozen fact.
            if primary.relative_path:
                disk = Path(settings.data_root) / primary.relative_path
                if not disk.is_file():
                    blockers.append("source_file_missing")
                else:
                    source_pdf_path = str(disk.resolve())
                    relative_path = primary.relative_path
            else:
                blockers.append("source_file_missing")

            # Metadata processing state only applies to a usable (non-deleted)
            # primary file; a missing primary never gains an invented generic
            # metadata blocker.
            if primary.deleted_at is None:
                metadata_blocker = _metadata_blocker(session, primary)
                if metadata_blocker is not None:
                    blockers.append(metadata_blocker)

        # Snapshot scalar values while the session is still open to avoid
        # lazy-loading relationships after the session closes.
        paper_title = paper.title
        paper_year = paper.publication_year
        paper_doi = paper.doi
        paper_arxiv = paper.arxiv_id
        paper_status = paper.status
        primary_id = primary.id if primary else None
        page_count = primary.page_count if primary else None
        author_names = [
            a.full_name.strip()
            for a in paper.authors
            if a.full_name and a.full_name.strip()
        ]

        # Duplicate convergence: only a pending critical relation (exact or
        # probable duplicate, AC-DUP-01) keeps the gate blocked until the user
        # resolves it. Confirmed/rejected/ignored relations and non-critical
        # pending relation types never block (AC-DUP-02).
        pending_critical = session.execute(
            select(PaperRelation).where(
                PaperRelation.status == "pending",
                PaperRelation.relation_type.in_(CRITICAL_DUPLICATE_RELATION_TYPES),
                (PaperRelation.source_paper_id == paper_id)
                | (PaperRelation.target_paper_id == paper_id),
            )
        ).scalars().first()
        if pending_critical is not None:
            blockers.append("pending_duplicate_review")

    if blockers:
        return _blocked(paper_id, blockers=blockers, flags=flags)

    return SecondLayerInputResult(
        status="ready",
        paper_id=paper_id,
        primary_file_id=primary_id,
        source_pdf_path=source_pdf_path,
        relative_path=relative_path,
        title=paper_title,
        authors=author_names,
        year=paper_year,
        doi=paper_doi,
        arxiv_id=paper_arxiv,
        page_count=page_count,
        identity_status=paper_status,
        duplicate_status=paper_status,
        blockers=[],
        metadata_quality_flags=flags,
        error_code=None,
        error_message=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blocked(
    paper_id: str, blockers: list[str], flags: list[str] | None = None
) -> SecondLayerInputResult:
    return SecondLayerInputResult(
        status="blocked",
        paper_id=paper_id,
        primary_file_id=None,
        source_pdf_path=None,
        relative_path=None,
        title=None,
        authors=[],
        year=None,
        doi=None,
        arxiv_id=None,
        page_count=None,
        identity_status=None,
        duplicate_status=None,
        blockers=blockers,
        metadata_quality_flags=flags or [],
        error_code=blockers[0] if blockers else None,
        error_message=None,
    )


def _compute_metadata_quality_flags(session, paper: Paper) -> list[str]:
    """Compute the frozen-ordered, non-blocking metadata quality flags.

    Judgment per AC-GATE-02: title/abstract/venue/arxiv blank means missing;
    author means an empty list of stripped non-empty full names; year missing
    only when ``None``; DOI missing when both ``paper.doi`` and
    ``paper.normalized_doi`` are empty (arXiv never substitutes). Returns the
    flags in the frozen order, a subset of ``METADATA_QUALITY_FLAG_ORDER``.
    """
    flags: list[str] = []

    if not (paper.title and paper.title.strip()):
        flags.append("metadata_missing:title")

    author_names = [
        a.full_name.strip()
        for a in paper.authors
        if a.full_name and a.full_name.strip()
    ]
    if not author_names:
        flags.append("metadata_missing:author")

    if paper.publication_year is None:
        flags.append("metadata_missing:year")

    if not (paper.doi and paper.doi.strip()) and not (
        paper.normalized_doi and paper.normalized_doi.strip()
    ):
        flags.append("stable_identifier_missing:doi")

    if not (paper.abstract and paper.abstract.strip()):
        flags.append("metadata_missing:abstract")

    if not (paper.venue and paper.venue.strip()):
        flags.append("metadata_missing:venue")

    if not (paper.arxiv_id and paper.arxiv_id.strip()):
        flags.append("metadata_missing:arxiv_id")

    return flags


def _paper_quality_flags(paper_id: str) -> list[str]:
    """Compute quality flags for an existing paper in a fresh session."""
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            return []
        return _compute_metadata_quality_flags(session, paper)


def _reconcile_result(
    *,
    status: str,
    paper_id: str,
    file_id: str | None = None,
    import_status: str | None = "accepted",
    metadata_status: str | None = None,
    metadata_enrichment_status: str | None = None,
    duplicate_status: str | None = None,
    relations_created: int = 0,
    relations_existing: int = 0,
    relation_ids: list[str] | None = None,
    current_stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    second_layer_ready: bool = False,
    second_layer_blockers: list[str] | None = None,
    metadata_quality_flags: list[str] | None = None,
    warnings: list[str] | None = None,
    enrichment_provider_results: list[dict[str, object]] | None = None,
) -> ImportPipelineResult:
    """Build an ImportPipelineResult for reconcile_paper().

    reconcile_paper() runs no ingestion job, so ``job_id`` is always None and
    ``is_exact_duplicate`` is always False.
    """
    return ImportPipelineResult(
        status=status,
        job_id=None,
        paper_id=paper_id,
        file_id=file_id,
        is_exact_duplicate=False,
        import_status=import_status,
        metadata_status=metadata_status,
        duplicate_status=duplicate_status,
        relations_created=relations_created,
        relations_existing=relations_existing,
        relation_ids=relation_ids or [],
        current_stage=current_stage,
        error_code=error_code,
        error_message=error_message,
        warnings=warnings or [],
        second_layer_ready=second_layer_ready,
        second_layer_blockers=second_layer_blockers or [],
        metadata_quality_flags=metadata_quality_flags or [],
        metadata_enrichment_status=metadata_enrichment_status,
        enrichment_provider_results=enrichment_provider_results,
    )


def _primary_file_id(session, paper_id: str) -> str | None:
    pf = session.execute(
        select(PaperFile).where(
            PaperFile.paper_id == paper_id,
            PaperFile.is_primary == True,  # noqa: E712
            PaperFile.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    return pf.id if pf else None


def _metadata_blocker(session, primary: PaperFile) -> str | None:
    """Return a blocker string if metadata processing is not completed.

    Called only for a usable (non-deleted) primary file. Any state other than
    an accepted/completed ingestion job is ``metadata_processing_pending``; an
    explicit metadata failure maps to ``metadata_extraction_failed``.
    """
    jobs = session.execute(
        select(IngestionJob).where(IngestionJob.file_id == primary.id)
    ).scalars().all()
    if not jobs:
        return "metadata_processing_pending"

    # A primary file can have more than one ingestion job: the first successful
    # import creates the file, while later exact-duplicate imports may point at
    # the same file_id with current_stage="exact_duplicate_check". For the
    # second-layer gate, any completed accepted job proves this file's metadata
    # path has already finished; duplicate-attempt jobs must not block it.
    if any(job.status == "accepted" and job.current_stage == "completed" for job in jobs):
        return None

    if any(job.current_stage == "metadata_failed" for job in jobs):
        return "metadata_extraction_failed"
    return "metadata_processing_pending"


def _update_stage(job_id: str | None, stage: str) -> None:
    """Set ingestion_jobs.current_stage in its own transaction."""
    if job_id is None:
        return
    with SessionLocal() as session:
        job = session.get(IngestionJob, job_id)
        if job is not None:
            job.current_stage = stage
            session.commit()
