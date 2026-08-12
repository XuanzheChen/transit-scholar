"""Stage 4 automated tests for paper-level duplicate detection + manual processing.

Uses the isolated test database from conftest.py (Alembic head on a temp dir).
Papers/PaperAuthors are constructed directly via ORM — no real PDFs needed
except for the file-move tests (T20-T22), which build tiny real files on disk.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from transit_scholar.db.engine import SessionLocal, engine as _engine
from transit_scholar.db.models import (
    AuditLog,
    Paper,
    PaperAuthor,
    PaperFile,
    PaperRelation,
)
from transit_scholar.identity import (
    archive_paper,
    detect_duplicate_candidates,
    list_duplicate_candidates,
    resolve_duplicate,
    restore_paper,
    set_primary_file,
    soft_delete_paper,
    update_paper_metadata,
)
from transit_scholar.identity.result import (
    DuplicateCandidateView,
    DuplicateDetectionResult,
    DuplicateResolutionResult,
    PaperActionResult,
)
from transit_scholar.identity.scoring import (
    calculate_author_overlap,
    calculate_title_similarity,
    score_paper_pair,
)
from transit_scholar.identity.service import (
    DATABASE_WRITE_FAILED,
    FILE_MOVE_FAILED,
    FILE_NOT_FOUND,
    INVALID_DECISION,
    INVALID_FIELDS,
    INVALID_STATE,
    PAPER_NOT_FOUND,
    RELATION_NOT_FOUND,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_tables():
    """Clear all Stage 4 + related tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(AuditLog).delete()
        session.query(PaperRelation).delete()
        session.query(PaperAuthor).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


def _make_paper(
    *,
    title: str | None = None,
    normalized_title: str | None = None,
    doi: str | None = None,
    normalized_doi: str | None = None,
    arxiv_id: str | None = None,
    status: str = "active",
) -> Paper:
    paper = Paper(
        title=title,
        normalized_title=normalized_title,
        doi=doi,
        normalized_doi=normalized_doi,
        arxiv_id=arxiv_id,
        status=status,
    )
    with SessionLocal() as session:
        session.add(paper)
        session.commit()
        session.refresh(paper)
        return paper


def _add_author(paper: Paper, name: str, order: int = 1) -> None:
    with SessionLocal() as session:
        p = session.get(Paper, paper.id)
        session.add(PaperAuthor(
            paper_id=p.id,
            author_order=order,
            full_name=name,
            normalized_name=name.lower(),
        ))
        session.commit()


# ---------------------------------------------------------------------------
# T01 / T02: ORM + Alembic
# ---------------------------------------------------------------------------


def test_paper_relation_and_audit_log_in_orm():
    """T01: ORM metadata contains PaperRelation and AuditLog."""
    from transit_scholar.db import models
    assert hasattr(models, "PaperRelation")
    assert hasattr(models, "AuditLog")
    rel_cols = {c.name for c in models.PaperRelation.__table__.columns}
    for required in (
        "id", "source_paper_id", "target_paper_id", "relation_type",
        "confidence", "status", "reasons_json", "created_at",
        "resolved_at", "resolved_by",
    ):
        assert required in rel_cols, f"missing PaperRelation column: {required}"
    log_cols = {c.name for c in models.AuditLog.__table__.columns}
    for required in (
        "id", "entity_type", "entity_id", "action", "actor_type",
        "old_value_json", "new_value_json", "created_at",
    ):
        assert required in log_cols, f"missing AuditLog column: {required}"


def test_alembic_creates_eight_tables():
    """T02: Alembic migration created paper_relations and audit_logs."""
    tables = set(inspect(_engine).get_table_names())
    for required in (
        "papers", "paper_files", "paper_authors", "ingestion_jobs",
        "metadata_candidates", "paper_relations", "audit_logs", "alembic_version",
    ):
        assert required in tables, f"missing table: {required}"


# ---------------------------------------------------------------------------
# T25 / T26: scoring unit tests
# ---------------------------------------------------------------------------


def test_author_overlap_empty():
    assert calculate_author_overlap([], ["a"]) == 0.0
    assert calculate_author_overlap(["a"], []) == 0.0
    assert calculate_author_overlap([], []) == 0.0


