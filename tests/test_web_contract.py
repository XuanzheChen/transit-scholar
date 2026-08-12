"""Web upload/enrichment contract tests (AC-WEB-001..006).

Covers bounded chunked upload reads, the exact 100 MiB upload limit, HTTP 413
``UPLOAD_TOO_LARGE`` with zero pipeline invocation, staging-directory cleanup
on every outcome (completed/partial/failed/exception/oversize), preservation
of pipeline-owned residue, enrichment serialization of all provider facts, the
controlled refresh network gate, and zero per-paper enrichment fan-out from
the paper list. Everything runs on isolated roots with fake streams/results:
no real network and no protected data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi.exceptions import HTTPException

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    DOIEnrichmentJob,
    DOIProviderResult,
    IngestionJob,
    Paper,
    PaperAuthor,
    PaperFile,
)
from transit_scholar.doi_enrichment import service as doi_service
from transit_scholar.doi_enrichment.clients.base import ProviderClient
from transit_scholar.doi_enrichment.result import (
    EnrichmentJobResult,
    ProviderResult,
)
from transit_scholar.web import app as app_module
from transit_scholar.web.app import (
    UPLOAD_CHUNK_SIZE,
    UPLOAD_MAX_BYTES,
    UPLOAD_TOO_LARGE,
    enrichment,
    enrichment_refresh,
    import_pdf,
    papers,
)
from transit_scholar.workflow import service as workflow_service
from transit_scholar.workflow.result import ImportPipelineResult

# Frozen clock instant used only for deterministic serialization assertions.
T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear business tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(DOIProviderResult).delete()
        session.query(DOIEnrichmentJob).delete()
        session.query(PaperAuthor).delete()
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


class FakeStream:
    """File-like object that records every read size for bounded-read audit.

    An unbounded ``read()`` (no size argument) returns an empty body so no
    caller hangs, but the tests assert that no such call ever happens.
    """

    def __init__(self, total_bytes: int = 0) -> None:
        self._remaining = total_bytes
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            return b""
        count = min(size, self._remaining)
        self._remaining -= count
        return b"\x00" * count


class FakePipeline:
    """Stand-in for run_import_pipeline: records calls, replays a result or error.

    The staged upload can only be observed while the pipeline call is
    running: ``import_pdf`` removes the per-request staging directory in its
    ``finally`` block (AC-WEB-005). Each call therefore snapshots whether the
    staged file exists and its exact size at call time.
    """

    def __init__(
        self,
        result: ImportPipelineResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[Path] = []
        self.seen_staged: list[tuple[bool, int | None]] = []

    def __call__(self, path):
        call_path = Path(path)
        self.calls.append(call_path)
        is_file = call_path.is_file()
        self.seen_staged.append(
            (is_file, call_path.stat().st_size if is_file else None)
        )
        if self.error is not None:
            raise self.error
        return self.result


def _pipeline_result(status: str) -> ImportPipelineResult:
    return ImportPipelineResult(
        status=status,
        job_id="job-web-contract",
        paper_id="p" * 32,
        file_id="f" * 32,
        is_exact_duplicate=False,
        import_status="accepted",
        metadata_status="completed",
        duplicate_status="completed",
        relations_created=0,
        relations_existing=0,
        relation_ids=[],
        current_stage="completed",
        error_code=None,
        error_message=None,
        warnings=[],
        second_layer_ready=True,
        second_layer_blockers=[],
        metadata_enrichment_status="pending",
        enrichment_provider_results=[],
    )


def _isolate_upload_root(monkeypatch, root: Path) -> Path:
    """Point the Web upload staging area at an isolated per-test directory."""
    upload_root = root / "stage7_uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_module, "_upload_root", lambda: upload_root)
    return upload_root


# ---------------------------------------------------------------------------
# AC-WEB-003/004: bounded chunked upload and the exact 100 MiB limit
# ---------------------------------------------------------------------------


def test_upload_reads_are_all_bounded(monkeypatch, project_tmp_path):
    """Every read passes an explicit size no larger than the chunk size."""
    _isolate_upload_root(monkeypatch, project_tmp_path)
    pipeline = FakePipeline(result=_pipeline_result("completed"))
    monkeypatch.setattr(app_module, "run_import_pipeline", pipeline)

    stream = FakeStream(total_bytes=200_000)
    result = import_pdf(file=UploadFile(filename="bounded.pdf", file=stream))

    assert result["status"] == "completed"
    assert stream.read_sizes, "the upload must actually read the stream"
    assert all(size > 0 for size in stream.read_sizes), (
        "an unbounded whole-file read (no size) is forbidden"
    )
    assert all(size <= UPLOAD_CHUNK_SIZE for size in stream.read_sizes)


def test_exact_upload_limit_reaches_pipeline(monkeypatch, project_tmp_path):
    """Exactly 104857600 bytes is accepted and handed to run_import_pipeline."""
    root = _isolate_upload_root(monkeypatch, project_tmp_path)
    pipeline = FakePipeline(result=_pipeline_result("completed"))
    monkeypatch.setattr(app_module, "run_import_pipeline", pipeline)

    stream = FakeStream(total_bytes=UPLOAD_MAX_BYTES)
    result = import_pdf(file=UploadFile(filename="exact.pdf", file=stream))

    assert result["status"] == "completed"
    assert len(pipeline.calls) == 1
    written = pipeline.calls[0]
    # The staged file exists at the exact accepted size only while the
    # pipeline runs: AC-WEB-005 cleanup removes the request directory in
    # import_pdf's finally block before this function returns.
    existed, size = pipeline.seen_staged[0]
    assert existed, "the pipeline must receive an existing staged file"
    assert size == UPLOAD_MAX_BYTES
    # The file sat in a uuid request directory directly under the upload root.
    assert written.parent.parent == root
    # Cleanup proof: the accepted path also removes the request directory.
    assert not written.exists()


def test_one_byte_over_limit_returns_413_and_skips_pipeline(monkeypatch, project_tmp_path):
    """104857601 bytes returns HTTP 413 UPLOAD_TOO_LARGE; pipeline never runs."""
    _isolate_upload_root(monkeypatch, project_tmp_path)
    pipeline = FakePipeline(result=_pipeline_result("completed"))
    monkeypatch.setattr(app_module, "run_import_pipeline", pipeline)

    stream = FakeStream(total_bytes=UPLOAD_MAX_BYTES + 1)
    with pytest.raises(HTTPException) as exc_info:
        import_pdf(file=UploadFile(filename="oversize.pdf", file=stream))

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == UPLOAD_TOO_LARGE
    assert pipeline.calls == []


# ---------------------------------------------------------------------------
# AC-WEB-005: staging-directory cleanup on every path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["completed", "partial", "failed"])
def test_upload_dir_removed_after_result_paths(monkeypatch, project_tmp_path, status):
    """Only this request's directory is removed; sibling entries survive."""
    root = _isolate_upload_root(monkeypatch, project_tmp_path)
    sibling = root / "other-request"
    sibling.mkdir()
    stray = root / "stray.txt"
    stray.write_text("keep me", encoding="utf-8")
    pipeline = FakePipeline(result=_pipeline_result(status))
    monkeypatch.setattr(app_module, "run_import_pipeline", pipeline)

    import_pdf(file=UploadFile(filename="clean.pdf", file=FakeStream(total_bytes=1000)))

    assert sibling.is_dir()
    assert stray.read_text(encoding="utf-8") == "keep me"
    assert sorted(p.name for p in root.iterdir()) == sorted(["other-request", "stray.txt"])


