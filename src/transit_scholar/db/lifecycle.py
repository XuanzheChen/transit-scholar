"""Shared database lifecycle service.

One Alembic upgrade-to-head path is used by the formal CLI initialisation
entry point (``transit_scholar.db.init_db.init_db``) and by the Web bootstrap
(``transit_scholar.web.bootstrap``), so formal database creation never
maintains two divergent code paths. ``Base.metadata.create_all`` is
intentionally not part of this service; it remains available only to
isolated unit tests.

The archive-then-rebuild operation is destructive by design and therefore
deliberately narrow:

* it requires the exact target-bound per-call authorization phrase
  ``REBUILD_DATABASE:<resolved absolute database path>`` -- there is no
  default, stored, or ambient authorization anywhere in the codebase;
* it permanently refuses the repository root and ``data/stage7_acceptance/``
  (and everything inside the acceptance tree) regardless of the phrase. The
  default project data root ``data/`` is deliberately *not* permanently
  protected: the default database stays rebuildable after the exact phrase,
  and the workflow additionally requires a fresh explicit user approval at
  execution time before the default database is ever rebuilt;
* it archives the old SQLite database with full ``sqlite3`` backup semantics
  (committed WAL frames included) to the frozen archive root
  ``output/database-archives/<UTC timestamp>/`` together with a
  machine-readable ``manifest.json`` before the live file is touched;
* the manifest carries a lifecycle ``status`` -- ``archived`` before the live
  replacement, ``rebuilt`` on success, and ``failure_rolled_back`` (with the
  failure facts) when the upgrade fails after archival, in which case the
  old live database is restored from the archive and the original exception
  is re-raised;
* it then deletes the live database and creates a clean head database that
  never imports old rows. Archived copies are never deleted automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from transit_scholar.config import Settings, settings
from transit_scholar.db.engine import engine_for

#: Repository root, used to anchor the frozen archive root and to resolve
#: the protected ``data`` tree independent of the current working directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: Frozen root for archived databases. Archived copies are never deleted.
DEFAULT_ARCHIVE_ROOT = _PROJECT_ROOT / "output" / "database-archives"

#: Default project data root. Not permanently protected: the default
#: database under it can be rebuilt after the caller supplies the exact
#: target-bound authorization phrase (see ``_authorization_phrase``).
PROTECTED_DATA_ROOT = _PROJECT_ROOT / "data"

#: Acceptance data tree. Permanently protected regardless of authorization.
PROTECTED_STAGE7_ROOT = PROTECTED_DATA_ROOT / "stage7_acceptance"

#: Marker for the machine-readable archive manifest.
MANIFEST_FORMAT = "transit-scholar-database-archive-manifest-v1"


class RebuildAuthorizationError(ValueError):
    """Raised when an archive/rebuild call lacks the exact target-bound
    per-call authorization phrase."""


class ProtectedDatabaseError(ValueError):
    """Raised when an archive/rebuild call targets a permanently protected
    root (the repository root or ``data/stage7_acceptance`` and its
    descendants)."""


class DatabaseMissingError(FileNotFoundError):
    """Raised when the database file to archive does not exist."""


def alembic_upgrade_head() -> None:
    """Run ``alembic upgrade head`` against the configured database.

    This is the single formal upgrade-to-head path. The Alembic ``env.py``
    derives the target URL from ``transit_scholar.config.settings``, so the
    caller must point ``settings.data_root`` at the intended root first --
    both ``init_db()`` and the Web bootstrap do exactly that. The
    ``alembic.ini`` path is anchored to the repository root so the service
    is independent of the current working directory.
    """
    cfg = AlembicConfig(str(_PROJECT_ROOT / "alembic.ini"))
    alembic_command.upgrade(cfg, "head")


def collect_database_facts(database_path: str | Path) -> dict[str, Any]:
    """Return auditable facts about an existing SQLite database file.

    Facts: Alembic revision (from ``alembic_version``), business table names
    and per-table row counts. Raises ``DatabaseMissingError`` when the file
    does not exist and ``ValueError`` when it is not a valid SQLite database.
    """
    path = Path(database_path)
    if not path.is_file():
        raise DatabaseMissingError(f"database file does not exist: {path}")

    engine = engine_for(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        tables = sorted(
            table for table in inspector.get_table_names() if table != "alembic_version"
        )
        with engine.connect() as connection:
            revision = None
            if inspector.has_table("alembic_version"):
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
            row_counts: dict[str, int] = {}
            for table in tables:
                row_counts[table] = connection.execute(
                    text(f'SELECT COUNT(*) FROM "{table}"')
                ).scalar()
    except OperationalError as exc:
        raise ValueError(
            f"database file is not a valid SQLite database: {path}"
        ) from exc
    finally:
        engine.dispose()

    return {"revision": revision, "tables": tables, "row_counts": row_counts}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_attributes(path: Path) -> dict[str, Any]:
    info = path.stat()
    return {
        "size_bytes": info.st_size,
        "mtime_utc": datetime.fromtimestamp(info.st_mtime, tz=timezone.utc).isoformat(),
        "mode": oct(stat.S_IMODE(info.st_mode)),
    }


def _new_archive_dir(archive_root: Path) -> Path:
    """Return an unused ``<UTC timestamp>`` directory under ``archive_root``."""
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = archive_root / base
    suffix = 1
    while candidate.exists():
        candidate = archive_root / f"{base}-{suffix}"
        suffix += 1
    return candidate


def _authorization_phrase(database_path: Path) -> str:
    """Return the exact per-call authorization phrase for ``database_path``.

    The phrase binds the caller's confirmation to the resolved absolute
    database path that will be destroyed, so a generic or copied token never
    unlocks a rebuild. It is derived at call time and never defaulted,
    stored, or read from the environment or configuration.
    """
    return f"REBUILD_DATABASE:{database_path.resolve()}"


def _check_authorization(authorization: str, database_path: Path) -> None:
    """Refuse the call unless the phrase names the exact target database."""
    expected = _authorization_phrase(database_path)
    if authorization != expected:
        raise RebuildAuthorizationError(
            "rebuild_database requires the exact target-bound per-call "
            f"authorization phrase {expected!r}; it is never defaulted, "
            "stored, or read from the environment or config"
        )


def _ensure_not_protected(data_root: Path) -> None:
    """Refuse rebuild targets inside the permanently protected roots.

    Only the repository root and ``data/stage7_acceptance`` (plus anything
    inside the acceptance tree) are permanently refused, regardless of the
    authorization phrase. The default project data root is deliberately not
    listed here: it stays rebuildable after the exact target-bound phrase,
    and the workflow requires a fresh explicit user approval at execution
    time before the default database is ever rebuilt.
    """
    resolved = data_root.resolve()
    if resolved == _PROJECT_ROOT.resolve():
        raise ProtectedDatabaseError(
            f"refusing to rebuild the repository root itself: {resolved}"
        )
    stage7 = PROTECTED_STAGE7_ROOT.resolve()
    if resolved == stage7 or stage7 in resolved.parents:
        raise ProtectedDatabaseError(
            f"refusing to rebuild the protected acceptance data root {resolved} "
            f"(guard: {stage7})"
        )


def _backup_database(source_path: Path, dest_path: Path) -> None:
    """Copy a SQLite database with full ``sqlite3`` backup semantics.

    ``Connection.backup`` snapshots committed WAL frames together with the
    main file, so a WAL-mode database with uncheckpointed commits is
    archived completely instead of losing frames to a plain file copy. The
    connection performs no writes (SQLite may still create a WAL ``-shm``
    sidecar for a WAL-mode source, which the caller unlinks with the live
    database) and is closed before the caller may remove the live files.
    """
    source = sqlite3.connect(str(source_path))
    try:
        destination = sqlite3.connect(str(dest_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def _restore_archive(archive_path: Path, database_path: Path) -> None:
    """Restore the archived snapshot at the live database location.

    Used to compensate a failed post-archive upgrade: the archived snapshot
    (a self-contained backup, independent of any WAL sidecar) is copied back
    over the live path after any sidecars left by the failed attempt are
    removed.
    """
    for sidecar in (Path(f"{database_path}-wal"), Path(f"{database_path}-shm")):
        if sidecar.exists():
            sidecar.unlink()
    shutil.copy2(archive_path, database_path)


def _write_manifest(manifest_path: Path, manifest: dict[str, Any]) -> None:
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def rebuild_database(
    authorization: str,
    data_root: str | Path,
    archive_root: str | Path | None = None,
) -> dict[str, Any]:
    """Archive the old SQLite database and create a clean head database.

    The only destructive database operation in the codebase. Every call must
    supply the exact target-bound authorization phrase
    ``REBUILD_DATABASE:<resolved absolute database path>``; the value is
    never defaulted, stored, or read from the environment or configuration.
    The repository root and ``data/stage7_acceptance`` (and everything
    inside the acceptance tree) are permanently refused regardless of the
    phrase; the default project data root ``data/`` is rebuildable with the
    phrase, and the workflow additionally requires a fresh explicit user
    approval at execution time before the default database is rebuilt.

    Order of operations:

    1. validate the exact target-bound authorization phrase and refuse the
       permanently protected roots;
    2. read facts (revision, tables, row counts) from the old database;
    3. copy the old database to ``<archive_root>/<UTC timestamp>/`` with
       full ``sqlite3`` backup semantics (committed WAL frames included);
    4. write ``manifest.json`` (status ``archived``) with source/archive
       paths, SHA256, file attributes, revision, tables and per-table row
       counts;
    5. delete the live database file (plus WAL/SHM sidecars);
    6. create a clean database at the Alembic head -- old rows are never
       imported; on success the manifest status becomes ``rebuilt``;
    7. if the upgrade fails after archival, the old database is restored
       from the archive, the manifest records status ``failure_rolled_back``
       with the failure facts, and the original exception is re-raised.
       Archived copies are never deleted automatically.

    The global ``settings`` is pointed at ``data_root`` (same convention as
    ``web.bootstrap.bootstrap_data_root``) so the Alembic ``env.py`` resolves
    the same database URL for the upgrade step. Returns a summary dict with
    the archive/manifest locations and the recorded facts.
    """
    root = Path(data_root)
    database_path = Settings(data_root=root).database_path
    _check_authorization(authorization, database_path)
    _ensure_not_protected(root)

    if not database_path.is_file():
        raise DatabaseMissingError(f"no database to rebuild at {database_path}")

    # Point the global settings at the target root so ``alembic/env.py``
    # derives the same database URL for the upgrade step.
    os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = str(root)
    settings.data_root = root
    settings.init_directories()

    old_facts = collect_database_facts(database_path)

    archive_root_path = (
        Path(archive_root) if archive_root is not None else DEFAULT_ARCHIVE_ROOT
    )
    archive_dir = _new_archive_dir(archive_root_path)
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / database_path.name
    _backup_database(database_path, archive_path)

    manifest: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "status": "archived",
        "archived_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": str(database_path.resolve()),
        "archive_dir": str(archive_dir.resolve()),
        "archive_path": str(archive_path.resolve()),
        "sha256": _sha256_file(archive_path),
        "file_attributes": _file_attributes(archive_path),
        "revision": old_facts["revision"],
        "tables": old_facts["tables"],
        "row_counts": old_facts["row_counts"],
    }
    manifest_path = archive_dir / "manifest.json"
    _write_manifest(manifest_path, manifest)

    # The archive and manifest now exist; remove the live database file (and
    # any WAL/SHM sidecars) before creating the clean head database.
    for path in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
    ):
        if path.exists():
            path.unlink()

    try:
        alembic_upgrade_head()
    except Exception as exc:
        # Compensate the failed upgrade: restore the archived database at
        # the live location, record the failure facts in the manifest and
        # re-raise the original exception. The archive is always preserved.
        restore_error: str | None = None
        try:
            _restore_archive(archive_path, database_path)
        except Exception as restore_exc:
            restore_error = f"{type(restore_exc).__name__}: {restore_exc}"
        manifest["status"] = "failure_rolled_back"
        manifest["failed_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["failure_type"] = type(exc).__name__
        manifest["failure_message"] = str(exc)
        if restore_error is not None:
            manifest["restore_error"] = restore_error
        _write_manifest(manifest_path, manifest)
        raise

    new_facts = collect_database_facts(database_path)
    manifest["status"] = "rebuilt"
    manifest["rebuilt_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["new_revision"] = new_facts["revision"]
    _write_manifest(manifest_path, manifest)

    return {
        "archive_dir": str(archive_dir.resolve()),
        "archive_path": str(archive_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "old_revision": old_facts["revision"],
        "new_revision": new_facts["revision"],
        "archived_sha256": manifest["sha256"],
        "archived_tables": old_facts["tables"],
        "archived_row_counts": old_facts["row_counts"],
    }