def test_author_overlap_partial():
    assert calculate_author_overlap(["alice", "bob"], ["bob", "carol"]) == 1 / 2


def test_author_overlap_identical():
    assert calculate_author_overlap(["alice", "bob"], ["alice", "bob"]) == 1.0


def test_title_similarity_empty():
    assert calculate_title_similarity(None, "x") == 0.0
    assert calculate_title_similarity("x", None) == 0.0
    assert calculate_title_similarity("", "x") == 0.0


def test_title_similarity_identical():
    assert calculate_title_similarity("hello world", "hello world") == 1.0


def test_title_similarity_different():
    s = calculate_title_similarity("completely different title", "nothing alike here")
    assert 0.0 <= s < 0.8


# ---------------------------------------------------------------------------
# T03 / T04: exact DOI / arXiv match
# ---------------------------------------------------------------------------


def test_doi_exact_match_creates_exact_duplicate():
    """T03: same normalized_doi -> exact_duplicate, confidence 1.0."""
    a = _make_paper(normalized_doi="10.1234/abc")
    b = _make_paper(normalized_doi="10.1234/abc")
    result = detect_duplicate_candidates(a.id)
    assert result.status == "completed"
    assert result.relations_created == 1
    views = list_duplicate_candidates(a.id)
    assert len(views) == 1
    assert views[0].relation_type == "exact_duplicate"
    assert views[0].confidence == 1.0
    assert views[0].status == "pending"


def test_arxiv_exact_match_creates_exact_duplicate():
    """T04: same arxiv_id -> exact_duplicate, confidence 1.0."""
    a = _make_paper(arxiv_id="2301.01234")
    b = _make_paper(arxiv_id="2301.01234")
    result = detect_duplicate_candidates(a.id)
    assert result.status == "completed"
    assert result.relations_created == 1
    views = list_duplicate_candidates(a.id)
    assert views[0].relation_type == "exact_duplicate"
    assert views[0].confidence == 1.0


# ---------------------------------------------------------------------------
# T05 / T06: title + author fuzzy
# ---------------------------------------------------------------------------


def test_same_title_with_authors_creates_probable_duplicate():
    """T05: same normalized_title + author_overlap>=0.5 -> probable_duplicate."""
    a = _make_paper(title="Bus Control Methods", normalized_title="bus control methods")
    b = _make_paper(title="Bus Control Methods", normalized_title="bus control methods")
    _add_author(a, "Alice Lee", order=1)
    _add_author(a, "Bob Chen", order=2)
    _add_author(b, "Alice Lee", order=1)
    _add_author(b, "Bob Chen", order=2)
    result = detect_duplicate_candidates(a.id)
    assert result.relations_created == 1
    views = list_duplicate_candidates(a.id)
    assert views[0].relation_type == "probable_duplicate"
    assert views[0].confidence >= 0.95


def test_similar_title_creates_weaker_relation():
    """T06: similar title + shared author -> possible_version or related."""
    a = _make_paper(
        title="A Novel Approach to Bus Control",
        normalized_title="a novel approach to bus control",
    )
    b = _make_paper(
        title="Novel Approaches for Bus Control Systems",
        normalized_title="novel approaches for bus control systems",
    )
    _add_author(a, "Alice Lee", order=1)
    _add_author(b, "Alice Lee", order=1)
    result = detect_duplicate_candidates(a.id)
    assert result.relations_created == 1
    views = list_duplicate_candidates(a.id)
    assert views[0].relation_type in ("possible_version", "related")


# ---------------------------------------------------------------------------
# T07: title similar but authors totally different -> no confirmed, no merge
# ---------------------------------------------------------------------------


