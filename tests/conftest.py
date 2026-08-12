"""Pytest fixtures for Stage 1.

The DB engine in ``transit_scholar.db.engine`` is a module-level singleton
bound to ``settings.database_url``. Because ``settings`` reads
``TRANSIT_SCHOLAR_DATA_DIR`` when the ``config`` package is first imported,
we set that env var here — before importing ``transit_scholar.db`` — so the
singleton points at an isolated temporary database.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

# Project-local temporary root, kept inside the repo so tests never touch the
# system default temp directory (which can have permission issues on some
# setups). Individual per-test subdirectories are created by the
# ``project_tmp_path`` fixture below.
_PYTEST_RUNS_ROOT = Path(__file__).resolve().parents[1] / "temp" / "pytest-runs"

# Create an isolated data root and point the settings at it BEFORE the
# transit_scholar packages are imported anywhere in this process.
# SQLite (and Alembic) will not create missing parent directories, so we must
# build the data tree up front.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="transit_scholar_test_"))
os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = str(_TMP_ROOT)

import pytest  # noqa: E402

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

# Build the data directory tree BEFORE importing the DB module, so the engine
# singleton binds to a path that already exists.
from transit_scholar.config import Settings  # noqa: E402

Settings(data_root=_TMP_ROOT).init_directories()

from transit_scholar.config import settings as _settings  # noqa: E402
from transit_scholar.db.base import Base  # noqa: E402
from transit_scholar.db.engine import engine, SessionLocal  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _alembic_head():
    """Run Alembic up to head once for the whole test session."""
    cfg = AlembicConfig("alembic.ini")
    alembic_command.upgrade(cfg, "head")
    yield


@pytest.fixture
def session():
    """Yield a transactional session and roll back after each test.

    The session joins the connection transaction using a SAVEPOINT, so a
    flush that raises ``IntegrityError`` only rolls back the savepoint and
    the outer transaction stays valid for clean teardown.
    """
    connection = engine.connect()
    transaction = connection.begin()
    bound_session = SessionLocal(
        bind=connection, join_transaction_mode="create_savepoint"
    )

    yield bound_session

    bound_session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def project_tmp_path():
    """A per-test temporary directory under the repo's ``temp/pytest-runs/``.

    Use this in place of pytest's built-in ``tmp_path`` to keep all test
    temporary files inside the repo and avoid system-temp permission issues.
    Each call creates a fresh unique subdirectory that is removed after the
    test.
    """
    _PYTEST_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    path = _PYTEST_RUNS_ROOT / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


# Sentinel distinguishing "TRANSIT_SCHOLAR_DATA_DIR was not set" from any real
# value, so the restore below can delete a key a test introduced instead of
# resurrecting a stale one.
_MISSING_ENV = object()


@pytest.fixture(autouse=True)
def _restore_global_data_root():
    """Restore the global data root and env var after each test.

    Several tests point the global ``settings.data_root`` at a per-test
    ``project_tmp_path`` via plain assignment (gate/metadata/API helpers) and
    ``bootstrap_data_root`` also rewrites ``TRANSIT_SCHOLAR_DATA_DIR``; neither
    is undone, and ``project_tmp_path`` deletes the directory at teardown, so
    later tests would read a stale, deleted temp root. Snapshot the global
    ``settings.data_root`` and the env var before the test and restore both
    afterwards (deleting the env key if it was absent before), so every test
    starts from the conftest-isolated root again.
    """
    original_root = _settings.data_root
    original_env = os.environ.get("TRANSIT_SCHOLAR_DATA_DIR", _MISSING_ENV)
    yield
    _settings.data_root = original_root
    if original_env is _MISSING_ENV:
        os.environ.pop("TRANSIT_SCHOLAR_DATA_DIR", None)
    else:
        os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = original_env


@pytest.fixture(scope="session", autouse=True)
def _cleanup(request):
    """Remove the temporary data root at the end of the session."""
    yield
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)
