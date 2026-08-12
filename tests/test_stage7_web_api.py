"""Stage 7 Web API tests.

Exercises the FastAPI app's module-level handler functions directly against an
isolated acceptance data root (a temp directory), so the real ``data/`` tree is
never touched. No ``httpx`` / ``starlette.testclient.TestClient`` is used — the
handlers are plain functions that return dicts or raise ``HTTPException``.
"""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

import fitz
import pytest
from fastapi import UploadFile
from fastapi.exceptions import HTTPException

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import IngestionJob, Paper, PaperFile
from fastapi.responses import HTMLResponse

from transit_scholar.web import create_app
from transit_scholar.web.app import (
    _static_dir,
    citations,
    health,
    homepage,
    import_pdf,
    maintenance_item,
    maintenance_items,
    maintenance_preview,
    metadata_candidates,
    paper_detail,
    paper_trace,
    papers,
    second_layer,
)


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear business tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


@pytest.fixture
def api_root():
    """Bootstrap the app onto the conftest-isolated data root and return it."""
    root = Path(os.environ["TRANSIT_SCHOLAR_DATA_DIR"])
    _config.settings.data_root = root
    create_app(data_root=root)
    return root


def _make_pdf(dir_path: Path, name: str | None = None) -> Path:
    filename = name or f"up_{uuid.uuid4().hex}.pdf"
    path = dir_path / filename
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 550, 750),
        # Publication-context year ("Published 2024" — a bare year is rejected
        # by the deterministic parser) plus an explicit Abstract section with
        # a clear Introduction boundary, so the second-layer gate (AC-GATE-004)
        # can reach "ready" for this shared fixture.
        "Web API upload body\n"
        "Published 2024\n"
        "DOI: 10.9999/webapi\n"
        "Abstract\n"
        "This paper applies reinforcement learning to transit scheduling.\n"
        "Introduction\n"
        "The remainder of this paper presents the model and experiments.\n",
        fontsize=11,
    )
    doc.set_metadata({"title": "Web API Paper", "author": "QA"})
    doc.save(str(path))
    doc.close()
    return path


def _upload_file(pdf_path: Path) -> UploadFile:
    return UploadFile(filename=pdf_path.name, file=io.BytesIO(pdf_path.read_bytes()))


# ---------------------------------------------------------------------------
# Data-root switching
# ---------------------------------------------------------------------------


def test_data_root_switch_is_isolated(project_tmp_path):
    """create_app(data_root=...) must point the whole stack at that root.

    Verifies the global settings and the database connection are rewired to the
    given temp root, that the API reads/writes there, and that the default
    ``data/`` tree is left untouched.
    """
    default_db = Path("data") / "database" / "transit_scholar.db"
    before = default_db.stat() if default_db.exists() else None

    create_app(data_root=project_tmp_path)

    # Health reports the switched root and a database_url under it.
    body = health()
    assert body["status"] == "ok"
    assert body["data_root"] == str(project_tmp_path)
    assert str(project_tmp_path) in body["database_url"]

    # An imported paper is written to the switched database and listed.
    pdf_path = _make_pdf(project_tmp_path, "isolated.pdf")
    result = import_pdf(file=_upload_file(pdf_path))
    assert result["status"] in {"completed", "duplicate", "partial", "failed"}
    assert result["job_id"] is not None
    listed = papers()
    assert any(p["paper_id"] == result["paper_id"] for p in listed)

    # Default data/ tree was never touched.
    after = default_db.stat() if default_db.exists() else None
    assert after == before


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health(api_root):
    body = health()
    assert body["status"] == "ok"
    assert "data_root" in body


# ---------------------------------------------------------------------------
# Import + papers + detail + gate + metadata + citations
# ---------------------------------------------------------------------------


def test_import_upload_synthetic_pdf(api_root):
    pdf_path = _make_pdf(api_root, "upload_me.pdf")
    result = import_pdf(file=_upload_file(pdf_path))
    assert result["status"] in {"completed", "duplicate", "partial", "failed"}
    assert result["job_id"] is not None


def test_papers_list_and_detail(api_root):
    # Import a paper first.
    pdf_path = _make_pdf(api_root, "list_me.pdf")
    result = import_pdf(file=_upload_file(pdf_path))
    paper_id = result["paper_id"]
    assert paper_id is not None

    # List papers.
    listing = papers()
    assert any(p["paper_id"] == paper_id for p in listing)

    # Detail.
    detail = paper_detail(paper_id)
    assert detail["paper_id"] == paper_id


def test_paper_detail_not_found():
    with pytest.raises(HTTPException) as exc_info:
        paper_detail("00000000000000000000000000000000")
    assert exc_info.value.status_code == 404


def test_second_layer_input_route(api_root):
    pdf_path = _make_pdf(api_root, "gate_me.pdf")
    result = import_pdf(file=_upload_file(pdf_path))
    paper_id = result["paper_id"]
    assert paper_id is not None

    body = second_layer(paper_id)
    assert body["paper_id"] == paper_id
    assert body["status"] in {"ready", "blocked"}


def test_second_layer_route_after_exact_duplicate_upload(api_root):
    """Repeated import should not make the original paper detail panels fail."""
    pdf_path = _make_pdf(api_root, "dup_gate.pdf")
    first = import_pdf(file=_upload_file(pdf_path))
    second = import_pdf(file=_upload_file(pdf_path))
    assert first["paper_id"] is not None
    assert second["status"] == "duplicate"
    assert second["paper_id"] == first["paper_id"]

    body = second_layer(first["paper_id"])
    assert body["paper_id"] == first["paper_id"]
    assert body["status"] == "ready"