def test_similar_title_different_authors_not_confirmed():
    """T07: similar title but disjoint authors must never auto-confirm or merge."""
    a = _make_paper(
        title="Deep Learning for Transit Signal Priority",
        normalized_title="deep learning for transit signal priority",
    )
    b = _make_paper(
        title="Deep Learning Approaches to Transit Signal Priority",
        normalized_title="deep learning approaches to transit signal priority",
    )
    _add_author(a, "Alice Lee", order=1)
    _add_author(b, "Zara Kim", order=1)
    result = detect_duplicate_candidates(a.id)
    # Whatever is created, it must NOT be confirmed and must NOT merge papers.
    with SessionLocal() as session:
        rels = session.query(PaperRelation).all()
        for r in rels:
            assert r.status != "confirmed"
        # Both papers still exist and are distinct.
        assert session.query(Paper).count() == 2
        pa = session.get(Paper, a.id)
        pb = session.get(Paper, b.id)
        assert pa is not None and pb is not None
        assert pa.id != pb.id


# ---------------------------------------------------------------------------
# T08 / T09 / T10: idempotency + constraints
# ---------------------------------------------------------------------------


def test_repeat_detection_no_duplicate_relations():
    """T08: running detection twice does not create duplicate relations."""
    a = _make_paper(normalized_doi="10.5555/xyz")
    b = _make_paper(normalized_doi="10.5555/xyz")
    first = detect_duplicate_candidates(a.id)
    second = detect_duplicate_candidates(a.id)
    assert first.relations_created == 1
    assert second.relations_created == 0
    assert second.relations_existing == 1
    with SessionLocal() as session:
        assert session.query(PaperRelation).count() == 1


def test_no_self_relation():
    """T09: source_paper_id != target_paper_id for every relation."""
    a = _make_paper(normalized_doi="10.7777/self")
    b = _make_paper(normalized_doi="10.7777/self")
    detect_duplicate_candidates(a.id)
    with SessionLocal() as session:
        for r in session.query(PaperRelation).all():
            assert r.source_paper_id != r.target_paper_id


def test_no_mirror_relations():
    """T10: no A->B and B->A mirror pair exists."""
    a = _make_paper(normalized_doi="10.8888/mirror")
    b = _make_paper(normalized_doi="10.8888/mirror")
    detect_duplicate_candidates(a.id)
    detect_duplicate_candidates(b.id)
    with SessionLocal() as session:
        pairs = sorted(
            (r.source_paper_id, r.target_paper_id, r.relation_type)
            for r in session.query(PaperRelation).all()
        )
        # Each unordered pair+type appears at most once.
        assert len(pairs) == len(set(pairs))
        assert len(pairs) == 1


# ---------------------------------------------------------------------------
# T11: list + status filter
# ---------------------------------------------------------------------------


def test_list_candidates_status_filter():
    """T11: list_duplicate_candidates filters by status."""
    a = _make_paper(normalized_doi="10.1111/one")
    b = _make_paper(normalized_doi="10.1111/one")
    detect_duplicate_candidates(a.id)
    assert len(list_duplicate_candidates(a.id, status="pending")) == 1
    assert len(list_duplicate_candidates(a.id, status="confirmed")) == 0
    # Resolve to confirmed, then filter.
    views = list_duplicate_candidates(a.id, status="pending")
    resolve_duplicate(views[0].relation_id, "same_paper")
    assert len(list_duplicate_candidates(a.id, status="confirmed")) == 1
    assert len(list_duplicate_candidates(a.id, status="pending")) == 0
    assert len(list_duplicate_candidates(a.id, status="rejected")) == 0
    assert len(list_duplicate_candidates(a.id, status="ignored")) == 0


# ---------------------------------------------------------------------------
# T12-T15: resolve_duplicate decisions
# ---------------------------------------------------------------------------


def _create_pending_relation() -> tuple[str, str]:
    a = _make_paper(normalized_doi="10.2222/dec")
    b = _make_paper(normalized_doi="10.2222/dec")
    detect_duplicate_candidates(a.id)
    views = list_duplicate_candidates(a.id, status="pending")
    return views[0].relation_id, a.id


