"""Second-layer gate contract tests (AC-GATE-001..005, AC-DUP-001..003).

Every producible frozen blocker from acceptance.md has an isolated fixture
that asserts the exact ordered blocker list, plus a fully-ready fixture.
Metadata gaps (DOI, title, author, year, abstract, venue, arXiv) are NOT
hard blockers: they are computed into ``metadata_quality_flags`` in the
frozen order and returned alongside the blockers whenever the paper row
exists (AC-GATE-01/02/04). A paper with only quality issues is ``ready``
(AC-GATE-03). Duplicate blocking applies only to pending exact/probable
duplicate relations (AC-DUP-01); confirmed/rejected/ignored relations and
pending non-critical relation types never produce ``pending_duplicate_review``
(AC-DUP-02).

Zero real network: enrichment fixtures rely on the default network-disabled
settings or explicit monkeypatching; no provider request is ever attempted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz
import pytest

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal as _RealSessionLocal
from transit_scholar.db.models import (
    CitationRecord,
    DOIEnrichmentJob,
    DOIProviderResult,
    IngestionJob,
    MetadataCandidate,
    Paper,
    PaperAuthor,
    PaperFile,
    PaperRelation,
)
from transit_scholar.doi_enrichment.service import enrich_paper_by_doi
from transit_scholar.identity.result import DuplicateDetectionResult
from transit_scholar.workflow import service as workflow_service
from transit_scholar.workflow.result import (
    PIPELINE_COMPLETED,
    PIPELINE_PARTIAL,
)
from transit_scholar.workflow.service import (
    get_second_layer_input,
    run_import_pipeline,
)

# Frozen ingestion current_stage vocabulary from acceptance.md
# (ingestion_stage_values); no service-emitted stage may fall outside it and
# the transient ``doi_enrichment`` stage must never be emitted.
FROZEN_STAGE_VOCABULARY = {
    "temp_copy",
    "sha256",
    "exact_duplicate_check",
    "database_write",
    "final_move",
    "metadata_extracting",
    "metadata_failed",
    "doi_required",
    "duplicate_checking",
    "awaiting_user_review",
    "completed",
}

# Frozen blocker vocabulary from acceptance.md (AC-GATE-01), in the canonical
# order the gate must emit them. Metadata gaps are permanently excluded.
FROZEN_BLOCKER_VOCABULARY = [
    "paper_not_found",
    "paper_not_active:<status>",
    "no_primary_file",
    "primary_file_deleted",
    "source_file_missing",
    "metadata_extraction_failed",
    "metadata_processing_pending",
    "pending_duplicate_review",
]

# Frozen metadata quality flag order from acceptance.md (AC-GATE-02).
# These facts are never hard blockers.
FROZEN_QUALITY_FLAG_ORDER = [
    "metadata_missing:title",
    "metadata_missing:author",
    "metadata_missing:year",
    "stable_identifier_missing:doi",
    "metadata_missing:abstract",
    "metadata_missing:venue",
    "metadata_missing:arxiv_id",
]

# Superseded synonyms that must never be returned (AC-GATE-01).
FORBIDDEN_SYNONYMS = (
    "metadata_not_ready",
    "metadata_missing:authors",
    "metadata_missing:stable_identifier",
)

# Exact complete ordered quality-flag list for a paper missing every metadata
# field together while metadata processing completed (AC-GATE-02/03).
ALL_MISSING_EXPECTED = list(FROZEN_QUALITY_FLAG_ORDER)


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear all business tables before each test for isolation."""
    with _RealSessionLocal() as session:
        session.query(CitationRecord).delete()
        session.query(PaperRelation).delete()
        session.query(MetadataCandidate).delete()
        session.query(DOIProviderResult).delete()
        session.query(DOIEnrichmentJob).delete()
        session.query(PaperAuthor).delete()
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


def _make_pdf(
    project_tmp_path: Path,
    *,
    title: str | None = "Gate Paper Title",
    author: str | None = "Jane Doe, John Smith",
    doi: str | None = "10.0000/gate",
    text: str | None = None,
) -> Path:
    """Generate a minimal PDF with metadata and optional first-page text."""
    path = project_tmp_path / f"gate_{uuid.uuid4().hex}.pdf"
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
        if doi:
            meta["keywords"] = f"doi:{doi}"
        doc.set_metadata(meta)
    doc.save(str(path))
    doc.close()
    return path