def test_upload_dir_removed_after_pipeline_exception(monkeypatch, project_tmp_path):
    """An exception escaping the pipeline still triggers the cleanup."""
    root = _isolate_upload_root(monkeypatch, project_tmp_path)
    sibling = root / "other-request"
    sibling.mkdir()
    pipeline = FakePipeline(error=RuntimeError("boom"))
    monkeypatch.setattr(app_module, "run_import_pipeline", pipeline)

    with pytest.raises(RuntimeError):
        import_pdf(file=UploadFile(filename="boom.pdf", file=FakeStream(total_bytes=1000)))

    assert sibling.is_dir()
    assert [p.name for p in root.iterdir()] == ["other-request"]


def test_upload_dir_removed_after_oversize(monkeypatch, project_tmp_path):
    """The oversized path also removes the partial staging directory."""
    root = _isolate_upload_root(monkeypatch, project_tmp_path)
    pipeline = FakePipeline(result=_pipeline_result("completed"))
    monkeypatch.setattr(app_module, "run_import_pipeline", pipeline)

    with pytest.raises(HTTPException) as exc_info:
        import_pdf(
            file=UploadFile(
                filename="oversize.pdf", file=FakeStream(total_bytes=UPLOAD_MAX_BYTES + 1)
            )
        )

    assert exc_info.value.status_code == 413
    assert list(root.iterdir()) == []
    assert pipeline.calls == []