def test_resolve_same_paper():
    """T12: same_paper -> confirmed, audit log, no merge."""
    rel_id, a_id = _create_pending_relation()
    result = resolve_duplicate(rel_id, "same_paper")
    assert result.status == "resolved"
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        rel = session.get(PaperRelation, rel_id)
        assert rel.status == "confirmed"
        assert rel.relation_type == "exact_duplicate"
        assert rel.resolved_at is not None
        assert rel.resolved_by == "local_user"
        log = session.get(AuditLog, result.audit_log_id)
        assert log is not None
        assert log.action == "resolve_duplicate"
        assert log.entity_type == "paper_relation"
        # No merge: still two papers.
        assert session.query(Paper).count() == 2
        assert session.get(Paper, a_id) is not None


def test_resolve_different_version():
    """T13: different_version -> confirmed + relation_type=possible_version."""
    rel_id, _ = _create_pending_relation()
    result = resolve_duplicate(rel_id, "different_version")
    assert result.status == "resolved"
    with SessionLocal() as session:
        rel = session.get(PaperRelation, rel_id)
        assert rel.status == "confirmed"
        assert rel.relation_type == "possible_version"


def test_resolve_not_duplicate():
    """T14: not_duplicate -> rejected + audit log."""
    rel_id, _ = _create_pending_relation()
    result = resolve_duplicate(rel_id, "not_duplicate")
    assert result.status == "resolved"
    with SessionLocal() as session:
        rel = session.get(PaperRelation, rel_id)
        assert rel.status == "rejected"
        assert session.get(AuditLog, result.audit_log_id).action == "resolve_duplicate"


def test_resolve_ignore():
    """T15: ignore -> ignored + audit log."""
    rel_id, _ = _create_pending_relation()
    result = resolve_duplicate(rel_id, "ignore")
    assert result.status == "resolved"
    with SessionLocal() as session:
        rel = session.get(PaperRelation, rel_id)
        assert rel.status == "ignored"


def test_resolve_invalid_decision():
    result = resolve_duplicate("nonexistent", "bogus_choice")
    assert result.status == "failed"
    assert result.error_code == INVALID_DECISION


def test_resolve_missing_relation():
    result = resolve_duplicate("missingrelationid1234567890ab", "same_paper")
    assert result.status == "failed"
    assert result.error_code == RELATION_NOT_FOUND


# ---------------------------------------------------------------------------
# T16: status convergence
# ---------------------------------------------------------------------------


def test_duplicate_pending_converges_to_active():
    """T16: once all pending relations are resolved, duplicate_pending -> active."""
    a = _make_paper(normalized_doi="10.3333/pend")
    b = _make_paper(normalized_doi="10.3333/pend")
    detect_duplicate_candidates(a.id)
    with SessionLocal() as session:
        assert session.get(Paper, a.id).status == "duplicate_pending"
        assert session.get(Paper, b.id).status == "duplicate_pending"
    views = list_duplicate_candidates(a.id, status="pending")
    resolve_duplicate(views[0].relation_id, "not_duplicate")
    with SessionLocal() as session:
        assert session.get(Paper, a.id).status == "active"
        assert session.get(Paper, b.id).status == "active"


# ---------------------------------------------------------------------------
# T17: update_paper_metadata
# ---------------------------------------------------------------------------


def test_update_metadata_syncs_normalised_and_audits():
    """T17: updating title/doi/arxiv_id syncs normalised columns + audit log."""
    p = _make_paper()
    result = update_paper_metadata(
        p.id,
        {"title": "New Title", "doi": "10.1234/NEW", "arxiv_id": "2301.00001"},
    )
    assert result.status == "updated"
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        paper = session.get(Paper, p.id)
        assert paper.title == "New Title"
        assert paper.normalized_title == "new title"
        assert paper.doi == "10.1234/NEW"
        assert paper.normalized_doi == "10.1234/new"
        assert paper.arxiv_id == "2301.00001"
        log = session.get(AuditLog, result.audit_log_id)
        assert log.action == "update_metadata"
        assert log.old_value_json is not None
        assert log.new_value_json is not None


def test_update_metadata_rejects_bad_field():
    p = _make_paper()
    result = update_paper_metadata(p.id, {"status": "hacked"})
    assert result.status == "failed"
    assert result.error_code == INVALID_FIELDS


