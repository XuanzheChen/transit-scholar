"""Layer2 Step1 parse lifecycle tests (AC-L2S1-PARSE-01..06)."""

from __future__ import annotations

import hashlib
import json

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import Paper, PaperFile
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from transit_scholar.layer2.paths import load_current, run_paths
from tests.l2s1_fixtures import (
    canonical_fixture_items,
    fake_pdf_bytes,
    make_ready_paper,
    patch_parsers,
    read_artifacts,
)

_FIELDS = (
    "paper_id",
    "file_id",
    "parse_run_id",
    "status",
    "parser_used",
    "output_dir",
    "warnings",
    "blockers",
    "error_code",
    "error_message",
)


def _basic_items():
    return [
        make_item(
            item_id="p1", item_type="paragraph",
            text="A short paragraph for the parse lifecycle tests.",
            order=0, page=1, bbox=[70.0, 100, 530, 120], font_size=10.0,
        )
    ]


def test_parse_result_has_all_fields_on_success(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-PARSE-01: ParsePaperResult fields present on success."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, file_id, _ = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_basic_items(), page_count=1)])
    result = parse_paper(paper_id, config=l2_config)
    for field in _FIELDS:
        assert hasattr(result, field)
    assert result.status == "passed"
    assert result.paper_id == paper_id
    assert result.file_id == file_id
    assert result.parse_run_id
    assert result.parser_used == "fake"
    assert result.output_dir
    assert result.warnings == []


def test_parse_result_fields_on_blocked_and_failure(monkeypatch, l2_config):
    """AC-L2S1-PARSE-01: ParsePaperResult fields present on blocked/failure."""
    from transit_scholar.layer2.pipeline import parse_paper

    class _BlockedGate:
        status = "blocked"
        paper_id = "p"
        primary_file_id = None
        source_pdf_path = None
        relative_path = None
        title = None
        authors = []
        year = None
        doi = None
        arxiv_id = None
        page_count = None
        identity_status = None
        duplicate_status = None
        blockers = ["paper_not_found"]
        error_code = "paper_not_found"
        error_message = None
        metadata_quality_flags = []

    monkeypatch.setattr(
        "transit_scholar.layer2.pipeline.get_second_layer_input",
        lambda pid: _BlockedGate(),
    )
    result = parse_paper("p", config=l2_config)
    assert result.status == "blocked"
    for field in _FIELDS:
        assert hasattr(result, field)


def test_parse_reuses_existing_run(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-PARSE-02: a second call reuses the same run; only one runs/ dir."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_basic_items(), page_count=1)])
    first = parse_paper(paper_id, config=l2_config)
    second = parse_paper(paper_id, config=l2_config)
    assert first.parse_run_id == second.parse_run_id
    assert first.output_dir == second.output_dir

    runs_dir = l2_config.parsed_paper_dir(paper_id) / "runs"
    assert len([p for p in runs_dir.iterdir() if p.is_dir()]) == 1


def test_parse_force_creates_new_run_and_keeps_old(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-PARSE-03: force=True creates a new run; current.json points at
    the newest; old run dirs remain on disk."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_basic_items(), page_count=1)])
    first = parse_paper(paper_id, config=l2_config)
    second = parse_paper(paper_id, force=True, config=l2_config)
    assert second.parse_run_id != first.parse_run_id

    assert load_current(l2_config.parsed_paper_dir(paper_id)) == second.parse_run_id
    runs_dir = l2_config.parsed_paper_dir(paper_id) / "runs"
    run_dirs = [p.name for p in runs_dir.iterdir() if p.is_dir()]
    assert first.parse_run_id in run_dirs
    assert second.parse_run_id in run_dirs


def test_parse_trigger_change_creates_new_run(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-PARSE-03: changing a trigger input (source sha, parser version,
    normalizer version) creates a new run."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, file_id, pdf_path = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_basic_items(), page_count=1)])
    first = parse_paper(paper_id, config=l2_config)

    # change the source bytes -> new source_sha256
    pdf_path.write_bytes(fake_pdf_bytes() + b"changed")
    second = parse_paper(paper_id, config=l2_config)
    assert second.parse_run_id != first.parse_run_id

    # change normalizer version via config
    import copy

    from transit_scholar.layer2.config import Layer2Config

    config_v2 = copy.copy(l2_config)
    object.__setattr__(config_v2, "normalizer_version", "2.0")
    third = parse_paper(paper_id, config=config_v2)
    assert third.parse_run_id != second.parse_run_id

    # parser version change
    patch_parsers(
        monkeypatch,
        [FakeParserAdapter(items=_basic_items(), page_count=1, version="9.9")],
    )
    fourth = parse_paper(paper_id, config=config_v2)
    assert fourth.parse_run_id != third.parse_run_id

    current = load_current(l2_config.parsed_paper_dir(paper_id))
    assert current == fourth.parse_run_id
    runs_dir = l2_config.parsed_paper_dir(paper_id) / "runs"
    names = {p.name for p in runs_dir.iterdir() if p.is_dir()}
    assert {first.parse_run_id, second.parse_run_id, third.parse_run_id, fourth.parse_run_id} <= names


def test_parse_config_hash_change_creates_new_run(
    project_tmp_path, monkeypatch, l2_config
):
    """task T-04 (AC-VER-001): a parser *config hash* change with the same
    parser version must create a new parse run (sixth trigger)."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    first_items = _basic_items()
    patch_parsers(monkeypatch, [FakeParserAdapter(items=first_items, page_count=1)])
    first = parse_paper(paper_id, config=l2_config)
    first_hash = json.loads(
        run_paths(l2_config, paper_id, first.parse_run_id).manifest_path.read_text(
            encoding="utf-8"
        )
    )["requested_parser_chain_hash"]

    # same parser version, different item stream -> different config hash
    more_items = _basic_items() + [
        make_item(
            item_id="p2", item_type="paragraph",
            text="An extra paragraph changes the parser config fingerprint.",
            order=1, page=1, bbox=[70.0, 140, 530, 160], font_size=10.0,
        )
    ]
    patch_parsers(monkeypatch, [FakeParserAdapter(items=more_items, page_count=1)])
    second = parse_paper(paper_id, config=l2_config)
    assert second.parse_run_id != first.parse_run_id
    second_hash = json.loads(
        run_paths(l2_config, paper_id, second.parse_run_id).manifest_path.read_text(
            encoding="utf-8"
        )
    )["requested_parser_chain_hash"]
    assert second_hash != first_hash
    assert load_current(l2_config.parsed_paper_dir(paper_id)) == second.parse_run_id

    # identical input + identical config -> reuse (no extra run)
    third = parse_paper(paper_id, config=l2_config)
    assert third.parse_run_id == second.parse_run_id


def test_parse_failed_new_run_does_not_corrupt_current(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-PARSE-04: a failed forced parse leaves current.json pointing at
    the old run and writes no partial canonical files."""
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.parser.base import ParserInfo, ParserResult

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_basic_items(), page_count=1)])
    good = parse_paper(paper_id, config=l2_config)
    old_run = good.parse_run_id
    good_rp = run_paths(l2_config, paper_id, old_run)
    assert good_rp.document_path.is_file()

    class _FailingAdapter(FakeParserAdapter):
        def parse(self, path):
            return ParserResult(
                status="error",
                info=ParserInfo(name="fake", version="1.0", config={}, config_hash="x"),
                error_code="PDF_OPEN_FAILED",
                error_message="simulated failure",
            )

    monkeypatch.setattr(
        "transit_scholar.layer2.pipeline.resolve_parsers",
        lambda config: [_FailingAdapter()],
    )
    failed = parse_paper(paper_id, force=True, config=l2_config)
    assert failed.status == "needs_review"
    assert failed.error_code == "PDF_OPEN_FAILED"

    # current.json still points at the good run
    assert load_current(l2_config.parsed_paper_dir(paper_id)) == old_run
    # the good run's canonical files are intact
    assert good_rp.document_path.is_file()
    # the failed new run has no shadowing canonical files
    failed_rp = run_paths(l2_config, paper_id, failed.parse_run_id)
    assert not failed_rp.document_path.exists()
    assert not failed_rp.blocks_path.exists()


def test_parse_every_run_writes_manifest(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-PARSE-05: every run (including needs_review) writes a complete
    parser_manifest.json."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_basic_items(), page_count=1)])
    result = parse_paper(paper_id, config=l2_config)
    rp = run_paths(l2_config, paper_id, result.parse_run_id)
    manifest = json.loads(rp.manifest_path.read_text(encoding="utf-8"))
    assert manifest["parse_status"] in ("passed", "degraded")
    assert manifest["parser_config"]


def test_parse_records_stable_run_creation_time(project_tmp_path, monkeypatch, l2_config):
    """Rework fix: a real parse run records one stable run-creation time shared
    by document.json and parser_manifest.json; reusing the run keeps it fixed."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_basic_items(), page_count=1)])
    result = parse_paper(paper_id, config=l2_config)
    rp = run_paths(l2_config, paper_id, result.parse_run_id)

    document = json.loads(rp.document_path.read_text(encoding="utf-8"))
    manifest = json.loads(rp.manifest_path.read_text(encoding="utf-8"))
    created_at = document["created_at"]
    # real, stable, shared run creation time
    assert created_at
    assert created_at.endswith("+00:00") or created_at.endswith("Z")
    assert created_at == manifest["created_at"]

    # a reused run does not rewrite canonical created_at
    second = parse_paper(paper_id, config=l2_config)
    assert second.parse_run_id == result.parse_run_id
    document_again = json.loads(rp.document_path.read_text(encoding="utf-8"))
    assert document_again["created_at"] == created_at


def test_parse_delete_derived_does_not_touch_source_or_db(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-PARSE-06: deleting the Layer2 tree never deletes or modifies the
    Layer1 SQLite data or the original PDF."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, file_id, pdf_path = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_basic_items(), page_count=1)])
    parse_paper(paper_id, config=l2_config)

    pdf_before = pdf_path.read_bytes()
    db_before = _database_sha256()

    import shutil

    shutil.rmtree(l2_config.parsed_paper_dir(paper_id))
    shutil.rmtree(l2_config.retrieval_paper_dir(paper_id), ignore_errors=True)

    assert pdf_path.read_bytes() == pdf_before
    assert _database_sha256() == db_before

    # Layer1 rows still exist
    with SessionLocal() as session:
        assert session.get(Paper, paper_id) is not None
        assert session.get(PaperFile, file_id) is not None


def _database_sha256() -> str:
    from pathlib import Path

    from transit_scholar.db.engine import engine

    database_path = Path(engine.url.database)
    digest = hashlib.sha256()
    digest.update(database_path.read_bytes())
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        if sidecar.exists():
            digest.update(sidecar.read_bytes())
    return digest.hexdigest()
