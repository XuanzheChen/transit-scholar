"""Request-scoped dependencies for the formal API."""
from collections.abc import Iterator
from fastapi import Request

from transit_scholar.config import settings
from transit_scholar.db.engine import SessionLocal
from transit_scholar.product.bootstrap import build_local_product


def get_session() -> Iterator:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def build_product(product_settings=settings):
    """Build one Product facade for a request or background worker."""
    try:
        return build_local_product(product_settings)
    except Exception as exc:
        # Schema catalog endpoints do not require an LLM/runtime. Keep them
        # usable in an unconfigured local installation while preserving the
        # normal bootstrap path whenever a provider is configured.
        from transit_scholar.layer2.schema_extraction.errors import LLMUnavailableError
        if not isinstance(exc, LLMUnavailableError):
            raise
        from sqlalchemy.orm import sessionmaker
        from transit_scholar.db.engine import engine_for
        from transit_scholar.product.facade import TransitScholarProduct
        session_factory = sessionmaker(
            bind=engine_for(product_settings.database_url),
            autoflush=False,
            autocommit=False,
            future=True,
        )
        return TransitScholarProduct(
            session_factory(), runtime_factory=None, data_root=product_settings.data_root
        )


def get_product(request: Request):
    """Yield a request-owned facade configured by the application factory."""
    product = request.app.state.product_factory()
    try:
        yield product
    finally:
        product.close()