def test_update_metadata_rejects_empty():
    p = _make_paper()
    result = update_paper_metadata(p.id, {})
    assert result.status == "failed"
    assert result.error_code == INVALID_FIELDS


def test_update_metadata_missing_paper():
    result = update_paper_metadata("missingid1234567890abcdefghijk", {"title": "X"})
    assert result.status == "failed"
    assert result.error_code == PAPER_NOT_FOUND


# ---------------------------------------------------------------------------
# T18: set_primary_file
# ---------------------------------------------------------------------------


def test_set_primary_file_exclusive():
    """T18: only one primary file per paper; audit log written."""
    with SessionLocal() as session:
        paper = Paper(status="active")
        session.add(paper)
        session.flush()
        f1 = PaperFile(paper_id=paper.id, is_primary=True)
        f2 = PaperFile(paper_id=paper.id, is_primary=False)
        session.add(f1)
        session.add(f2)
        session.commit()
        paper_id = paper.id
        f2_id = f2.id

    result = set_primary_file(paper_id, f2_id)
    assert result.status == "updated"
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        files = session.query(PaperFile).filter(PaperFile.paper_id == paper_id).all()
        primaries = [f for f in files if f.is_primary]
        assert len(primaries) == 1
        assert primaries[0].id == f2_id


def test_set_primary_file_wrong_paper():
    a = _make_paper()
    b = _make_paper()
    with SessionLocal() as session:
        pf = PaperFile(paper_id=b.id, is_primary=True)
        session.add(pf)
        session.commit()
        fid = pf.id
    result = set_primary_file(a.id, fid)
    assert result.status == "failed"
    assert result.error_code == INVALID_FIELDS


# ---------------------------------------------------------------------------
# T19: archive_paper
# ---------------------------------------------------------------------------


def test_archive_paper():
    """T19: archive sets status, no file move, audit log."""
    p = _make_paper()
    result = archive_paper(p.id)
    assert result.status == "archived"
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        assert session.get(Paper, p.id).status == "archived"
        log = session.get(AuditLog, result.audit_log_id)
        assert log.action == "archive_paper"


# ---------------------------------------------------------------------------
# T20-T22: soft delete + restore + missing file
# ---------------------------------------------------------------------------


def _paper_with_file(project_tmp_path: Path) -> tuple[str, str, Path]:
    """Create a paper with one real file on disk under an isolated data root."""
    from transit_scholar import config as _config
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        paper = Paper(status="active")
        session.add(paper)
        session.flush()
        pf = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            relative_path=f"library/originals/placeholder/source.pdf",
        )
        session.add(pf)
        session.flush()
        pf.relative_path = f"library/originals/{pf.id}/source.pdf"
        session.commit()
        paper_id = paper.id
        file_id = pf.id
    # Write a real file to disk.
    disk = project_tmp_path / pf.relative_path
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(b"%PDF-1.4 fake content for test\n")
    return paper_id, file_id, project_tmp_path


def _paper_with_two_files(project_tmp_path: Path) -> tuple[str, str, str, Path]:
    """Create a paper with two real files on disk under an isolated data root."""
    from transit_scholar import config as _config
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        paper = Paper(status="active")
        session.add(paper)
        session.flush()
        f1 = PaperFile(paper_id=paper.id, is_primary=True)
        f2 = PaperFile(paper_id=paper.id, is_primary=False)
        session.add(f1)
        session.add(f2)
        session.flush()
        f1.relative_path = f"library/originals/{f1.id}/source.pdf"
        f2.relative_path = f"library/originals/{f2.id}/source.pdf"
        session.commit()
        paper_id = paper.id
        f1_id = f1.id
        f2_id = f2.id
    for fid in (f1_id, f2_id):
        disk = project_tmp_path / f"library/originals/{fid}/source.pdf"
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(b"%PDF-1.4 fake content for test\n")
    return paper_id, f1_id, f2_id, project_tmp_path