def test_cleanup_preserves_pipeline_owned_residue(monkeypatch, project_tmp_path):
    """data_root/library copies (temporary/originals residue) are never touched."""
    root = _isolate_upload_root(monkeypatch, project_tmp_path)
    library = project_tmp_path / "library"
    residue = library / "temporary" / "residue.pdf"
    originals = library / "originals" / "keep.pdf"
    residue.parent.mkdir(parents=True, exist_ok=True)
    originals.parent.mkdir(parents=True, exist_ok=True)
    residue.write_text("temporary residue", encoding="utf-8")
    originals.write_text("originals copy", encoding="utf-8")
    pipeline = FakePipeline(result=_pipeline_result("completed"))
    monkeypatch.setattr(app_module, "run_import_pipeline", pipeline)

    import_pdf(file=UploadFile(filename="res.pdf", file=FakeStream(total_bytes=1000)))

    assert residue.read_text(encoding="utf-8") == "temporary residue"
    assert originals.read_text(encoding="utf-8") == "originals copy"
    assert list(root.iterdir()) == []


# ---------------------------------------------------------------------------
# AC-WEB-001: enrichment serialization exposes every provider fact
# ---------------------------------------------------------------------------


def test_enrichment_serializes_provider_facts(monkeypatch):
    paper_id = "p" * 32
    result = EnrichmentJobResult(
        paper_id=paper_id,
        doi="10.1000/example",
        status="partial",
        providers=[
            ProviderResult(
                provider="crossref",
                status="fetched",
                http_status=200,
                fetched_at=T0,
                attempt_count=1,
                next_retry_at=None,
                error_code=None,
                error_message=None,
                fields=["title", "authors", "publication_year"],
            ),
            ProviderResult(
                provider="openalex",
                status="skipped",
                http_status=None,
                fetched_at=None,
                attempt_count=0,
                next_retry_at=None,
                error_code="missing_api_key",
                error_message="OpenAlex API key is not configured; provider skipped.",
                fields=[],
            ),
            ProviderResult(
                provider="semantic_scholar",
                status="retry_scheduled",
                http_status=429,
                fetched_at=None,
                attempt_count=2,
                next_retry_at=T0,
                error_code="rate_limited",
                error_message="HTTP Error 429: Too Many Requests",
                fields=[],
            ),
        ],
        resolved={"title": "doi_provider:crossref"},
    )
    monkeypatch.setattr(app_module, "collect_provider_results", lambda pid: result)

    data = enrichment(paper_id)

    assert data["paper_id"] == paper_id
    assert data["doi"] == "10.1000/example"
    assert data["metadata_enrichment_status"] == "partial"
    assert data["resolved"] == {"title": "doi_provider:crossref"}
    required_keys = {
        "provider", "status", "http_status", "fetched_at", "attempt_count",
        "next_retry_at", "error_code", "error_message", "fields",
    }
    by_name = {p["provider"]: p for p in data["providers"]}
    assert set(by_name) == {"crossref", "openalex", "semantic_scholar"}
    for provider in data["providers"]:
        assert required_keys <= set(provider)

    crossref = by_name["crossref"]
    assert crossref["status"] == "fetched"
    assert crossref["http_status"] == 200
    assert crossref["attempt_count"] == 1
    assert crossref["fetched_at"] == T0.isoformat()
    assert crossref["next_retry_at"] is None
    assert crossref["fields"] == ["title", "authors", "publication_year"]

    openalex = by_name["openalex"]
    assert openalex["status"] == "skipped"
    assert openalex["error_code"] == "missing_api_key"
    assert openalex["attempt_count"] == 0

    semantic = by_name["semantic_scholar"]
    assert semantic["status"] == "retry_scheduled"
    assert semantic["http_status"] == 429
    assert semantic["error_code"] == "rate_limited"
    assert semantic["attempt_count"] == 2
    assert semantic["next_retry_at"] == T0.isoformat()


