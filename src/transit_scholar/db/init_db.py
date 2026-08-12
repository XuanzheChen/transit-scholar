"""Database initialisation entry point.

Formal database initialisation uses the shared Alembic upgrade-to-head
service from ``transit_scholar.db.lifecycle`` — the same path used by the
Web bootstrap. ``Base.metadata.create_all`` is intentionally not used here;
it remains available only to isolated unit tests.
"""

from transit_scholar.config import settings
from transit_scholar.db.lifecycle import alembic_upgrade_head


def init_db() -> None:
    """Create the data directory tree and upgrade the database to head.

    Ensures the data directory tree exists first, so calling ``init_db()``
    on a fresh checkout works without any manual setup, then runs the single
    shared Alembic upgrade-to-head path against the configured database.
    """
    settings.init_directories()
    alembic_upgrade_head()