def test_soft_delete_moves_file_and_audits(project_tmp_path):
    """T20: soft delete sets status/deleted_at, moves file to trash, audit log."""
    paper_id, file_id, root = _paper_with_file(project_tmp_path)
    result = soft_delete_paper(paper_id)
    assert result.status == "deleted", result.error_message
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.status == "deleted"
        assert paper.deleted_at is not None
        pf = session.get(PaperFile, file_id)
        assert pf.deleted_at is not None
        assert pf.relative_path == f"library/trash/{file_id}/source.pdf"
    # File now lives in trash, not originals.
    assert (root / f"library/trash/{file_id}/source.pdf").is_file()
    assert not (root / f"library/originals/{file_id}/source.pdf").exists()


def test_restore_moves_file_back_and_audits(project_tmp_path):
    """T21: restore from trash restores status + file location, audit log."""
    paper_id, file_id, root = _paper_with_file(project_tmp_path)
    soft_delete_paper(paper_id)
    result = restore_paper(paper_id)
    assert result.status == "restored", result.error_message
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.status == "active"
        assert paper.deleted_at is None
        pf = session.get(PaperFile, file_id)
        assert pf.deleted_at is None
        assert pf.relative_path == f"library/originals/{file_id}/source.pdf"
    assert (root / f"library/originals/{file_id}/source.pdf").is_file()
    assert not (root / f"library/trash/{file_id}/source.pdf").exists()


def test_restore_missing_file_returns_error(project_tmp_path):
    """T22: restore with missing trash file returns FILE_NOT_FOUND, no data loss."""
    paper_id, file_id, root = _paper_with_file(project_tmp_path)
    soft_delete_paper(paper_id)
    # Remove the trash file to simulate data loss.
    trash_path = root / f"library/trash/{file_id}/source.pdf"
    assert trash_path.is_file()
    trash_path.unlink()
    result = restore_paper(paper_id)
    assert result.status == "failed"
    assert result.error_code == FILE_NOT_FOUND
    # DB record is NOT permanently deleted.
    with SessionLocal() as session:
        assert session.get(Paper, paper_id) is not None
        assert session.get(PaperFile, file_id) is not None


def test_soft_delete_file_move_failed_returns_file_move_failed(project_tmp_path, monkeypatch):
    """shutil.move failure in soft_delete_paper -> FILE_MOVE_FAILED, no DB commit."""
    from transit_scholar.identity import service as _svc

    paper_id, file_id, root = _paper_with_file(project_tmp_path)

    original_move = _svc.shutil.move

    def failing_move(*args, **kwargs):
        raise OSError("simulated move failure")

    monkeypatch.setattr(_svc.shutil, "move", failing_move)

    result = soft_delete_paper(paper_id)
    assert result.status == "failed"
    assert result.error_code == FILE_MOVE_FAILED, result.error_message
    # DB state must NOT be committed: paper still active, file not marked deleted.
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.status == "active"
        assert paper.deleted_at is None
        pf = session.get(PaperFile, file_id)
        assert pf.deleted_at is None
        assert pf.relative_path == f"library/originals/{file_id}/source.pdf"
    # No audit log written for the failed operation.
    with SessionLocal() as session:
        assert session.query(AuditLog).count() == 0
    # Original file still on disk in originals (move never happened).
    assert (root / f"library/originals/{file_id}/source.pdf").is_file()


def test_restore_file_move_failed_returns_file_move_failed(project_tmp_path, monkeypatch):
    """shutil.move failure in restore_paper -> FILE_MOVE_FAILED, no DB commit."""
    from transit_scholar.identity import service as _svc

    paper_id, file_id, root = _paper_with_file(project_tmp_path)
    soft_delete_paper(paper_id)

    # soft_delete_paper already wrote one audit log; capture that baseline so
    # we can assert the failed restore writes NO additional audit log.
    baseline_audit_count = SessionLocal().query(AuditLog).count()

    def failing_move(*args, **kwargs):
        raise OSError("simulated move failure")

    monkeypatch.setattr(_svc.shutil, "move", failing_move)

    result = restore_paper(paper_id)
    assert result.status == "failed"
    assert result.error_code == FILE_MOVE_FAILED, result.error_message
    # DB state must NOT be committed: paper still deleted, file still in trash.
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.status == "deleted"
        assert paper.deleted_at is not None
        pf = session.get(PaperFile, file_id)
        assert pf.deleted_at is not None
        assert pf.relative_path == f"library/trash/{file_id}/source.pdf"
    # No NEW audit log written for the failed operation (baseline unchanged).
    with SessionLocal() as session:
        assert session.query(AuditLog).count() == baseline_audit_count
    # File still in originals would be wrong; it must remain in trash.
    assert (root / f"library/trash/{file_id}/source.pdf").is_file()


