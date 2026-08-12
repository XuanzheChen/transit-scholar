"""Database lifecycle tests.

Covers the frozen DB acceptance slice (AC-DB-001 .. AC-DB-006) plus the
no-schema-change guard (AC-GLOBAL-003):

- formal init_db and Web bootstrap share one Alembic upgrade-to-head path,
  and ``Base.metadata.create_all`` stays a test-only affordance;
- an empty isolated root upgrades to the frozen head with the exact table set;
- the archive/rebuild API requires the exact target-bound per-call
  authorization phrase ``REBUILD_DATABASE:<resolved absolute database path>``;
  missing or mismatched phrases cause zero mutation;
- the repository root and ``data/stage7_acceptance`` (and descendants) are
  permanently refused even with the exact phrase, while the default project
  data root stays rebuildable with the exact phrase (simulated on an
  isolated root via monkeypatched lifecycle constants);
- archives are taken with full ``sqlite3`` backup semantics (committed WAL
  frames included) and carry a lifecycle ``status`` (``archived`` ->
  ``rebuilt`` or ``failure_rolled_back``); a forced post-archive upgrade
  failure restores the old live database from the archive and records the
  failure facts in the manifest.

All destructive behavior runs only against isolated temporary roots or
monkeypatched module constants; the real project ``data/`` tree is never
touched.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

# Import lazily: conftest.py must set TRANSIT_SCHOLAR_DATA_DIR first.
from transit_scholar.config import Settings, settings  # noqa: E402
from transit_scholar.db.base import Base  # noqa: E402
from transit_scholar.db.engine import engine_for  # noqa: E402
from transit_scholar.db.init_db import init_db  # noqa: E402
from transit_scholar.db.lifecycle import (  # noqa: E402
    DatabaseMissingError,
    ProtectedDatabaseError,
    RebuildAuthorizationError,
    alembic_upgrade_head,
    collect_database_facts,
    rebuild_database,
)
from transit_scholar.db.models import Paper, PaperFile  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]

FROZEN_HEAD = "c2f02a8e1b39"

#: Prefix of the exact target-bound per-call authorization phrase required
#: by ``rebuild_database`` (see ``lifecycle._authorization_phrase``).
_AUTH_PREFIX = "REBUILD_DATABASE:"

EXPECTED_BUSINESS_TABLES = {
    "papers",
    "paper_files",
    "paper_authors",
    "ingestion_jobs",
    "metadata_candidates",
    "paper_relations",
    "audit_logs",
    "citation_records",
    "citation_renders",
    "doi_enrichment_jobs",
    "doi_provider_results",
}


def _auth_phrase(data_root: Path) -> str:
    """Exact authorization phrase for the database under ``data_root``,
    bound to its resolved absolute path."""
    return f"{_AUTH_PREFIX}{Settings(data_root=data_root).database_path.resolve()}"


def _tables_and_revision(data_root: Path):
    """Inspect the database under ``data_root`` without touching globals."""
    from sqlalchemy import inspect, text

    engine = engine_for(Settings(data_root=data_root).database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
    finally:
        engine.dispose()
    return tables, revision


def _seed_isolated_db(data_root: Path) -> None:
    """Upgrade an isolated root to head and insert one paper + one file.

    The caller must have pointed ``settings.data_root`` at ``data_root``.
    """
    from sqlalchemy.orm import sessionmaker

    Settings(data_root=data_root).init_directories()
    alembic_upgrade_head()
    engine = engine_for(Settings(data_root=data_root).database_url)
    session = sessionmaker(bind=engine, future=True)()
    try:
        session.add(Paper(title="old row"))
        session.add(PaperFile(sha256="a" * 64, relative_path="old.pdf"))
        session.commit()
    finally:
        session.close()
        engine.dispose()


def test_init_db_uses_shared_upgrade_and_never_create_all(
    project_tmp_path, monkeypatch
):
    """AC-DB-001/002: init_db upgrades an empty isolated root via Alembic
    only; Base.metadata.create_all is never used by the formal entry point."""
    monkeypatch.setattr(settings, "data_root", project_tmp_path)
    assert not (project_tmp_path / "database").exists()

    called = {"create_all": False}

    def _spy(*args, **kwargs):
        called["create_all"] = True

    monkeypatch.setattr(Base.metadata, "create_all", _spy)

    init_db()
    init_db()  # repeatable from the same root

    assert (project_tmp_path / "database" / "transit_scholar.db").is_file()
    assert called["create_all"] is False
    tables, revision = _tables_and_revision(project_tmp_path)
    assert revision == FROZEN_HEAD
    assert tables == EXPECTED_BUSINESS_TABLES | {"alembic_version"}


def test_web_bootstrap_shares_the_same_upgrade_service():
    """AC-DB-001: the Web bootstrap re-uses the lifecycle upgrade service
    instead of maintaining a second Alembic code path."""
    from transit_scholar.web import bootstrap as bootstrap_module

    assert bootstrap_module.alembic_upgrade_head is alembic_upgrade_head
    assert bootstrap_module.alembic_upgrade_head.__module__ == (
        "transit_scholar.db.lifecycle"
    )


def test_bootstrap_data_root_upgrades_isolated_root(project_tmp_path, monkeypatch):
    """AC-DB-001/002: the Web bootstrap upgrades an empty isolated root to
    the frozen head with the exact table set."""
    from transit_scholar.db import engine as engine_module
    from transit_scholar.web import bootstrap as bootstrap_module

    old_engine = engine_module.engine
    old_session_local = engine_module.SessionLocal

    monkeypatch.setattr(settings, "data_root", project_tmp_path)
    monkeypatch.setenv("TRANSIT_SCHOLAR_DATA_DIR", str(project_tmp_path))
    try:
        root = bootstrap_module.bootstrap_data_root(project_tmp_path)
        assert root == project_tmp_path
    finally:
        # bootstrap rebinds the global engine; restore it for the rest of the
        # test session even when an assertion fails above.
        engine_module.engine = old_engine
        engine_module.SessionLocal.configure(bind=old_engine)

    tables, revision = _tables_and_revision(project_tmp_path)
    assert revision == FROZEN_HEAD
    assert tables == EXPECTED_BUSINESS_TABLES | {"alembic_version"}


def test_rebuild_requires_target_bound_authorization(project_tmp_path):
    """AC-DB-004: missing, malformed, or mismatched authorization phrases
    cause zero mutation -- the exact phrase must name the resolved absolute
    database path that will be rebuilt."""
    for token in (
        "",
        None,
        "   ",
        "test-token",
        "yes",
        "REBUILD_DATABASE:",
        _auth_phrase(project_tmp_path / "somewhere-else"),
    ):
        with pytest.raises(RebuildAuthorizationError):
            rebuild_database(token, data_root=project_tmp_path)
    # Refusal happens before any I/O: nothing was created.
    assert not (project_tmp_path / "database").exists()


def test_rebuild_refuses_permanently_protected_roots(project_tmp_path, monkeypatch):
    """The repository root and data/stage7_acceptance (and anything inside
    the acceptance tree) are permanently refused even with the exact
    target-bound authorization phrase."""
    from transit_scholar.db import lifecycle as lifecycle_module

    stage7 = project_tmp_path / "stage7_acceptance"
    monkeypatch.setattr(lifecycle_module, "PROTECTED_STAGE7_ROOT", stage7)

    for root in (stage7, stage7 / "library" / "originals", _REPO_ROOT):
        with pytest.raises(ProtectedDatabaseError):
            rebuild_database(_auth_phrase(root), data_root=root)
    # Refusal happens before any I/O: nothing was created under the guard.
    assert not (stage7 / "database").exists()


def test_rebuild_missing_database_raises(project_tmp_path):
    """A rebuild without an old database file has nothing to archive and
    refuses before any I/O."""
    with pytest.raises(DatabaseMissingError):
        rebuild_database(
            _auth_phrase(project_tmp_path), data_root=project_tmp_path
        )
    assert not (project_tmp_path / "database").exists()


def test_rebuild_archives_and_creates_clean_head_db(project_tmp_path, monkeypatch):
    """AC-DB-005/006: the old database is archived with a full manifest
    (source/archive paths, SHA256, attributes, revision, tables, row counts,
    lifecycle status ``rebuilt``) before a clean head database is created;
    old rows are never imported and archives are never deleted
    automatically."""
    monkeypatch.setattr(settings, "data_root", project_tmp_path)
    monkeypatch.setenv("TRANSIT_SCHOLAR_DATA_DIR", str(project_tmp_path))
    _seed_isolated_db(project_tmp_path)

    old_path = Settings(data_root=project_tmp_path).database_path
    old_facts = collect_database_facts(old_path)
    assert old_facts["revision"] == FROZEN_HEAD
    assert old_facts["row_counts"]["papers"] == 1
    assert old_facts["row_counts"]["paper_files"] == 1
    assert old_facts["row_counts"]["metadata_candidates"] == 0

    archive_root = project_tmp_path / "output" / "database-archives"
    result = rebuild_database(
        _auth_phrase(project_tmp_path),
        data_root=project_tmp_path,
        archive_root=archive_root,
    )

    assert result["old_revision"] == FROZEN_HEAD
    assert result["new_revision"] == FROZEN_HEAD

    archive_dir = Path(result["archive_dir"])
    assert archive_dir.parent == archive_root.resolve()
    assert archive_dir.is_dir()
    assert archive_dir.name.startswith("20")

    archived_db = archive_dir / "transit_scholar.db"
    assert archived_db.is_file()
    # The snapshot is content-identical to the old database; sqlite3 backup
    # may reorder pages, so byte equality with the source main file is not
    # expected -- SHA256 covers the archive file itself.
    archived_facts = collect_database_facts(archived_db)
    assert archived_facts["revision"] == FROZEN_HEAD
    assert set(archived_facts["tables"]) == EXPECTED_BUSINESS_TABLES
    assert archived_facts["row_counts"]["papers"] == 1
    assert archived_facts["row_counts"]["paper_files"] == 1
    assert archived_facts["row_counts"]["metadata_candidates"] == 0

    manifest_path = archive_dir / "manifest.json"
    assert Path(result["manifest_path"]) == manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["format"] == "transit-scholar-database-archive-manifest-v1"
    assert manifest["status"] == "rebuilt"
    assert manifest["source_path"] == str(old_path.resolve())
    assert manifest["archive_path"] == str(archived_db.resolve())
    assert manifest["sha256"] == hashlib.sha256(
        archived_db.read_bytes()
    ).hexdigest()
    assert manifest["file_attributes"]["size_bytes"] == archived_db.stat().st_size
    assert "mtime_utc" in manifest["file_attributes"]
    assert manifest["revision"] == FROZEN_HEAD
    assert set(manifest["tables"]) == EXPECTED_BUSINESS_TABLES
    assert manifest["row_counts"]["papers"] == 1
    assert manifest["row_counts"]["paper_files"] == 1
    assert manifest["row_counts"]["metadata_candidates"] == 0
    assert manifest["new_revision"] == FROZEN_HEAD
    assert result["archived_sha256"] == manifest["sha256"]

    # Clean head database: old rows are never imported.
    new_facts = collect_database_facts(old_path)
    assert new_facts["revision"] == FROZEN_HEAD
    assert set(new_facts["tables"]) == EXPECTED_BUSINESS_TABLES
    assert all(count == 0 for count in new_facts["row_counts"].values())

    # Archived copies remain after the rebuild (never auto-deleted).
    assert archived_db.is_file()
    assert manifest_path.is_file()


def test_rebuild_authorized_default_data_root(project_tmp_path, monkeypatch):
    """FR-DB-004/AC-DB-004/005: the default project data root is rebuildable
    after the exact target-bound authorization phrase -- it is not
    permanently refused. The permanent guards are monkeypatched onto an
    isolated tree so the real ``data/`` is never touched: the simulated root
    plays the role of the default data root and its ``stage7_acceptance``
    sibling stays permanently refused."""
    from transit_scholar.db import lifecycle as lifecycle_module

    fake_data_root = project_tmp_path / "data"
    monkeypatch.setattr(lifecycle_module, "PROTECTED_DATA_ROOT", fake_data_root)
    monkeypatch.setattr(
        lifecycle_module, "PROTECTED_STAGE7_ROOT", fake_data_root / "stage7_acceptance"
    )
    monkeypatch.setattr(settings, "data_root", fake_data_root)
    monkeypatch.setenv("TRANSIT_SCHOLAR_DATA_DIR", str(fake_data_root))
    _seed_isolated_db(fake_data_root)

    archive_root = project_tmp_path / "output" / "database-archives"
    result = rebuild_database(
        _auth_phrase(fake_data_root),
        data_root=fake_data_root,
        archive_root=archive_root,
    )

    assert result["old_revision"] == FROZEN_HEAD
    assert result["new_revision"] == FROZEN_HEAD
    manifest_path = Path(result["manifest_path"])
    # manifest.json sits inside the UTC timestamp directory; the parent of
    # that directory is the archive root.
    assert manifest_path.parent.parent == archive_root.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "rebuilt"
    assert manifest["source_path"] == str(
        Settings(data_root=fake_data_root).database_path.resolve()
    )
    assert manifest["row_counts"]["papers"] == 1
    assert manifest["row_counts"]["paper_files"] == 1

    # Clean head database: old rows are never imported.
    new_facts = collect_database_facts(Settings(data_root=fake_data_root).database_path)
    assert new_facts["revision"] == FROZEN_HEAD
    assert all(count == 0 for count in new_facts["row_counts"].values())

    # The acceptance sibling stays permanently refused even with its exact
    # phrase, and nothing is created under it.
    stage7 = fake_data_root / "stage7_acceptance"
    with pytest.raises(ProtectedDatabaseError):
        rebuild_database(_auth_phrase(stage7), data_root=stage7)
    assert not (stage7 / "database").exists()


def test_backup_database_includes_committed_wal_frames(project_tmp_path, monkeypatch):
    """``lifecycle._backup_database`` (the exact helper used by
    ``rebuild_database``) snapshots committed WAL frames: while a connection
    keeps the WAL uncheckpointed, a bare main-file copy misses the committed
    row but the sqlite3 backup contains it."""
    from transit_scholar.db import lifecycle as lifecycle_module

    monkeypatch.setattr(settings, "data_root", project_tmp_path)
    monkeypatch.setenv("TRANSIT_SCHOLAR_DATA_DIR", str(project_tmp_path))
    _seed_isolated_db(project_tmp_path)

    db_path = Settings(data_root=project_tmp_path).database_path
    archive_path = (
        project_tmp_path / "output" / "database-archives" / "wal-snapshot.db"
    )

    # Commit a second row while journal_mode=WAL; keep this connection open
    # so the committed frames stay in the WAL (closing the last connection
    # may checkpoint them into the main file, which would invalidate the
    # premise below).
    writer = sqlite3.connect(str(db_path))
    main_only_connection = None
    archived_connection = None
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO papers (id, title, status) VALUES (?, ?, 'active')",
            ("w" * 32, "wal committed row"),
        )
        writer.commit()

        # A bare copy of the main file alone (the old copy2 approach) misses
        # the committed WAL frame...
        main_only = project_tmp_path / "main_only.db"
        main_only.write_bytes(db_path.read_bytes())
        main_only_connection = sqlite3.connect(str(main_only))
        main_only_count = main_only_connection.execute(
            "SELECT COUNT(*) FROM papers"
        ).fetchone()[0]
        assert main_only_count == 1

        # ...while the sqlite3 backup used by rebuild_database includes it,
        # even while the WAL connection stays open.
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        lifecycle_module._backup_database(db_path, archive_path)
        archived_connection = sqlite3.connect(str(archive_path))
        archived_count = archived_connection.execute(
            "SELECT COUNT(*) FROM papers"
        ).fetchone()[0]
        archived_row = archived_connection.execute(
            "SELECT title FROM papers WHERE id = ?", ("w" * 32,)
        ).fetchone()
        assert archived_count == 2
        assert archived_row == ("wal committed row",)
    finally:
        if archived_connection is not None:
            archived_connection.close()
        if main_only_connection is not None:
            main_only_connection.close()
        writer.close()


def test_rebuild_post_archive_upgrade_failure_restores_and_audits(
    project_tmp_path, monkeypatch
):
    """A forced upgrade failure after the archive was created preserves the
    archive, restores the old live database from it, records the rolled-back
    failure in the manifest and re-raises the original exception."""
    from transit_scholar.db import lifecycle as lifecycle_module

    monkeypatch.setattr(settings, "data_root", project_tmp_path)
    monkeypatch.setenv("TRANSIT_SCHOLAR_DATA_DIR", str(project_tmp_path))
    _seed_isolated_db(project_tmp_path)

    def _forced_failure(*args, **kwargs):
        raise RuntimeError("forced upgrade failure")

    monkeypatch.setattr(lifecycle_module, "alembic_upgrade_head", _forced_failure)

    archive_root = project_tmp_path / "output" / "database-archives"
    with pytest.raises(RuntimeError, match="forced upgrade failure"):
        rebuild_database(
            _auth_phrase(project_tmp_path),
            data_root=project_tmp_path,
            archive_root=archive_root,
        )

    # The archive and manifest are preserved and record the compensation.
    archive_dir = next(archive_root.iterdir())
    archived_db = archive_dir / "transit_scholar.db"
    assert archived_db.is_file()
    manifest_path = archive_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failure_rolled_back"
    assert manifest["failure_type"] == "RuntimeError"
    assert manifest["failure_message"] == "forced upgrade failure"
    assert "failed_at_utc" in manifest
    assert "rebuilt_at_utc" not in manifest
    assert "new_revision" not in manifest

    # The old live database is restored from the archive, byte for byte.
    restored_path = Settings(data_root=project_tmp_path).database_path
    assert restored_path.is_file()
    assert hashlib.sha256(restored_path.read_bytes()).hexdigest() == manifest["sha256"]
    restored_facts = collect_database_facts(restored_path)
    assert restored_facts["revision"] == FROZEN_HEAD
    assert restored_facts["row_counts"]["papers"] == 1
    assert restored_facts["row_counts"]["paper_files"] == 1


def test_create_all_remains_a_test_only_affordance(project_tmp_path):
    """AC-DB-001: Base.metadata.create_all still works for isolated tests
    but never stamps an Alembic revision."""
    from sqlalchemy import inspect

    engine = engine_for(f"sqlite:///{project_tmp_path / 'test_only.db'}")
    try:
        Base.metadata.create_all(bind=engine)
        tables = set(inspect(engine).get_table_names())
        assert "papers" in tables
        assert "alembic_version" not in tables
    finally:
        engine.dispose()
