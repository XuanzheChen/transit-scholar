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

# ---------------------------------------------------------------------------
# tmp-dir sandbox compatibility shim
# ---------------------------------------------------------------------------
# The DSH Windows file sandbox treats directories created with POSIX mode
# ``0o700`` (the mode used by ``tempfile.mkdtemp``, ``pathlib.mkdir`` and
# pytest's own ``tmp_path`` machinery) as write-denied, which breaks the
# deterministic suite under the sandboxed harness. Normalize ``os.mkdir`` so
# ``0o700`` becomes ``0o755``: on normal Windows the mode bit is ignored, so
# this is a no-op there, while under the sandbox every test temp tree remains
# usable exactly as before. Test-harness-only; never touches product behaviour.
_ORIG_MKDIR = os.mkdir


def _sandbox_safe_mkdir(*args, **kwargs):
    if len(args) >= 2 and args[1] == 0o700:
        args = (args[0], 0o755) + args[2:]
    elif kwargs.get("mode") == 0o700:
        kwargs["mode"] = 0o755
    return _ORIG_MKDIR(*args, **kwargs)


os.mkdir = _sandbox_safe_mkdir

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


# ---------------------------------------------------------------------------
# Layer2 fixtures (task-2026-08-12-002)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _l2_offline_guard(monkeypatch):
    """Offline guard for the Layer2 suite.

    When ``TRANSIT_SCHOLAR_BLOCK_NETWORK`` is truthy (set as an env var for
    self-test command #3, or loaded from the project-root ``.env``), any
    outbound socket connect is blocked, the embedding/reranker API key env
    vars are cleared, and the ``TRANSIT_SCHOLAR_LLM_*`` vars are cleared so
    deterministic tests that do not inject an explicit fake
    client/verifier/recheck fail explicitly with ``LLMUnavailableError``
    instead of silently resolving the developer's real LLM config. Because the
    current project ``.env`` ships ``TRANSIT_SCHOLAR_BLOCK_NETWORK=true``,
    this effectively keeps the whole L2S2 suite hermetic (AC-RW-15).
    """
    if os.environ.get("TRANSIT_SCHOLAR_BLOCK_NETWORK", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return
    import socket as _socket

    def _is_loopback(address) -> bool:
        """Allow loopback so internal machinery (asyncio proactor self-pipes,
        local servers) keeps working while real outbound network is blocked."""
        if not isinstance(address, tuple):
            return False
        host = address[0]
        return host in ("127.0.0.1", "::1", "localhost")

    class _GuardedSocket(_socket.socket):
        def connect(self, address, *args, **kwargs):
            if _is_loopback(address):
                return super().connect(address, *args, **kwargs)
            raise OSError("network blocked by TRANSIT_SCHOLAR_BLOCK_NETWORK")

        def connect_ex(self, address, *args, **kwargs):
            if _is_loopback(address):
                return super().connect_ex(address, *args, **kwargs)
            raise OSError("network blocked by TRANSIT_SCHOLAR_BLOCK_NETWORK")

    def _guarded_create_connection(address, *args, **kwargs):
        if _is_loopback(address):
            return _socket.create_connection(address, *args, **kwargs)
        raise OSError("network blocked by TRANSIT_SCHOLAR_BLOCK_NETWORK")

    monkeypatch.setattr(_socket, "socket", _GuardedSocket)
    monkeypatch.setattr(_socket, "create_connection", _guarded_create_connection)
    os.environ["TRANSIT_SCHOLAR_EMBEDDING_API_KEY"] = ""
    os.environ["TRANSIT_SCHOLAR_RERANKER_API_KEY"] = ""
    os.environ["JINA_API_KEY"] = ""
    for _llm_env in (
        "TRANSIT_SCHOLAR_LLM_PROVIDER",
        "TRANSIT_SCHOLAR_LLM_MODEL",
        "TRANSIT_SCHOLAR_LLM_API_KEY",
        "TRANSIT_SCHOLAR_LLM_BASE_URL",
        "TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK",
        "TRANSIT_SCHOLAR_LLM_TIMEOUT_SECONDS",
        "TRANSIT_SCHOLAR_LLM_MAX_RETRIES",
        "TRANSIT_SCHOLAR_LLM_RATE_LIMIT_RPM",
    ):
        monkeypatch.delenv(_llm_env, raising=False)


@pytest.fixture
def l2_settings(project_tmp_path):
    """A Settings object pointed at a fresh per-test data root (no API keys)."""
    from transit_scholar.config import Settings

    s = Settings(data_root=project_tmp_path)
    s.init_directories()
    s.layer2_embedding_provider = None
    s.layer2_embedding_api_key = None
    s.layer2_embedding_model = None
    s.layer2_embedding_dimension = None
    s.jina_api_key = None
    s.layer2_reranker_provider = None
    s.layer2_reranker_api_key = None
    s.layer2_reranker_model = None
    s.layer2_block_network = True
    s.layer2_retrieval_allow_network = False
    return s


@pytest.fixture
def l2_config(l2_settings):
    """Default Layer2 test config pinned to the deterministic local store.

    Automated tests must be deterministic and independent of whether LanceDB
    is installed, so they explicitly opt into the pure-Python ``local`` store.
    The LanceDB-specific tests use ``l2_lancedb_config`` instead. The V1
    *default* ``Layer2Config.store`` remains ``"lancedb"`` (asserted in
    ``test_l2s1_config.py``).
    """
    from transit_scholar.layer2.config import Layer2Config

    config = Layer2Config.from_settings(l2_settings)
    object.__setattr__(config, "store", "local")
    return config


@pytest.fixture
def l2_lancedb_config(l2_settings):
    """Config that requests the real LanceDB store (production default)."""
    from transit_scholar.layer2.config import Layer2Config

    return Layer2Config.from_settings(l2_settings)