# ---------------------------------------------------------------------------
# T23 / T24: no out-of-scope tables / no network
# ---------------------------------------------------------------------------


def test_no_out_of_scope_models():
    """T23: Stage 4 must not contain second-layer / network / LLM models.

    CitationRecord / CitationRender are now in-scope (Stage 5) and are
    explicitly excluded from this negative guard.
    """
    from transit_scholar.db import models
    for name in ("PaperRelation", "AuditLog", "CitationRecord", "CitationRender"):
        assert hasattr(models, name)
    # No second-layer / RAG / schema-extraction models.
    for name in ("ParsedBlock", "SchemaExtraction", "RagChunk"):
        assert not hasattr(models, name), f"out-of-scope model found: {name}"
    tables = set(inspect(_engine).get_table_names())
    assert "parsed_blocks" not in tables
    assert "schema_extractions" not in tables
    assert "rag_chunks" not in tables


def test_detection_does_not_read_pdf_or_network():
    """T24: detection only reads DB fields; works with no PDFs on disk."""
    a = _make_paper(
        title="On the Theory of Everything",
        normalized_title="on the theory of everything",
        doi="10.0000/nte",
        normalized_doi="10.0000/nte",
    )
    b = _make_paper(
        title="On the Theory of Everything",
        normalized_title="on the theory of everything",
        doi="10.0000/nte",
        normalized_doi="10.0000/nte",
    )
    _add_author(a, "Alice", order=1)
    _add_author(b, "Alice", order=1)
    # No PaperFiles / PDFs exist at all.
    result = detect_duplicate_candidates(a.id)
    assert result.status == "completed"
    assert result.relations_created == 1


# ---------------------------------------------------------------------------
# T27 / T28: detection error paths
# ---------------------------------------------------------------------------


def test_detection_missing_paper():
    """T27: unknown paper_id -> failed/PAPER_NOT_FOUND."""
    result = detect_duplicate_candidates("doesnotexist1234567890abcdefg")
    assert result.status == "failed"
    assert result.error_code == PAPER_NOT_FOUND


def test_detection_deleted_paper():
    """T28: deleted paper -> failed/INVALID_STATE."""
    p = _make_paper(status="deleted")
    result = detect_duplicate_candidates(p.id)
    assert result.status == "failed"
    assert result.error_code == INVALID_STATE


# ---------------------------------------------------------------------------
# Additional integrity: detection does not modify archived papers
# ---------------------------------------------------------------------------


def test_detection_does_not_override_archived_or_deleted():
    """Creating pending relations must not flip archived/deleted papers."""
    a = _make_paper(normalized_doi="10.4444/arch")
    keeper = _make_paper(normalized_doi="10.4444/arch")
    archived = _make_paper(status="archived")
    # keeper shares DOI with a; archived is unrelated but ensure it stays archived.
    detect_duplicate_candidates(a.id)
    with SessionLocal() as session:
        # The active 'keeper' that got a pending relation -> duplicate_pending.
        assert session.get(Paper, keeper.id).status == "duplicate_pending"
        # The explicitly archived paper must remain archived.
        assert session.get(Paper, archived.id).status == "archived"


# ---------------------------------------------------------------------------
# Multi-file move-failure compensation
# ---------------------------------------------------------------------------