def test_metadata_candidates_route(api_root):
    pdf_path = _make_pdf(api_root, "meta_me.pdf")
    result = import_pdf(file=_upload_file(pdf_path))
    paper_id = result["paper_id"]
    assert paper_id is not None

    rows = metadata_candidates(paper_id)
    assert isinstance(rows, list)


def test_citations_route(api_root):
    pdf_path = _make_pdf(api_root, "cite_me.pdf")
    result = import_pdf(file=_upload_file(pdf_path))
    paper_id = result["paper_id"]
    assert paper_id is not None

    rows = citations(paper_id)
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# Maintenance routes
# ---------------------------------------------------------------------------


def test_maintenance_list_and_item_and_preview(api_root):
    # Seed a failed job so the maintenance list is non-empty.
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="broken.pdf",
            source_path="/nonexistent/broken.pdf",
            status="failed",
            error_message="boom",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    items = maintenance_items()
    item_id = f"ingestion:{job_id}"
    assert any(i["item_id"] == item_id for i in items)

    # Single item.
    one = maintenance_item(item_id)
    assert one["item_id"] == item_id

    # Preview.
    prev = maintenance_preview(item_id, {"action": "purge_temporary_path"})
    assert prev["action"] == "purge_temporary_path"
    assert "allowed" in prev


def test_maintenance_item_not_found():
    with pytest.raises(HTTPException) as exc_info:
        maintenance_item("ingestion:doesnotexist")
    assert exc_info.value.status_code == 404


def test_maintenance_preview_missing_action(api_root):
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="b.pdf",
            source_path="/x/b.pdf",
            status="failed",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    item_id = f"ingestion:{job_id}"
    with pytest.raises(HTTPException) as exc_info:
        maintenance_preview(item_id, {})
    assert exc_info.value.status_code == 400


def test_maintenance_preview_inapplicable_action(api_root):
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="c.pdf",
            source_path="/x/c.pdf",
            status="failed",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    item_id = f"ingestion:{job_id}"
    prev = maintenance_preview(item_id, {"action": "purge_trash_path"})
    assert prev["allowed"] is False
    assert "action_not_applicable_to_item_type" in prev["blockers"]


# ---------------------------------------------------------------------------
# Static homepage + static assets
# ---------------------------------------------------------------------------


def test_static_dir_points_to_web_static():
    """The static dir helper resolves to src/transit_scholar/web/static/."""
    sdir = _static_dir()
    assert sdir.name == "static"
    assert (sdir / "index.html").is_file()
    assert (sdir / "app.js").is_file()
    assert (sdir / "styles.css").is_file()


def test_homepage_returns_index_html():
    """homepage() serves the panel's index.html as an HTMLResponse."""
    response = homepage()
    assert isinstance(response, HTMLResponse)
    body = response.body.decode("utf-8")
    assert "<title>" in body
    assert "Acceptance Panel" in body
    assert "Import PDF" in body
    assert "/static/app.js" in body
    assert "/static/styles.css" in body


# ---------------------------------------------------------------------------
# Stage 7 hotfix: trace API route (A01-A03)
# ---------------------------------------------------------------------------


def test_a01_trace_route_returns_structure(api_root):
    """GET /api/papers/{id}/trace returns the full trace structure."""
    pdf_path = _make_pdf(api_root, "trace_struct.pdf")
    result = import_pdf(file=_upload_file(pdf_path))
    paper_id = result["paper_id"]
    assert paper_id is not None

    body = paper_trace(paper_id)
    assert body["paper_id"] == paper_id
    assert len(body["steps"]) == 6
    assert "metadata_summary" in body
    assert "metadata_candidates" in body
    assert "duplicate_relations" in body
    assert "second_layer_gate" in body
    assert "ingestion_jobs" in body


def test_a02_trace_load_does_not_affect_other_panels(api_root):
    """Loading trace is independent of other panels (separate request)."""
    pdf_path = _make_pdf(api_root, "trace_isolated.pdf")
    result = import_pdf(file=_upload_file(pdf_path))
    paper_id = result["paper_id"]
    assert paper_id is not None

    detail = paper_detail(paper_id)
    gate = second_layer(paper_id)
    trace = paper_trace(paper_id)
    meta = metadata_candidates(paper_id)

    # All four panels load successfully and independently.
    assert detail["paper_id"] == paper_id
    assert gate["paper_id"] == paper_id
    assert trace["paper_id"] == paper_id
    assert isinstance(meta, list)


def test_a03_trace_route_paper_not_found(api_root):
    """Trace route raises 404 for a non-existent paper."""
    with pytest.raises(HTTPException) as exc_info:
        paper_trace("00000000000000000000000000000000")
    assert exc_info.value.status_code == 404


def test_create_app_registers_homepage_and_static_mount(api_root):
    """create_app registers a GET / route and a /static mount."""
    app = create_app(data_root=api_root)
    routes = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/" in routes
    # StaticFiles mount registers a route whose name ends with the mount name.
    mounted = [r for r in app.routes if getattr(r, "name", "") == "static"]
    assert mounted, "expected a '/static' StaticFiles mount"
