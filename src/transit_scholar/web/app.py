"""FastAPI application for the local acceptance panel.

All routes are read-only except ``POST /api/import`` which ingests an uploaded
PDF through the existing pipeline. Maintenance actions are preview-only: there
is no execute/apply/run endpoint.

Handler functions are module-level so they can be exercised directly by tests
without an HTTP test client; ``create_app`` registers them as routes.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.params import File as _FileParam, Query as _QueryParam
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from transit_scholar.citation.service import list_citation_records
from transit_scholar.config import settings
from transit_scholar.doi_enrichment.service import (
    collect_provider_results,
    refresh_enrichment,
)
from transit_scholar.maintenance.service import preview_maintenance_action
from transit_scholar.maintenance import (
    get_maintenance_item as _get_maintenance_item,
)
from transit_scholar.maintenance import (
    list_maintenance_items as _list_maintenance_items,
)
from transit_scholar.web import schemas
from transit_scholar.web.bootstrap import bootstrap_data_root
from transit_scholar.workflow import (
    get_paper,
    get_paper_trace,
    get_second_layer_input,
    list_papers,
    run_import_pipeline,
)
from transit_scholar.workflow.readers import list_metadata_candidates

# Upload bounds (frozen acceptance value AC-WEB-003/004): a single PDF is
# capped at 100 MiB and streamed in bounded chunks, so no unbounded whole-file
# read or write ever happens. UPLOAD_TOO_LARGE is the stable machine-readable
# rejection code carried in the HTTP 413 response detail.
UPLOAD_MAX_BYTES = 104857600  # 100 MiB — exactly this many bytes are accepted
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB per read/write
UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"


def _static_dir() -> Path:
    """Return the absolute path to the web/static directory."""
    return Path(__file__).resolve().parent / "static"


def _resolve_param(value: Any) -> Any:
    """Return the underlying default of a FastAPI Query/File param.

    When a handler is called directly (not through the routing layer), the
    ``Query(...)``/``File(...)`` default objects are passed straight through.
    FastAPI normally substitutes their ``.default`` value; this reproduces
    that behaviour so the same function works in tests and in the live app.
    """
    if isinstance(value, (_QueryParam, _FileParam)):
        return value.default
    return value


def homepage() -> HTMLResponse:
    """Serve the acceptance panel's static index.html."""
    index_path = _static_dir() / "index.html"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


def health() -> dict:
    return {
        "status": "ok",
        "data_root": str(settings.data_root),
        "database_url": settings.database_url,
    }


def _upload_root() -> Path:
    """Return the shared staging parent for Web uploads.

    Each request gets its own uuid-named directory below this root; cleanup
    removes only that per-request directory, never this shared parent.
    """
    return Path("temp") / "stage7_uploads"


def _stream_upload(stream: Any, target: Path) -> None:
    """Copy an upload stream to ``target`` in bounded chunks.

    Every read passes an explicit size. As soon as the running total would
    exceed ``UPLOAD_MAX_BYTES`` the copy aborts with HTTP 413
    ``UPLOAD_TOO_LARGE`` so the pipeline is never handed an oversized file.
    """
    total = 0
    with target.open("wb") as out:
        while True:
            chunk = stream.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            if total + len(chunk) > UPLOAD_MAX_BYTES:
                raise HTTPException(status_code=413, detail=UPLOAD_TOO_LARGE)
            out.write(chunk)
            total += len(chunk)


def import_pdf(file: UploadFile = File(...)) -> dict:
    upload_root = _upload_root()
    upload_root.mkdir(parents=True, exist_ok=True)
    # Strip any path components from the client-supplied filename so the
    # upload can never escape the upload root, then isolate this upload in a
    # uuid-prefixed directory to guarantee no overwrites.
    safe_name = Path(file.filename or "upload.pdf").name or "upload.pdf"
    upload_dir = upload_root / uuid.uuid4().hex
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / safe_name
    try:
        _stream_upload(file.file, upload_path)
        result = run_import_pipeline(upload_path)
        return schemas.import_pipeline_result_to_dict(result)
    finally:
        # Remove only this request's staging directory. Completed, partial,
        # failed, oversized and exception paths all land here; the shared
        # upload parent and pipeline-owned data_root/library copies (the
        # temporary/originals residue) are never touched.
        shutil.rmtree(upload_dir, ignore_errors=True)