def test_soft_delete_multi_file_partial_failure_restores_disk_and_db(project_tmp_path, monkeypatch):
    """Soft delete with 2 files where the 2nd move fails: the 1st file must be
    moved back to originals (compensation), DB must not be committed, and no
    audit log written."""
    from transit_scholar.identity import service as _svc

    paper_id, f1_id, f2_id, root = _paper_with_two_files(project_tmp_path)

    # Save the REAL shutil.move before patching. The mock delegates to it for
    # every call EXCEPT the second forward move, so that:
    #   - call #1 (forward f1): actually succeeds -> f1 is in trash
    #   - call #2 (forward f2): raises OSError -> triggers rollback
    #   - call #3 (compensation f1): actually succeeds -> f1 back to originals
    # If we let the mock raise on ALL calls after the first, the compensation
    # itself would fail and we'd get a false sense that the disk is restored.
    original_move = _svc.shutil.move

    call_count = {"n": 0}

    def fail_only_second_move(src, dst, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated failure on second forward move")
        return original_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(_svc.shutil, "move", fail_only_second_move)

    result = soft_delete_paper(paper_id)
    assert result.status == "failed"
    assert result.error_code == FILE_MOVE_FAILED, result.error_message
    # Sanity: exactly 3 move attempts — forward f1, forward f2 (fails),
    # compensation f1 (succeeds). This proves compensation was exercised.
    assert call_count["n"] == 3, f"expected 3 move attempts, got {call_count['n']}"
    # DB must not be committed: paper still active, files not marked deleted.
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.status == "active"
        assert paper.deleted_at is None
        for fid in (f1_id, f2_id):
            pf = session.get(PaperFile, fid)
            assert pf.deleted_at is None
            assert pf.relative_path == f"library/originals/{fid}/source.pdf"
    # No audit log written.
    with SessionLocal() as session:
        assert session.query(AuditLog).count() == 0
    # Both files must be back on disk in originals (compensation moved f1 back).
    for fid in (f1_id, f2_id):
        assert (root / f"library/originals/{fid}/source.pdf").is_file(), (
            f"file {fid} not restored to originals"
        )
        assert not (root / f"library/trash/{fid}/source.pdf").exists(), (
            f"file {fid} unexpectedly in trash"
        )


def test_restore_multi_file_partial_failure_restores_disk_and_db(project_tmp_path, monkeypatch):
    """Restore with 2 files where the 2nd move fails: the 1st file must be
    moved back to trash (compensation), DB must not be committed, and no new
    audit log written."""
    from transit_scholar.identity import service as _svc

    paper_id, f1_id, f2_id, root = _paper_with_two_files(project_tmp_path)
    soft_delete_paper(paper_id)

    baseline_audit_count = SessionLocal().query(AuditLog).count()

    # Save the REAL shutil.move before patching. The mock delegates to it for
    # every call EXCEPT the second forward move, so that:
    #   - call #1 (forward f1): actually succeeds -> f1 is in originals
    #   - call #2 (forward f2): raises OSError -> triggers rollback
    #   - call #3 (compensation f1): actually succeeds -> f1 back to trash
    original_move = _svc.shutil.move

    call_count = {"n": 0}

    def fail_only_second_move(src, dst, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated failure on second forward move")
        return original_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(_svc.shutil, "move", fail_only_second_move)

    result = restore_paper(paper_id)
    assert result.status == "failed"
    assert result.error_code == FILE_MOVE_FAILED, result.error_message
    # Sanity: exactly 3 move attempts — forward f1, forward f2 (fails),
    # compensation f1 (succeeds). This proves compensation was exercised.
    assert call_count["n"] == 3, f"expected 3 move attempts, got {call_count['n']}"
    # DB must not be committed: paper still deleted, files still in trash.
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.status == "deleted"
        assert paper.deleted_at is not None
        for fid in (f1_id, f2_id):
            pf = session.get(PaperFile, fid)
            assert pf.deleted_at is not None
            assert pf.relative_path == f"library/trash/{fid}/source.pdf"
    # No NEW audit log written.
    with SessionLocal() as session:
        assert session.query(AuditLog).count() == baseline_audit_count
    # Both files must be back on disk in trash (compensation moved f1 back).
    for fid in (f1_id, f2_id):
        assert (root / f"library/trash/{fid}/source.pdf").is_file(), (
            f"file {fid} not restored to trash"
        )
        assert not (root / f"library/originals/{fid}/source.pdf").exists(), (
            f"file {fid} unexpectedly in originals"
        )