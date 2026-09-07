from .facade import TransitScholarProduct
from .runtime import RuntimeFactory
from sqlalchemy.orm import sessionmaker
from transit_scholar.db.engine import engine_for
from transit_scholar.db.base import Base
import transit_scholar.db.models  # noqa: F401 - register ORM models


def build_local_product(settings=None):
    if settings is None:
        from transit_scholar.config import settings as settings
    settings.init_directories()
    engine = engine_for(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    runtime = RuntimeFactory(settings=settings, session_factory=session_factory)
    session = session_factory()
    return TransitScholarProduct(session, runtime)
