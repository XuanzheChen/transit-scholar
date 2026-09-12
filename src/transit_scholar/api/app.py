"""FastAPI application factory for the versioned product API.

The same application also hosts the built local Web UI as static assets, so
normal product use needs one local server and one browser origin:

* ``/api/v1/*`` — the frozen product HTTP API
* everything else — the React/TypeScript frontend build (``ui/dist``)

The legacy Stage 7 acceptance panel lives in ``transit_scholar.web.app`` and
stays a separate application; the formal product UI is not an extension of it.
"""
from __future__ import annotations

import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from transit_scholar.api.errors import install_error_handlers
from transit_scholar.api.runtime import LocalExecutionManager
from transit_scholar.api.runtime_context import ApiRuntimeContext
from transit_scholar.api.routers import conversations, papers, runs, schemas, wiki, workspaces
from transit_scholar.api.schemas import CapabilityResponse, HealthResponse
from transit_scholar.config import Settings

#: Environment override for the built frontend directory (``ui/dist``).
UI_DIST_ENV_VAR = "TRANSIT_SCHOLAR_UI_DIST"

#: Stable machine-readable code when no frontend build can be found.
FRONTEND_BUILD_MISSING = "FRONTEND_BUILD_MISSING"

#: Web asset extensions that Windows' registry-derived ``mimetypes`` database
#: can map incorrectly (``.js`` is commonly reported as ``text/plain``), which
#: makes browsers reject ES module scripts. These values are registered before
#: serving the frontend build.
_WEB_ASSET_MIME_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".wasm": "application/wasm",
}

_FRONTEND_BUILD_HINT = (
    "The frontend build was not found. Run `npm run build` in the ui/ directory, "
    "or point TRANSIT_SCHOLAR_UI_DIST at a built frontend directory."
)


def _register_web_asset_mime_types() -> None:
    """Register correct content types for the frontend build's asset types."""
    for extension, media_type in _WEB_ASSET_MIME_TYPES.items():
        mimetypes.add_type(media_type, extension)


def _ui_dist_candidates(ui_dist: Path | str | None) -> list[Path]:
    """Ordered candidate locations for the built frontend.

    An explicit argument wins (used by tests and by embedding applications),
    then ``TRANSIT_SCHOLAR_UI_DIST``, then the repository-relative ``ui/dist``,
    then ``<cwd>/ui/dist`` for a source checkout started from another directory.
    """
    if ui_dist is not None:
        return [Path(ui_dist)]
    candidates: list[Path] = []
    override = os.environ.get(UI_DIST_ENV_VAR)
    if override and override.strip():
        candidates.append(Path(override.strip()))
    candidates.append(Path(__file__).resolve().parents[3] / "ui" / "dist")
    candidates.append(Path.cwd() / "ui" / "dist")
    return candidates


def resolve_ui_dist_dir(ui_dist: Path | str | None = None) -> Path | None:
    """Return the first candidate directory containing a built ``index.html``."""
    seen: set[str] = set()
    for candidate in _ui_dist_candidates(ui_dist):
        target = candidate.expanduser()
        key = str(target)
        if key in seen:
            continue
        seen.add(key)
        if (target / "index.html").is_file():
            return target
    return None


def _mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Serve ``dist_dir`` as the single-origin frontend for this app.

    Hashed build assets are served from ``/assets``; the SPA entry document is
    served for client-side routes so navigation survives a page reload.

    The fallback is implemented as a 404 handler rather than a catch-all route
    so that routes registered after ``create_app`` keep working, and so unknown
    API paths keep their JSON 404 semantics instead of returning HTML.
    """
    _register_web_asset_mime_types()
    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="ui-assets")

    index_file = dist_dir / "index.html"
    root = dist_dir.resolve()

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(index_file, media_type="text/html")

    @app.exception_handler(StarletteHTTPException)
    async def frontend_or_http_error(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        if (
            exc.status_code == 404
            and not path.startswith("/api/")
            and not path.startswith("/assets/")
        ):
            if path and path != "/":
                try:
                    candidate = (root / path.lstrip("/")).resolve()
                except OSError:
                    candidate = index_file
                if candidate.is_file() and candidate.is_relative_to(root):
                    return FileResponse(candidate)
            return FileResponse(index_file, media_type="text/html")
        # Preserve the default HTTP error response for API and asset paths.
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )


def _mount_frontend_build_missing(app: FastAPI, expected_dirs: list[Path]) -> None:
    """Report a missing frontend build explicitly instead of failing at import."""

    @app.get("/", include_in_schema=False)
    def frontend_build_missing() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": FRONTEND_BUILD_MISSING,
                    "message": _FRONTEND_BUILD_HINT,
                    "details": {"expected_dist": [str(path) for path in expected_dirs]},
                }
            },
        )


def create_app(
    *,
    data_root: Path | str | None = None,
    runtime_context=None,
    execution_manager=None,
    ui_dist: Path | str | None = None,
) -> FastAPI:
    """Create an API app with request-owned product facades and one worker.

    ``ui_dist`` optionally points at a built frontend directory; when omitted
    the standard repository ``ui/dist`` location is used if it exists.
    """
    product_settings = Settings(data_root=Path(data_root)) if data_root is not None else Settings()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime_context.initialize()
        manager = app.state.execution_manager
        reconcile = getattr(manager, "reconcile_interrupted_runs", None)
        if callable(reconcile):
            reconcile()
        else:
            active_run_id = getattr(manager, "active_run_id", None)
            product = app.state.product_factory()
            try:
                product.reconcile_interrupted_runs({active_run_id} if active_run_id else set())
            finally:
                product.close()
        try:
            yield
        finally:
            app.state.execution_manager.shutdown()

    app = FastAPI(title="Transit Scholar API", version="1.0.0", lifespan=lifespan)
    app.state.runtime_context = runtime_context or ApiRuntimeContext(product_settings)
    app.state.product_factory = app.state.runtime_context.create_product
    app.state.execution_manager = execution_manager or LocalExecutionManager(app.state.product_factory)
    install_error_handlers(app)

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/v1/capabilities", response_model=CapabilityResponse)
    def capabilities() -> CapabilityResponse:
        return CapabilityResponse(
            pause_resume=app.state.runtime_context.agent_runtime_available,
            user_schema_creation=True,
            base_wiki=True,
            agentic_wiki=True,
            semantic_wiki_search=True,
            pdf_upload_max_bytes=app.state.runtime_context.settings.max_file_size_bytes,
        )

    app.include_router(papers.router)
    app.include_router(schemas.router)
    app.include_router(workspaces.router)
    app.include_router(conversations.router)
    app.include_router(runs.router)
    app.include_router(wiki.router)

    # Frontend hosting is registered last so it can never shadow API routes.
    frontend_dir = resolve_ui_dist_dir(ui_dist)
    app.state.ui_dist = frontend_dir
    if frontend_dir is not None:
        _mount_frontend(app, frontend_dir)
    else:
        _mount_frontend_build_missing(app, _ui_dist_candidates(ui_dist))

    return app
