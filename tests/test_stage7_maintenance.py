"""Stage 7 maintenance tests.

Covers maintenance item detection and the read-only preview contract.
The isolated Alembic-head database from conftest.py is used; each test
clears the relevant tables for isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz
import pytest
from sqlalchemy import select

from transit_scholar import config as _config
from transit_scholar.config import settings
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    IngestionJob,
    Paper,
    PaperFile,
)
from transit_scholar.maintenance import (
    get_maintenance_item,
    list_maintenance_items,
    preview_maintenance_action,
)
from transit_scholar.maintenance.service import (
    FAILED_INGESTION_JOB,
    MISSING_ORIGINAL_FILE,
    ORPHAN_TEMPORARY_DIR,
    ORPHAN_TRASH_FILE,
    RECONCILE_MISSING_ORIGINAL,
    RESTORE,
    RETRY_IMPORT,
    SOFT_DELETED_FILE,
    SOFT_DELETED_PAPER,
    TEMPORARY_RESIDUE,
    PURGE_TEMPORARY_PATH,
    PURGE_TRASH_PATH,
    PURGE_DELETED_ASSET,
)


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear business tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


def _make_pdf(tmp_path: Path, name: str | None = None) -> Path:
    filename = name or f"t_{uuid.uuid4().hex}.pdf"
    path = tmp_path / filename
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 550, 750), "Hello world", fontsize=11)
    doc.set_metadata({"title": "T", "author": "A"})
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Failed job detection
# ---------------------------------------------------------------------------


def test_failed_job_identified(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="broken.pdf",
            source_path="/nonexistent/source.pdf",
            status="failed",
            error_message="boom",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    items = list_maintenance_items()
    matched = [i for i in items if i.related_job_id == job_id]
    assert len(matched) == 1
    item = matched[0]
    assert item.item_type == FAILED_INGESTION_JOB
    assert item.item_id == f"ingestion:{job_id}"
    assert item.can_purge is True
    # source_path is not accessible -> retry blocked
    assert item.can_retry_import is False
    assert "source_path_unavailable" in item.blockers
    assert RETRY_IMPORT in item.safe_actions
    assert PURGE_TEMPORARY_PATH in item.safe_actions


def test_failed_job_with_accessible_source(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    source = _make_pdf(project_tmp_path, "ok.pdf")
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="ok.pdf",
            source_path=str(source),
            status="failed",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    items = list_maintenance_items()
    item = [i for i in items if i.related_job_id == job_id][0]
    assert item.can_retry_import is True
    assert item.blockers == []


# ---------------------------------------------------------------------------
# Temporary residue vs orphan temporary dir (mutual exclusion)
# ---------------------------------------------------------------------------


def test_temporary_residue_for_failed_job(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="a.pdf",
            source_path="/x/a.pdf",
            status="failed",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    # Create temporary/<job_id>/source.pdf
    temp_file = project_tmp_path / "library" / "temporary" / job_id / "source.pdf"
    temp_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file.write_bytes(b"%PDF-1.4 fake\n")

    items = list_maintenance_items()
    residue = [i for i in items if i.item_type == TEMPORARY_RESIDUE]
    orphan = [i for i in items if i.item_type == ORPHAN_TEMPORARY_DIR]
    assert len(residue) == 1
    assert residue[0].related_job_id == job_id
    assert orphan == []
    # The temp path is reported (compare resolved paths).
    resolved = {Path(p).resolve() for p in residue[0].paths}
    assert temp_file.resolve() in resolved


def test_orphan_temporary_dir_when_no_job(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    bogus = project_tmp_path / "library" / "temporary" / "notajobid1234567890abcdef"
    bogus.mkdir(parents=True, exist_ok=True)
    (bogus / "source.pdf").write_bytes(b"%PDF-1.4 fake\n")

    items = list_maintenance_items()
    residue = [i for i in items if i.item_type == TEMPORARY_RESIDUE]
    orphan = [i for i in items if i.item_type == ORPHAN_TEMPORARY_DIR]
    assert residue == []
    assert len(orphan) == 1
    assert orphan[0].related_job_id is None


# ---------------------------------------------------------------------------
# Soft-deleted paper / file
# ---------------------------------------------------------------------------


def test_soft_deleted_paper_identified(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        paper = Paper(title="Gone", status="active",
                      deleted_at=datetime.now(timezone.utc))
        session.add(paper)
        session.commit()
        paper_id = paper.id

    items = list_maintenance_items()
    matched = [i for i in items if i.item_id == f"paper:{paper_id}"]
    assert len(matched) == 1
    item = matched[0]
    assert item.item_type == SOFT_DELETED_PAPER
    assert item.can_restore is True
    assert item.can_purge is True
    assert RESTORE in item.safe_actions
    assert PURGE_DELETED_ASSET in item.dangerous_actions


def test_soft_deleted_file_identified(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        paper = Paper(status="active")
        session.add(paper)
        session.flush()
        pf = PaperFile(
            paper_id=paper.id,
            original_filename="f.pdf",
            relative_path=f"library/originals/{paper.id}/source.pdf",
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(pf)
        session.commit()
        pf_id = pf.id

    items = list_maintenance_items()
    matched = [i for i in items if i.item_id == f"file:{pf_id}"]
    assert len(matched) == 1
    assert matched[0].item_type == SOFT_DELETED_FILE


# ---------------------------------------------------------------------------
# Missing original file
# ---------------------------------------------------------------------------


def test_missing_original_file_identified(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        paper = Paper(status="active")
        session.add(paper)
        session.flush()
        pf = PaperFile(
            paper_id=paper.id,
            original_filename="missing.pdf",
            relative_path=f"library/originals/{paper.id}/source.pdf",
        )
        session.add(pf)
        session.commit()
        pf_id = pf.id

    items = list_maintenance_items()
    matched = [i for i in items if i.item_id == f"missing:{pf_id}"]
    assert len(matched) == 1
    item = matched[0]
    assert item.item_type == MISSING_ORIGINAL_FILE
    assert item.requires_user_input is True
    assert "requires_user_input" in item.blockers
    assert item.recommended_actions == [RECONCILE_MISSING_ORIGINAL]


# ---------------------------------------------------------------------------
# Orphan trash file
# ---------------------------------------------------------------------------


def test_orphan_trash_file_identified(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    trash_file = project_tmp_path / "library" / "trash" / "stray_file.pdf"
    trash_file.parent.mkdir(parents=True, exist_ok=True)
    trash_file.write_bytes(b"%PDF-1.4 fake\n")

    items = list_maintenance_items()
    matched = [i for i in items if i.item_type == ORPHAN_TRASH_FILE]
    assert len(matched) == 1
    assert matched[0].can_purge is True
    assert PURGE_TRASH_PATH in matched[0].safe_actions


# ---------------------------------------------------------------------------
# Preview: read-only contract and action matching
# ---------------------------------------------------------------------------


def test_preview_does_not_delete_files(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    bogus = project_tmp_path / "library" / "temporary" / "orphanxyz1234567890abcdef"
    bogus.mkdir(parents=True, exist_ok=True)
    leftover = bogus / "source.pdf"
    leftover.write_bytes(b"%PDF-1.4 fake\n")

    items = list_maintenance_items()
    item = [i for i in items if i.item_type == ORPHAN_TEMPORARY_DIR][0]

    result = preview_maintenance_action(item.item_id, PURGE_TEMPORARY_PATH)
    assert result.allowed is True
    assert result.will_delete_paths == item.paths
    # The file still exists: preview never deletes.
    assert leftover.exists()


def test_preview_inapplicable_action_blocked(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="a.pdf",
            source_path="/x/a.pdf",
            status="failed",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    item_id = f"ingestion:{job_id}"
    # purge_trash_path is not valid for a failed job.
    result = preview_maintenance_action(item_id, PURGE_TRASH_PATH)
    assert result.allowed is False
    assert "action_not_applicable_to_item_type" in result.blockers


def test_preview_missing_original_requires_user_input(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        paper = Paper(status="active")
        session.add(paper)
        session.flush()
        pf = PaperFile(
            paper_id=paper.id,
            original_filename="m.pdf",
            relative_path=f"library/originals/{paper.id}/source.pdf",
        )
        session.add(pf)
        session.commit()
        pf_id = pf.id

    item_id = f"missing:{pf_id}"
    result = preview_maintenance_action(item_id, RECONCILE_MISSING_ORIGINAL)
    assert result.allowed is False
    assert result.requires_user_input is True
    assert "requires_user_input" in result.blockers


def test_preview_unknown_item(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    result = preview_maintenance_action("ingestion:doesnotexist", RETRY_IMPORT)
    assert result.allowed is False
    assert "item_not_found" in result.blockers


def test_get_maintenance_item_returns_none_for_missing(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    assert get_maintenance_item("ingestion:nope") is None