def _ready_paper(project_tmp_path: Path) -> tuple[str, str]:
    """Create a paper that fully satisfies the ready gate (AC-GATE-03/04).

    Returns ``(paper_id, file_id)``. Individual tests remove one condition to
    produce the exact blocker or quality flag they cover. The fixture carries
    venue + arXiv too, so a fully complete paper has zero quality flags.
    """
    _config.settings.data_root = project_tmp_path
    with _RealSessionLocal() as session:
        paper = Paper(
            title="Ready Gate Paper",
            normalized_title="ready gate paper",
            abstract="This abstract satisfies the second-layer ready gate.",
            publication_year=2024,
            doi="10.9999/gate",
            normalized_doi="10.9999/gate",
            venue="Journal of Transit Studies",
            arxiv_id="2401.00001",
            status="active",
        )
        session.add(paper)
        session.flush()
        session.add(PaperAuthor(
            paper_id=paper.id,
            author_order=1,
            full_name="Gate Author",
            normalized_name="gate author",
        ))
        pf = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            relative_path="library/originals/placeholder/source.pdf",
        )
        session.add(pf)
        session.flush()
        pf.relative_path = f"library/originals/{pf.id}/source.pdf"
        session.commit()
        paper_id = paper.id
        file_id = pf.id
    disk = project_tmp_path / pf.relative_path
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(b"%PDF-1.4 fake content for test\n")
    with _RealSessionLocal() as session:
        session.add(IngestionJob(
            uploaded_filename="gate.pdf",
            file_id=file_id,
            paper_id=paper_id,
            status="accepted",
            current_stage="completed",
        ))
        session.commit()
    return paper_id, file_id


def _add_relation(
    session,
    paper_a_id: str,
    paper_b_id: str,
    status: str,
    relation_type: str = "probable_duplicate",
) -> None:
    """Insert a relation between two papers with canonical pair ordering."""
    src, tgt = sorted([paper_a_id, paper_b_id])
    session.add(PaperRelation(
        source_paper_id=src,
        target_paper_id=tgt,
        relation_type=relation_type,
        confidence=0.9,
        status=status,
        reasons_json="[]",
    ))


# ---------------------------------------------------------------------------
# AC-GATE-01: every producible frozen blocker has an isolated fixture and no
# forbidden synonym is returned
# ---------------------------------------------------------------------------


def test_gate_paper_not_found():
    result = get_second_layer_input("doesnotexist1234567890abcdefghijk")
    assert result.status == "blocked"
    assert result.blockers == ["paper_not_found"]
    assert result.metadata_quality_flags == []
    assert result.source_pdf_path is None


@pytest.mark.parametrize("status", ["archived", "deleted", "duplicate_pending"])
def test_gate_paper_not_active(project_tmp_path, status):
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.status = status
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == [f"paper_not_active:{status}"]
    assert result.metadata_quality_flags == []


def test_gate_no_primary_file(project_tmp_path):
    paper_id, file_id = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        pf = session.get(PaperFile, file_id)
        pf.is_primary = False
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == ["no_primary_file"]
    assert result.metadata_quality_flags == []


def test_gate_primary_file_deleted(project_tmp_path):
    paper_id, file_id = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        pf = session.get(PaperFile, file_id)
        pf.deleted_at = datetime.now(timezone.utc)
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == ["primary_file_deleted"]
    assert result.metadata_quality_flags == []


def test_gate_source_file_missing(project_tmp_path):
    paper_id, file_id = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        pf = session.get(PaperFile, file_id)
        disk = project_tmp_path / pf.relative_path
    disk.unlink()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == ["source_file_missing"]
    assert result.metadata_quality_flags == []


def test_gate_metadata_processing_pending(project_tmp_path):
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        job = session.query(IngestionJob).filter_by(paper_id=paper_id).one()
        job.current_stage = "metadata_extracting"
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == ["metadata_processing_pending"]
    assert result.metadata_quality_flags == []


def test_gate_metadata_extraction_failed(project_tmp_path):
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        job = session.query(IngestionJob).filter_by(paper_id=paper_id).one()
        job.current_stage = "metadata_failed"
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == ["metadata_extraction_failed"]
    assert result.metadata_quality_flags == []


