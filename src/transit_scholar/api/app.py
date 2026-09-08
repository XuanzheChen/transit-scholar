"""FastAPI application factory for the versioned product API."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from transit_scholar.api.dependencies import build_product
from transit_scholar.api.errors import install_error_handlers
from transit_scholar.api.runtime import LocalExecutionManager
from transit_scholar.api.routers import conversations, papers, runs, schemas, wiki, workspaces
from transit_scholar.api.schemas import CapabilityResponse, HealthResponse
from transit_scholar.config import Settings


def create_app(*, data_root: Path | str | None = None) -> FastAPI:
    """Create an API app with request-owned product facades and one worker."""
    product_settings = Settings(data_root=Path(data_root)) if data_root is not None else Settings()
    product_settings.init_directories()

    app = FastAPI(title="Transit Scholar API", version="1.0.0")
    app.state.product_factory = lambda: build_product(product_settings)
    app.state.execution_manager = LocalExecutionManager(lambda: app.state.product_factory())
    install_error_handlers(app)

    @app.on_event("startup")
    def reconcile_interrupted_runs() -> None:
        manager = app.state.execution_manager
        reconcile = getattr(manager, "reconcile_interrupted_runs", None)
        if callable(reconcile):
            reconcile()
            return

        active_run_id = getattr(manager, "active_run_id", None)
        product = app.state.product_factory()
        try:
            product.reconcile_interrupted_runs(
                {active_run_id} if active_run_id else set()
            )
        finally:
            product.close()

    @app.on_event("shutdown")
    def shutdown_execution_manager() -> None:
        app.state.execution_manager.shutdown()

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/v1/capabilities", response_model=CapabilityResponse)
    def capabilities() -> CapabilityResponse:
        return CapabilityResponse(
            pause_resume=True,
            user_schema_creation=True,
            base_wiki=True,
            agentic_wiki=True,
            semantic_wiki_search=True,
            pdf_upload_max_bytes=product_settings.max_file_size_bytes,
        )

    app.include_router(papers.router)
    app.include_router(schemas.router)
    app.include_router(workspaces.router)
    app.include_router(conversations.router)
    app.include_router(runs.router)
    app.include_router(wiki.router)
    return app
