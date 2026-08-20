"""Layer2 Parser benchmark toolchain tests (task T-08, AC-PARSER-001..007).

All tests are offline and deterministic: the benchmark CLI is driven against
tiny generated PDFs with the registered ``fake`` parser through documented
test-only env seams (``L2S1_BENCH_*``), never against heavy parsers or real
PDFs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.l2s1_fixtures import make_pdf

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"

FAKE_ITEMS_JSON = json.dumps(
    [
        {
            "item_id": "h1", "item_type": "heading", "text": "Benchmark Section",
            "order": 0, "page": 1, "level": 1, "bbox": [70.0, 60.0, 530.0, 80.0],
            "font_size": 14.0,
        },
        {
            "item_id": "p1", "item_type": "paragraph",
            "text": "Benchmark paper body text for the parser runner tests.",
            "order": 1, "page": 1, "bbox": [70.0, 100.0, 530.0, 120.0],
            "font_size": 10.0,
        },
    ]
)


def _run_cli(*args: str, extra_env: dict[str, str] | None = None, timeout: float = 180) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(VENV_PYTHON), "-m", "transit_scholar.layer2.benchmark.parser_runner", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _make_corpus(project_tmp_path: Path, count: int = 2) -> Path:
    corpus = project_tmp_path / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        make_pdf(
            corpus / f"bench_paper_{index}.pdf",
            text=f"Benchmark corpus paper {index} body.",
            pages=1,
        )
    return corpus


def test_t08_cli_help_and_exit_codes(project_tmp_path):
    """AC-PARSER-003: --help works; missing corpus exits 2; unknown parser
    exits 4; invalid limit exits 4."""
    help_result = _run_cli("--help")
    assert help_result.returncode == 0
    for option in ("--corpus", "--output", "--parsers", "--limit", "--resume", "--per-paper-timeout"):
        assert option in help_result.stdout

    missing = _run_cli("--corpus", str(project_tmp_path / "nope"), "--output", str(project_tmp_path / "out"))
    assert missing.returncode == 2

    corpus = _make_corpus(project_tmp_path, count=1)
    unknown = _run_cli("--corpus", str(corpus), "--output", str(project_tmp_path / "out2"),
                       "--parsers", "does_not_exist")
    assert unknown.returncode == 4

    bad_limit = _run_cli("--corpus", str(corpus), "--output", str(project_tmp_path / "out3"),
                         "--parsers", "fake", "--limit", "0")
    assert bad_limit.returncode == 4


def test_t08_benchmark_run_manifest_aggregate_and_fields(project_tmp_path):
    """AC-PARSER-004/005: a fake-parser run produces per_paper.jsonl records
    that pass the field validator, aggregate.json, manifest.json and a
    corpus_sha256."""
    from transit_scholar.layer2.benchmark.quality import validate_unit_record

    corpus = _make_corpus(project_tmp_path, count=2)
    output = project_tmp_path / "bm_out"
    result = _run_cli(
        "--corpus", str(corpus), "--output", str(output),
        "--parsers", "fake", "--limit", "2",
        extra_env={"L2S1_BENCH_FAKE_ITEMS": FAKE_ITEMS_JSON, "L2S1_BENCH_FAKE_PAGE_COUNT": "1"},
    )
    assert result.returncode == 0, result.stderr[-2000:]

    per_paper = [
        json.loads(line)
        for line in (output / "per_paper.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(per_paper) == 2
    for record in per_paper:
        assert record["parser_name"] == "fake"
        assert record["status"] == "passed"
        assert validate_unit_record(record) == []

    aggregate = json.loads((output / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate["unit_total"] == 2
    fake_stats = aggregate["parsers"]["fake"]
    assert fake_stats["N"] == 2 and fake_stats["success"] == 2
    assert fake_stats["failure_rate"] == 0.0

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["corpus_pdf_count"] == 2
    assert manifest["corpus_sha256"]
    assert len(manifest["corpus_files"]) == 2
    assert all("sha256" in entry for entry in manifest["corpus_files"])
    assert manifest["command_args"]["per_paper_timeout"] == 900.0
    assert manifest["parsers"][0]["name"] == "fake"


def test_t08_resume_skips_completed_units(project_tmp_path):
    """AC-PARSER-005: --resume skips completed, unchanged units (state.json
    proof) and keeps their results identical."""
    corpus = _make_corpus(project_tmp_path, count=2)
    output = project_tmp_path / "bm_resume"
    env = {"L2S1_BENCH_FAKE_ITEMS": FAKE_ITEMS_JSON, "L2S1_BENCH_FAKE_PAGE_COUNT": "1"}

    first = _run_cli("--corpus", str(corpus), "--output", str(output),
                     "--parsers", "fake", "--limit", "1", extra_env=env)
    assert first.returncode == 0, first.stderr[-2000:]
    state_after_first = json.loads((output / "state.json").read_text(encoding="utf-8"))
    assert len(state_after_first) == 1
    first_units = [
        json.loads(line)
        for line in (output / "per_paper.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(first_units) == 1
    first_result_bytes = (output / "units" / first_units[0]["unit_dir_key"] / "result.json").read_bytes()

    second = _run_cli("--corpus", str(corpus), "--output", str(output),
                      "--parsers", "fake", "--limit", "4", "--resume", extra_env=env)
    assert second.returncode == 0, second.stderr[-2000:]
    state_after_second = json.loads((output / "state.json").read_text(encoding="utf-8"))
    assert len(state_after_second) == 2
    # the first unit was skipped, not re-run (run counter stays at 1)
    first_key = first_units[0]["unit_key"]
    assert state_after_second[first_key]["run"] == 1
    assert state_after_second[first_key].get("skipped") is True
    assert (output / "units" / first_units[0]["unit_dir_key"] / "result.json").read_bytes() == first_result_bytes

    per_paper = [
        json.loads(line)
        for line in (output / "per_paper.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(per_paper) == 2
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["resume_skipped"] == 1
    assert manifest["executed_count"] == 1


def test_t08_per_paper_timeout_recorded_and_isolated(project_tmp_path):
    """AC-PARSER-005: a hung unit is recorded as timeout, does not crash the
    batch, and the other units are still processed."""
    corpus = _make_corpus(project_tmp_path, count=2)
    output = project_tmp_path / "bm_timeout"
    result = _run_cli(
        "--corpus", str(corpus), "--output", str(output),
        "--parsers", "fake", "--limit", "2", "--per-paper-timeout", "1",
        extra_env={"L2S1_BENCH_SLEEP_SECONDS": "5"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    per_paper = [
        json.loads(line)
        for line in (output / "per_paper.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(per_paper) == 2
    assert all(record["status"] == "timeout" for record in per_paper)
    assert all(record["error_code"] == "PER_UNIT_TIMEOUT" for record in per_paper)
    aggregate = json.loads((output / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate["parsers"]["fake"]["timeout"] == 2
    assert aggregate["parsers"]["fake"]["failure_rate"] == 1.0


def test_t08_failure_records_satisfy_schema_with_null_quality(project_tmp_path):
    """error / timeout / dependency_missing records satisfy the stable unit
    record schema: unavailable quality metrics are null (never missing
    fields), and failures still enter the aggregate denominator."""
    from transit_scholar.layer2.benchmark.quality import (
        complete_failure_record,
        validate_unit_record,
    )

    corpus = _make_corpus(project_tmp_path, count=2)
    output = project_tmp_path / "bm_fail"

    # adapter-level structured error (fake seam) -> status error
    errored = _run_cli(
        "--corpus", str(corpus), "--output", str(output),
        "--parsers", "fake", "--limit", "1",
        extra_env={"L2S1_BENCH_FAIL": "error"},
    )
    assert errored.returncode == 0, errored.stderr[-2000:]

    # timeout -> status timeout
    timed = _run_cli(
        "--corpus", str(corpus), "--output", str(output / "t"),
        "--parsers", "fake", "--limit", "1", "--per-paper-timeout", "1",
        extra_env={"L2S1_BENCH_SLEEP_SECONDS": "5"},
    )
    assert timed.returncode == 0, timed.stderr[-2000:]

    per_paper = [
        json.loads(line)
        for line in (output / "per_paper.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    timed_records = [
        json.loads(line)
        for line in (output / "t" / "per_paper.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert per_paper[0]["status"] == "error"
    assert timed_records[0]["status"] == "timeout"

    for record in per_paper + timed_records:
        # no "missing required field" violations for legitimate failures
        assert validate_unit_record(record) == []
        for field in (
            "page_count", "section_count", "block_count", "type_counts",
            "meaningful_page_ratio", "replacement_char_ratio", "duplicate_ratio",
            "provenance_page_coverage", "bbox_coverage", "table_count",
            "figure_count", "equation_count", "caption_count",
            "caption_relation_completeness",
        ):
            assert field in record, f"failure record missing {field}"
            assert record[field] is None or record[field] == []
        assert "warnings" in record
        assert record["error_code"]

    aggregate = json.loads((output / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate["parsers"]["fake"]["failed"] == 1
    assert aggregate["parsers"]["fake"]["failure_rate"] == 1.0

    # complete_failure_record fills the quality nulls (identity fields come
    # from the unit payload) and is idempotent
    base = {
        "unit_key": "k", "unit_dir_key": "d",
        "pdf_name": "x.pdf", "pdf_sha256": "s",
        "parser_name": "fake", "parser_version": "1.0.0",
        "parser_config_hash": "h", "status": "error",
        "error_code": "E", "error_message": "m",
        "runtime_s": 1.0, "artifact_dir": "a",
    }
    completed = complete_failure_record(base)
    assert completed == base | {k: None for k in (
        "page_count", "section_count", "block_count", "type_counts",
        "meaningful_page_ratio", "replacement_char_ratio", "duplicate_ratio",
        "provenance_page_coverage", "bbox_coverage", "table_count",
        "figure_count", "equation_count", "caption_count",
        "caption_relation_completeness",
    )} | {"warnings": []}
    assert validate_unit_record(completed) == []
    again = complete_failure_record(completed)
    assert again == completed


def test_t08_review_generate_and_annotation_validation(project_tmp_path):
    """AC-PARSER-007: review materials are generated into isolated dirs and
    annotation validation rejects reserved tool annotators."""
    corpus = _make_corpus(project_tmp_path, count=1)
    output = project_tmp_path / "bm_review"
    run = _run_cli("--corpus", str(corpus), "--output", str(output),
                   "--parsers", "fake", "--limit", "1",
                   extra_env={"L2S1_BENCH_FAKE_ITEMS": FAKE_ITEMS_JSON, "L2S1_BENCH_FAKE_PAGE_COUNT": "1"})
    assert run.returncode == 0

    def _review(*args, timeout=120):
        return subprocess.run(
            [str(VENV_PYTHON), "-m", "transit_scholar.layer2.benchmark.review", *args],
            cwd=str(REPO_ROOT), env=dict(os.environ), capture_output=True, text=True, timeout=timeout,
        )

    generated = _review("generate", "--root", str(output))
    assert generated.returncode == 0, generated.stderr[-2000:]
    review_dir = output / "review"
    assert (review_dir / "bench_paper_0.md").is_file()
    assert (review_dir / "diffs" / "bench_paper_0.json").is_file()
    diff = json.loads((review_dir / "diffs" / "bench_paper_0.json").read_text(encoding="utf-8"))
    assert "fake" in diff["per_parser_stats"]
    assert "machine-generated" in diff["note"].lower()

    # annotations with a reserved tool annotator must fail validation
    annotations = review_dir / "annotations.jsonl"
    annotations.write_text(
        json.dumps(
            {"annotator": "opencode", "paper": "bench_paper_0.pdf", "parser": "fake",
             "item": "reading_order", "score": 4, "notes": "", "date": "2026-08-13"}
        )
        + "\n",
        encoding="utf-8",
    )
    invalid = _review("validate", "--root", str(output))
    assert invalid.returncode == 3

    # a human annotator passes
    annotations.write_text(
        json.dumps(
            {"annotator": "Planner(Codex)", "paper": "bench_paper_0.pdf", "parser": "fake",
             "item": "reading_order", "score": 4, "notes": "ok", "date": "2026-08-13"}
        )
        + "\n",
        encoding="utf-8",
    )
    valid = _review("validate", "--root", str(output))
    assert valid.returncode == 0, valid.stderr[-2000:]


def test_t08_corpus_pipeline_subprocess_with_fake_override(project_tmp_path):
    """AC-PARSER-001/FR-GOLD groundwork: the production-chain corpus pipeline
    parses each PDF through parse_paper in an isolated data root and emits a
    corpus_manifest.json mapping pdf_sha256 -> paper_id -> parse_run_id."""
    corpus = _make_corpus(project_tmp_path, count=2)
    output = project_tmp_path / "cp_out"
    env = dict(os.environ)
    env.update(
        {
            "L2S1_CORPUS_PARSER_OVERRIDE": "fake",
            "L2S1_CORPUS_FAKE_ITEMS": FAKE_ITEMS_JSON,
            "L2S1_CORPUS_FAKE_PAGE_COUNT": "1",
        }
    )
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "transit_scholar.layer2.benchmark.corpus_pipeline",
         "--corpus", str(corpus), "--output", str(output)],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, result.stderr[-3000:]

    corpus_manifest = json.loads(
        (output / "corpus_manifest.json").read_text(encoding="utf-8")
    )
    assert corpus_manifest["corpus_pdf_count"] == 2
    papers = corpus_manifest["papers"]
    assert len(papers) == 2
    for paper in papers:
        assert paper["status"] == "passed"
        assert paper["parser_used"] == "fake"
        assert paper["paper_id"]
        assert paper["parse_run_id"]
        assert paper["pdf_sha256"]
    # isolated data root was created inside the output root
    assert (output / "data" / "layer2" / "parsed").is_dir()

    # resume skips completed papers
    again = subprocess.run(
        [str(VENV_PYTHON), "-m", "transit_scholar.layer2.benchmark.corpus_pipeline",
         "--corpus", str(corpus), "--output", str(output), "--resume"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=240,
    )
    assert again.returncode == 0
    state = json.loads((output / "state.json").read_text(encoding="utf-8"))
    assert all(entry["run"] == 1 and entry.get("skipped") is True for entry in state.values())


def test_t08_corpus_pipeline_missing_corpus_exit_code(project_tmp_path):
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "transit_scholar.layer2.benchmark.corpus_pipeline",
         "--corpus", str(project_tmp_path / "nope"), "--output", str(project_tmp_path / "out")],
        cwd=str(REPO_ROOT), env=dict(os.environ), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 2


def test_t08_scan_key_leakage(project_tmp_path, monkeypatch):
    """NFR-002 / AC-JINA-001: the scan CLI finds actual key values in output
    dirs (exit 3) and reports clean dirs as exit 0."""
    secret = "jina-super-secret-value-for-scan-test"
    monkeypatch.setenv("JINA_API_KEY", secret)
    dirty = project_tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "report.json").write_text(f'{{"log": "key={secret}"}}', encoding="utf-8")
    clean = project_tmp_path / "clean"
    clean.mkdir()
    (clean / "report.json").write_text('{"log": "no secret here"}', encoding="utf-8")

    def _scan(root, key_env="JINA_API_KEY"):
        return subprocess.run(
            [str(VENV_PYTHON), "-m", "transit_scholar.layer2.benchmark.scan",
             str(root), "--key-env", key_env],
            cwd=str(REPO_ROOT), env=dict(os.environ), capture_output=True, text=True, timeout=120,
        )

    dirty_result = _scan(dirty)
    assert dirty_result.returncode == 3
    clean_result = _scan(clean)
    assert clean_result.returncode == 0