def test_gate_emitted_blockers_stay_in_frozen_vocabulary(project_tmp_path):
    """AC-GATE-01: every blocker the gate can emit comes from the frozen
    vocabulary, including status-parameterized values."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.status = "duplicate_pending"
        other = Paper(status="active")
        session.add(other)
        session.flush()
        _add_relation(session, paper_id, other.id, status="pending")
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    frozen_bases = {v.split(":", 1)[0] for v in FROZEN_BLOCKER_VOCABULARY}
    for blocker in result.blockers:
        assert blocker.split(":", 1)[0] in frozen_bases, (
            f"blocker {blocker!r} outside frozen vocabulary"
        )
    assert "paper_not_active:duplicate_pending" in result.blockers
    assert "pending_duplicate_review" in result.blockers
    assert result.metadata_quality_flags == []


# ---------------------------------------------------------------------------
# AC-GATE-02/03: metadata gaps are quality flags, not blockers; a paper with
# only quality issues is ready
# ---------------------------------------------------------------------------


def test_gate_all_metadata_missing_returns_ready_with_full_flag_order(
    project_tmp_path,
):
    """AC-GATE-02/03: all metadata fields missing together produces the exact
    complete ordered quality flag list, the gate stays ready, and no forbidden
    synonym or old metadata blocker is emitted."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.title = None
        paper.normalized_title = None
        paper.abstract = None
        paper.publication_year = None
        paper.doi = None
        paper.normalized_doi = None
        paper.venue = None
        paper.arxiv_id = None
        for author in list(paper.authors):
            session.delete(author)
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert result.metadata_quality_flags == ALL_MISSING_EXPECTED
    for flag in result.metadata_quality_flags:
        assert flag not in FORBIDDEN_SYNONYMS
        assert "metadata_not_ready" not in flag
        assert not flag.startswith("metadata_missing:stable_identifier")


@pytest.mark.parametrize(
    "mutate,expected_flag",
    [
        (
            lambda session, p: setattr(p, "title", None)
            or setattr(p, "normalized_title", None),
            "metadata_missing:title",
        ),
        (
            lambda session, p: [session.delete(a) for a in list(p.authors)],
            "metadata_missing:author",
        ),
        (
            lambda session, p: setattr(p, "publication_year", None),
            "metadata_missing:year",
        ),
        (
            lambda session, p: setattr(p, "doi", None)
            or setattr(p, "normalized_doi", None),
            "stable_identifier_missing:doi",
        ),
        (
            lambda session, p: setattr(p, "abstract", None),
            "metadata_missing:abstract",
        ),
        (lambda session, p: setattr(p, "venue", None), "metadata_missing:venue"),
        (lambda session, p: setattr(p, "arxiv_id", None), "metadata_missing:arxiv_id"),
    ],
)
def test_gate_single_missing_field_ready_with_exact_flag(
    project_tmp_path, mutate, expected_flag
):
    """AC-GATE-03: each single missing metadata field yields ready with exactly
    one quality flag, in the frozen order."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        paper = session.get(Paper, paper_id)
        mutate(session, paper)
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert result.metadata_quality_flags == [expected_flag]
    assert result.metadata_quality_flags[0] in FROZEN_QUALITY_FLAG_ORDER


def test_gate_arxiv_without_doi_is_quality_flag_not_blocker(project_tmp_path):
    """AC-GATE-02: arXiv never substitutes for the missing DOI; the missing
    DOI is a quality flag, never a hard blocker."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.doi = None
        paper.normalized_doi = None
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert result.metadata_quality_flags == ["stable_identifier_missing:doi"]


def test_gate_abstract_missing_is_quality_flag_not_blocker(project_tmp_path):
    """AC-GATE-03: only the abstract is missing -> ready + one flag."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.abstract = None
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert result.metadata_quality_flags == ["metadata_missing:abstract"]


def test_gate_venue_and_arxiv_flags_are_new_checks(project_tmp_path):
    """AC-GATE-02: venue and arXiv are new quality checks; they are missing
    together here and appear last, after the older checks."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.venue = None
        paper.arxiv_id = None
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert result.metadata_quality_flags == [
        "metadata_missing:venue",
        "metadata_missing:arxiv_id",
    ]


# ---------------------------------------------------------------------------
# AC-GATE-04: hard blockers keep blocking, flags are returned alongside
# ---------------------------------------------------------------------------


def test_gate_blocked_result_carries_quality_flags_when_paper_exists(
    project_tmp_path,
):
    """AC-GATE-04: whenever the paper row exists, flags are computed and
    returned alongside the hard blockers."""
    paper_id, file_id = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.title = None
        paper.normalized_title = None
        paper.venue = None
        pf = session.get(PaperFile, file_id)
        pf.is_primary = False
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == ["no_primary_file"]
    assert result.metadata_quality_flags == [
        "metadata_missing:title",
        "metadata_missing:venue",
    ]


