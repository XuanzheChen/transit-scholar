"""Stage 1 automated tests for the directory + database skeleton.

Covers:
- data directory initialisation
- Alembic migration creating the four core tables
- session open / commit / query
- Paper + PaperAuthor + IngestionJob creation and relations
- sha256 / relative_path unique constraints
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

# Import lazily: conftest.py must set TRANSIT_SCHOLAR_DATA_DIR first.
from transit_scholar.config import settings  # noqa: E402
from transit_scholar.db.engine import engine as _engine  # noqa: E402
from transit_scholar.db.models import (  # noqa: E402
    IngestionJob,
    Paper,
    PaperAuthor,
    PaperFile,
)


def test_settings_paths_derive_from_data_root():
    """All configured paths sit under the data root."""
    root = settings.data_root
    assert settings.database_dir == root / "database"
    assert settings.database_path == root / "database" / "transit_scholar.db"
    assert settings.library_root == root / "library"
    assert settings.originals_dir == root / "library" / "originals"
    assert settings.temporary_dir == root / "library" / "temporary"
    assert settings.trash_dir == root / "library" / "trash"
    assert settings.logs_dir == root / "logs"


def test_init_directories_creates_all_dirs(project_tmp_path):
    """init_directories() creates the full data tree on demand.

    Uses a local Settings instance so the global ``settings`` is not mutated.
    """
    from transit_scholar.config import Settings

    local = Settings(data_root=project_tmp_path)
    local.init_directories()

    assert (project_tmp_path / "database").is_dir()
    assert (project_tmp_path / "library" / "originals").is_dir()
    assert (project_tmp_path / "library" / "temporary").is_dir()
    assert (project_tmp_path / "library" / "trash").is_dir()
    assert (project_tmp_path / "logs").is_dir()


def test_init_db_creates_fresh_data_tree(project_tmp_path, monkeypatch):
    """init_db() on a brand-new data root creates the full directory tree.

    Regression test for the empty-checkout scenario: calling the real
    ``init_db()`` entry point must create ``data/database``, ``data/library/*``,
    and ``data/logs`` before touching the database, so it does not fail when
    the target parent directory does not exist yet.
    """
    from transit_scholar import config as _config
    from transit_scholar.db import init_db as init_db_fn

    # Point the global settings at a fresh, empty temp dir and confirm it is
    # truly empty before we call the entry point.
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    assert not (project_tmp_path / "database").exists()

    init_db_fn()

    assert (project_tmp_path / "database").is_dir()
    assert (project_tmp_path / "library" / "originals").is_dir()
    assert (project_tmp_path / "library" / "temporary").is_dir()
    assert (project_tmp_path / "library" / "trash").is_dir()
    assert (project_tmp_path / "logs").is_dir()


def test_database_file_exists():
    """The Alembic migration created the SQLite file."""
    assert Path(settings.database_path).is_file()


def test_four_core_tables_exist():
    """The four core tables (+ alembic_version) are present in the DB."""
    inspector = inspect(_engine)
    tables = set(inspector.get_table_names())
    for required in ("papers", "paper_files", "paper_authors", "ingestion_jobs"):
        assert required in tables, f"missing table: {required}"


def test_create_and_query_paper(session):
    """A Paper can be persisted and read back."""
    paper = Paper(title="Test Paper", status="active", publication_year=2024)
    session.add(paper)
    session.flush()

    fetched = session.query(Paper).filter_by(id=paper.id).one()
    assert fetched.title == "Test Paper"
    assert fetched.status == "active"
    assert fetched.publication_year == 2024
    assert fetched.created_at is not None


def test_create_author_linked_to_paper(session):
    """A PaperAuthor can be associated with its Paper."""
    paper = Paper(title="With Author")
    session.add(paper)
    session.flush()

    author = PaperAuthor(
        paper_id=paper.id,
        author_order=1,
        full_name="Jane Doe",
        normalized_name="jane doe",
    )
    session.add(author)
    session.flush()

    fetched_paper = session.query(Paper).filter_by(id=paper.id).one()
    assert len(fetched_paper.authors) == 1
    assert fetched_paper.authors[0].full_name == "Jane Doe"


def test_create_ingestion_job_linked_to_paper(session):
    """An IngestionJob can be associated with its Paper."""
    paper = Paper(title="Job Paper")
    session.add(paper)
    session.flush()

    job = IngestionJob(
        paper_id=paper.id,
        uploaded_filename="upload.pdf",
        status="accepted",
        current_stage="done",
    )
    session.add(job)
    session.flush()

    fetched = session.query(IngestionJob).filter_by(id=job.id).one()
    assert fetched.paper_id == paper.id
    assert fetched.status == "accepted"
    assert fetched.paper.title == "Job Paper"


def test_sha256_unique_constraint(session):
    """Two PaperFiles with the same sha256 violate the unique constraint."""
    file_a = PaperFile(sha256="a" * 64, relative_path="a.pdf")
    file_b = PaperFile(sha256="a" * 64, relative_path="b.pdf")
    session.add(file_a)
    session.add(file_b)

    with pytest.raises(IntegrityError):
        session.flush()


def test_relative_path_unique_constraint(session):
    """Two PaperFiles with the same relative_path violate the unique constraint."""
    file_a = PaperFile(sha256="1" * 64, relative_path="same.pdf")
    file_b = PaperFile(sha256="2" * 64, relative_path="same.pdf")
    session.add(file_a)
    session.add(file_b)

    with pytest.raises(IntegrityError):
        session.flush()


def test_indexes_present():
    """Expected indexes exist on the core tables."""
    inspector = inspect(_engine)
    papers_idx = {i["name"] for i in inspector.get_indexes("papers")}
    assert "ix_papers_normalized_title" in papers_idx
    assert "ix_papers_normalized_doi" in papers_idx
    assert "ix_papers_arxiv_id" in papers_idx
    assert "ix_papers_status" in papers_idx

    authors_idx = {i["name"] for i in inspector.get_indexes("paper_authors")}
    assert "ix_paper_authors_normalized_name" in authors_idx

    jobs_idx = {i["name"] for i in inspector.get_indexes("ingestion_jobs")}
    assert "ix_ingestion_jobs_status" in jobs_idx