# ---------------------------------------------------------------------------
# AC-WEB-002: controlled refresh obeys network / API-key / rate-limit config
# ---------------------------------------------------------------------------


def test_refresh_network_disabled_returns_stable_code(monkeypatch):
    """Network-disabled refresh must not call the provider service at all."""
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)
    paper_id = "p" * 32
    canned = EnrichmentJobResult(paper_id=paper_id, doi="10.1000/example", status="pending")
    monkeypatch.setattr(app_module, "collect_provider_results", lambda pid: canned)
    monkeypatch.setattr(
        app_module,
        "refresh_enrichment",
        lambda *a, **k: pytest.fail("refresh must not run when network is disabled"),
    )

    data = enrichment_refresh(paper_id)

    assert data["error_code"] == "network_disabled"
    assert data["error_message"] == "Metadata enrichment network access is disabled."
    assert data["metadata_enrichment_status"] == "pending"


def test_refresh_network_enabled_returns_persisted_state(monkeypatch):
    """Enabled refresh surfaces the persisted state including key/skip facts."""
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", True)
    paper_id = "p" * 32
    canned = EnrichmentJobResult(
        paper_id=paper_id,
        doi="10.1000/example",
        status="partial",
        providers=[
            ProviderResult(
                provider="crossref",
                status="fetched",
                http_status=200,
                fetched_at=T0,
                attempt_count=1,
                next_retry_at=None,
                error_code=None,
                error_message=None,
                fields=["title"],
            ),
            ProviderResult(
                provider="openalex",
                status="skipped",
                http_status=None,
                fetched_at=None,
                attempt_count=0,
                next_retry_at=None,
                error_code="missing_api_key",
                error_message="OpenAlex API key is not configured; provider skipped.",
                fields=[],
            ),
        ],
        resolved={},
    )
    monkeypatch.setattr(app_module, "refresh_enrichment", lambda pid: canned)
    monkeypatch.setattr(
        app_module,
        "collect_provider_results",
        lambda *a, **k: pytest.fail("collect must not run during a refresh"),
    )

    data = enrichment_refresh(paper_id)

    assert data["metadata_enrichment_status"] == "partial"
    by_name = {p["provider"]: p for p in data["providers"]}
    assert by_name["crossref"]["status"] == "fetched"
    assert by_name["crossref"]["fields"] == ["title"]
    assert by_name["openalex"]["error_code"] == "missing_api_key"


def test_refresh_paper_not_found_raises_404(monkeypatch):
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", True)
    canned = EnrichmentJobResult(
        paper_id="missing", doi=None, status="failed", error_code="paper_not_found"
    )
    monkeypatch.setattr(app_module, "refresh_enrichment", lambda pid: canned)

    with pytest.raises(HTTPException) as exc_info:
        enrichment_refresh("missing")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# AC-WEB-006: paper list performs zero enrichment fan-out
# ---------------------------------------------------------------------------


def test_papers_list_zero_enrichment_fan_out(monkeypatch):
    """One GET /api/papers call must never touch any enrichment path."""
    with SessionLocal() as session:
        paper = Paper(
            title="List Only Paper",
            normalized_title="list only paper",
            doi="10.1000/listonly",
            normalized_doi="10.1000/listonly",
            status="active",
        )
        session.add(paper)
        session.commit()
        paper_id = paper.id

    def _boom(*args, **kwargs):
        pytest.fail("papers() triggered an enrichment path")

    # Handler-level, service-level, and provider-client tripwires.
    monkeypatch.setattr(app_module, "collect_provider_results", _boom)
    monkeypatch.setattr(app_module, "refresh_enrichment", _boom)
    monkeypatch.setattr(doi_service, "collect_provider_results", _boom)
    monkeypatch.setattr(doi_service, "refresh_enrichment", _boom)
    monkeypatch.setattr(doi_service, "enrich_paper_by_doi", _boom)
    monkeypatch.setattr(workflow_service, "enrich_paper_by_doi", _boom)
    monkeypatch.setattr(workflow_service, "collect_provider_results", _boom)
    monkeypatch.setattr(ProviderClient, "fetch", _boom)

    rows = papers()

    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["paper_id"] == paper_id
    assert rows[0]["doi"] == "10.1000/listonly"