def test_gate_ready_requires_all_frozen_conditions(project_tmp_path):
    paper_id, file_id = _ready_paper(project_tmp_path)
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert result.metadata_quality_flags == []
    assert result.paper_id == paper_id
    assert result.primary_file_id == file_id
    assert result.source_pdf_path is not None
    assert Path(result.source_pdf_path).is_file()
    assert result.relative_path is not None
    assert result.title == "Ready Gate Paper"
    assert result.authors == ["Gate Author"]
    assert result.year == 2024
    assert result.doi == "10.9999/gate"
    assert result.identity_status == "active"
    assert result.duplicate_status == "active"


# ---------------------------------------------------------------------------
# AC-DUP-01/02: critical pending duplicates block; everything else does not
# ---------------------------------------------------------------------------


def test_gate_pending_probable_duplicate_blocks(project_tmp_path):
    """AC-DUP-01: pending probable_duplicate relation keeps the gate blocked;
    once resolved the paper is ready again."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        other = Paper(status="active")
        session.add(other)
        session.flush()
        _add_relation(session, paper_id, other.id, status="pending")
        session.commit()

    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == ["pending_duplicate_review"]

    with _RealSessionLocal() as session:
        rel = session.query(PaperRelation).filter_by(status="pending").one()
        rel.status = "confirmed"
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert result.metadata_quality_flags == []


def test_gate_pending_exact_duplicate_blocks(project_tmp_path):
    """AC-DUP-01: pending exact_duplicate relation also blocks."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        other = Paper(status="active")
        session.add(other)
        session.flush()
        _add_relation(
            session, paper_id, other.id, status="pending",
            relation_type="exact_duplicate",
        )
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.blockers == ["pending_duplicate_review"]


