"""Bootstrap the Stage 7 acceptance data root.

Initialises the directory tree and runs the shared Alembic upgrade-to-head
service (``transit_scholar.db.lifecycle.alembic_upgrade_head``) against the
acceptance database. The default root is ``data/stage7_acceptance`` but any
root can be supplied for isolated runs.
"""

from __future__ import annotations

import os
from pathlib import Path

from transit_scholar.config import settings
from transit_scholar.db.lifecycle import alembic_upgrade_head


DEFAULT_STAGE7_ROOT = Path("data") / "stage7_acceptance"


def bootstrap_data_root(data_root: str | Path | None = None) -> Path:
    """Create the data root directory tree and upgrade the database.

    Returns the resolved ``data_root`` path. Idempotent: safe to call on an
    existing acceptance root.

    Mutates the global ``transit_scholar.config.settings`` object and rebinds
    the ``transit_scholar.db.engine`` engine and ``SessionLocal`` to the
    resulting database, so the whole Web stack targets the given root.
    """
    root = Path(data_root) if data_root is not None else DEFAULT_STAGE7_ROOT

    os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = str(root)

    # Point the GLOBAL settings at this root so every module that imported
    # ``transit_scholar.config.settings`` (the web app, alembic env, services)
    # sees the new data_root and the derived database_url.
    settings.data_root = root
    settings.init_directories()

    # Re-bind the engine singleton and the SessionLocal session factory to the
    # new database_url, so sessions opened after bootstrap hit this root's DB.
    from transit_scholar.db import engine as _engine_module

    new_engine = _engine_module.engine_for(settings.database_url)
    _engine_module.engine = new_engine
    _engine_module.SessionLocal.configure(bind=new_engine)

    alembic_upgrade_head()

    return root