def papers(
    status: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    limit = _resolve_param(limit)
    offset = _resolve_param(offset)
    rows = list_papers(
        status=status,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )
    return [schemas.paper_summary_to_dict(r) for r in rows]


def paper_detail(paper_id: str) -> dict:
    detail = get_paper(paper_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return schemas.paper_detail_to_dict(detail)


def second_layer(paper_id: str) -> dict:
    return schemas.second_layer_to_dict(get_second_layer_input(paper_id))


def metadata_candidates(paper_id: str) -> list[dict]:
    rows = list_metadata_candidates(paper_id=paper_id)
    return [schemas.metadata_candidate_to_dict(r) for r in rows]


def paper_trace(paper_id: str) -> dict:
    trace = get_paper_trace(paper_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return schemas.paper_trace_to_dict(trace)


def citations(paper_id: str) -> list[dict]:
    rows = list_citation_records(paper_id)
    return [schemas.citation_record_view_to_dict(r) for r in rows]


def enrichment(paper_id: str) -> dict:
    result = collect_provider_results(paper_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return schemas.enrichment_to_dict(result)


def enrichment_refresh(paper_id: str) -> dict:
    if not settings.metadata_enrichment_allow_network:
        current = collect_provider_results(paper_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Paper not found")
        current.error_code = "network_disabled"
        current.error_message = "Metadata enrichment network access is disabled."
        return schemas.enrichment_to_dict(current)
    result = refresh_enrichment(paper_id)
    if result.error_code == "paper_not_found":
        raise HTTPException(status_code=404, detail="Paper not found")
    return schemas.enrichment_to_dict(result)


def maintenance_items() -> list[dict]:
    return [schemas.maintenance_item_to_dict(i) for i in _list_maintenance_items()]


def maintenance_item(item_id: str) -> dict:
    item = _get_maintenance_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Maintenance item not found")
    return schemas.maintenance_item_to_dict(item)


def maintenance_preview(item_id: str, body: dict) -> dict:
    action = body.get("action")
    if not action:
        raise HTTPException(status_code=400, detail="Missing 'action' in body")
    result = preview_maintenance_action(item_id, action)
    return schemas.maintenance_preview_to_dict(result)


def create_app(data_root: str | Path | None = None) -> FastAPI:
    """Build and return the FastAPI app, bootstrapping the data root."""
    bootstrap_data_root(data_root)

    app = FastAPI(title="TransitScholar Acceptance Panel")

    # Serve the static acceptance panel assets (index.html, app.js, styles.css).
    static_dir = _static_dir()
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.get("/", response_class=HTMLResponse)(homepage)
    app.get("/api/health")(health)
    app.post("/api/import")(import_pdf)
    app.get("/api/papers")(papers)
    app.get("/api/papers/{paper_id}")(paper_detail)
    app.get("/api/papers/{paper_id}/second-layer-input")(second_layer)
    app.get("/api/papers/{paper_id}/metadata-candidates")(metadata_candidates)
    app.get("/api/papers/{paper_id}/trace")(paper_trace)
    app.get("/api/papers/{paper_id}/citations")(citations)
    app.get("/api/papers/{paper_id}/enrichment")(enrichment)
    app.post("/api/papers/{paper_id}/enrichment/refresh")(enrichment_refresh)
    app.get("/api/maintenance/items")(maintenance_items)
    app.get("/api/maintenance/items/{item_id}")(maintenance_item)
    app.post("/api/maintenance/items/{item_id}/preview")(maintenance_preview)

    return app