@pytest.mark.parametrize("status", ["confirmed", "rejected", "ignored"])
def test_gate_resolved_critical_relation_does_not_block(project_tmp_path, status):
    """AC-DUP-02: confirmed/rejected/ignored relations never block."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        other = Paper(status="active")
        session.add(other)
        session.flush()
        _add_relation(session, paper_id, other.id, status=status)
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert "pending_duplicate_review" not in result.blockers
    assert result.blockers == []


@pytest.mark.parametrize(
    "relation_type", ["possible_version", "supplement_of", "related"]
)
def test_gate_pending_non_critical_relation_does_not_block(
    project_tmp_path, relation_type
):
    """AC-DUP-02: pending non-critical relation types never produce
    ``pending_duplicate_review``; an active paper stays ready."""
    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        other = Paper(status="active")
        session.add(other)
        session.flush()
        _add_relation(session, paper_id, other.id, status="pending",
                      relation_type=relation_type)
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert "pending_duplicate_review" not in result.blockers


def test_gate_pending_relation_still_visible_via_get_paper(project_tmp_path):
    """AC-DUP-02: non-blocking relations stay visible through get_paper()."""
    from transit_scholar.workflow.service import get_paper

    paper_id, _ = _ready_paper(project_tmp_path)
    with _RealSessionLocal() as session:
        other = Paper(status="active")
        session.add(other)
        session.flush()
        _add_relation(
            session, paper_id, other.id, status="pending",
            relation_type="possible_version",
        )
        session.commit()
    detail = get_paper(paper_id)
    assert detail is not None
    assert len(detail.duplicate_relations) == 1
    assert detail.duplicate_relations[0]["relation_type"] == "possible_version"


# ---------------------------------------------------------------------------
# AC-GATE-003: missing DOI and network-disabled enrichment stay separate
# ---------------------------------------------------------------------------


def test_gate_missing_doi_and_network_disabled_stay_separate(
    project_tmp_path, monkeypatch
):
    """The gate reports ``stable_identifier_missing:doi`` as a quality flag
    without mutating, clearing, or replacing persisted provider records, and
    never returns a network fact as a gate blocker."""
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)
    monkeypatch.setattr(_config.settings, "openalex_api_key", None)

    # Paper A has a DOI; enrichment is network-disabled and persists the fact.
    with _RealSessionLocal() as session:
        paper_a = Paper(
            doi="10.1000/network",
            normalized_doi="10.1000/network",
            status="active",
        )
        session.add(paper_a)
        session.commit()
        paper_a_id = paper_a.id
    enrich_paper_by_doi(paper_a_id)
    with _RealSessionLocal() as session:
        records = session.query(DOIProviderResult).all()
        assert records, "no provider records were persisted"
        for record in records:
            assert record.error_code in ("network_disabled", "missing_api_key")
        assert any(
            r.error_code == "network_disabled" for r in records
        ), "network_disabled fact missing"

    # Paper B has no DOI: the missing-DOI fact is a quality flag, never a
    # blocker, and never a network fact.
    with _RealSessionLocal() as session:
        paper_b = Paper(status="active")
        session.add(paper_b)
        session.commit()
        paper_b_id = paper_b.id
    result = get_second_layer_input(paper_b_id)
    assert result.status == "blocked"  # no primary file is a hard blocker
    assert result.blockers == ["no_primary_file"]
    assert "stable_identifier_missing:doi" in result.metadata_quality_flags
    assert not any("network_disabled" in b for b in result.blockers)
    assert not any("network_disabled" in f for f in result.metadata_quality_flags)

    # The gate never created an enrichment job and never touched the records.
    with _RealSessionLocal() as session:
        assert (
            session.query(DOIEnrichmentJob).filter_by(paper_id=paper_b_id).count() == 0
        )
        crossref = session.query(DOIProviderResult).filter_by(provider="crossref").one()
        assert crossref.error_code == "network_disabled"


# ---------------------------------------------------------------------------
# Pipeline: DOI-required branch, duplicate-detection failure, frozen stages
# ---------------------------------------------------------------------------


def test_pipeline_doi_required_with_other_missing_fields(
    project_tmp_path, monkeypatch
):
    """AC-GATE-05: the DOI-required pipeline branch reports an empty hard
    blocker list and the missing DOI as the sole quality flag."""
    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)
    pdf_path = _make_pdf(
        project_tmp_path, title="No DOI Gate Paper", author="Casey", doi=None
    )
    result = run_import_pipeline(pdf_path)
    assert result.status == PIPELINE_PARTIAL
    assert result.current_stage == "doi_required"
    assert result.metadata_enrichment_status == "skipped"
    assert result.enrichment_provider_results == []
    assert result.second_layer_ready is False
    assert result.second_layer_blockers == []
    assert result.metadata_quality_flags == ["stable_identifier_missing:doi"]


def test_pipeline_duplicate_detection_failed_keeps_frozen_blocker(
    project_tmp_path, monkeypatch
):
    """The duplicate-detection failure pipeline result keeps the frozen
    ``duplicate_detection_failed`` blocker; the paper's quality flags stay
    visible alongside it."""
    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)

    def _fail_detect(paper_id, *, create_relations=True):
        return DuplicateDetectionResult(
            paper_id=paper_id,
            status="failed",
            candidates_seen=0,
            relations_created=0,
            relations_existing=0,
            relation_ids=[],
            error_code="DATABASE_WRITE_FAILED",
            error_message="simulated detection failure",
        )

    monkeypatch.setattr(workflow_service, "detect_duplicate_candidates", _fail_detect)

    pdf_path = _make_pdf(project_tmp_path, title="Dup Fail Paper", author="Dana")
    result = run_import_pipeline(pdf_path)
    assert result.status == PIPELINE_PARTIAL
    assert result.current_stage == "duplicate_checking"
    assert result.duplicate_status == "failed"
    assert result.second_layer_blockers == ["duplicate_detection_failed"]
    assert result.metadata_quality_flags == [
        "metadata_missing:year",
        "metadata_missing:abstract",
        "metadata_missing:venue",
        "metadata_missing:arxiv_id",
    ]


def test_pipeline_never_emits_doi_enrichment_stage(project_tmp_path, monkeypatch):
    """Every service-emitted current_stage stays inside the frozen stage
    vocabulary and the transient ``doi_enrichment`` stage is never written."""
    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)

    observed: list[tuple[str | None, str | None]] = []

    def recording_factory():
        session = _RealSessionLocal()
        original_commit = session.commit

        def recording_commit():
            original_commit()
            with _RealSessionLocal() as snap:
                for job in snap.query(IngestionJob).all():
                    observed.append((job.status, job.current_stage))

        session.commit = recording_commit
        return session

    monkeypatch.setattr(workflow_service, "SessionLocal", recording_factory)

    pdf_path = _make_pdf(
        project_tmp_path, title="Stage Normalization", author="Evan"
    )
    result = run_import_pipeline(pdf_path)
    assert result.status == PIPELINE_COMPLETED
    assert result.current_stage == "completed"
    assert observed, "no service-emitted records were captured"
    for status, stage in observed:
        assert stage is None or stage in FROZEN_STAGE_VOCABULARY, (
            f"current_stage {stage!r} outside frozen vocabulary"
        )
        assert stage != "doi_enrichment", "transient doi_enrichment stage emitted"
    emitted_stages = {stage for _, stage in observed}
    assert {"metadata_extracting", "duplicate_checking", "completed"} <= emitted_stages
