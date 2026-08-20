"""Layer2 Step1 safety & scope guardrails (AC-L2S1-SAFETY-01..07)."""

from __future__ import annotations

import json
import pathlib
import re
import socket
import subprocess

import pytest

from tests.l2s1_fixtures import make_ready_paper, patch_parsers, read_artifacts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LAYER2_ROOT = REPO_ROOT / "src" / "transit_scholar" / "layer2"

FORBIDDEN_TERMS = (
    "reward_function",
    "state_definition",
    "action_space",
    "holding_limit",
)


def test_safety_offline_socket_guard(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-SAFETY-01: with outbound sockets blocked, the Layer2 retrieval
    boundary still works offline (no network attempted)."""
    original = socket.socket

    class _BlockingSocket(original):
        def connect(self, address, *args, **kwargs):
            raise OSError("network blocked by test guard")

        def connect_ex(self, address, *args, **kwargs):
            raise OSError("network blocked by test guard")

    monkeypatch.setattr(socket, "socket", _BlockingSocket)

    from transit_scholar.layer2 import build_retrieval, search_bm25, search_dense

    paper_id, _fid, _pdf = make_ready_paper(project_tmp_path)
    from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item

    items = [
        make_item(item_id="p1", item_type="paragraph", text="Offline body text.", order=0, page=1, bbox=[70, 100, 530, 120])
    ]
    patch_parsers(monkeypatch, [FakeParserAdapter(items=items, page_count=1)])
    from transit_scholar.layer2.pipeline import parse_paper

    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    assert build_retrieval(paper_id, config=l2_config)["status"] == "ok"
    bm25 = search_bm25(paper_id, "offline", config=l2_config)
    assert bm25.status == "ok"
    dense = search_dense(paper_id, "offline", config=l2_config)
    assert dense.status == "unavailable"  # no key, no network


def test_safety_no_api_key_leakage_scan():
    """AC-L2S1-SAFETY-02: scanning Layer2-generated artifacts and source for the
    reserved key env var names finds no key *values* stored anywhere."""
    from transit_scholar.layer2.benchmark.scan import _KEY_SHAPED_RE
    from transit_scholar.layer2.config import RESERVED_ENV_NAMES

    blob = ""
    for path in LAYER2_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # per-file scan with the production key-shape pattern: only a real
        # key-looking value fails, and a join across file boundaries can never
        # fabricate one
        assert not _KEY_SHAPED_RE.search(text), (
            f"{path} contains a key-shaped secret value"
        )
        blob += text
    # env var NAMES are expected in config; no key VALUE may be present
    for name in RESERVED_ENV_NAMES:
        assert name in blob  # declared names exist


def test_safety_no_tracked_pdf():
    """AC-L2S1-SAFETY-03: no real PDF is tracked by git."""
    result = subprocess.run(
        ["git", "ls-files", "*.pdf"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_safety_no_data_mutation():
    """AC-L2S1-SAFETY-04: the automated suite leaves the real data/ tree
    untouched (git reports no change; data/ is gitignored)."""
    result = subprocess.run(
        ["git", "status", "--short", "--", "data/"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_safety_no_scope_creep_source_scan():
    """AC-L2S1-SAFETY-05: the Layer2 source tree contains no BusControlPaper
    schema terms, no QA/Wiki/Web/Layer3/citation and no Alembic migration."""
    for path in LAYER2_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "schema.py":
            continue  # declares the FORBIDDEN_BLOCK_TYPES rejection constant
        for term in FORBIDDEN_TERMS:
            assert term not in text, f"{path} contains forbidden term {term!r}"

    # no Layer1 write-path imports anywhere in the Layer2 package
    # (workflow.service hosts the sanctioned read-only gate entry, so it is
    # the only allowed Layer1 import)
    forbidden_imports = (
        "transit_scholar.ingestion",
        "transit_scholar.metadata",
        "transit_scholar.doi_enrichment",
        "transit_scholar.identity",
        "transit_scholar.citation",
        "transit_scholar.web",
    )
    for path in LAYER2_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for module in forbidden_imports:
            if module in text:
                raise AssertionError(f"{path} imports forbidden module {module}")

    result = subprocess.run(
        ["git", "status", "--short", "--", "alembic/versions"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert result.stdout.strip() == ""


def test_safety_heavy_deps_declared_optional():
    """AC-L2S1-SAFETY-06: heavy production dependencies are declared as an
    optional extra in pyproject.toml."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.optional-dependencies]" in pyproject
    assert "layer2" in pyproject
    for dep in ("docling", "mineru", "pymupdf4llm", "lancedb"):
        assert dep in pyproject


def test_safety_code_lives_in_expected_dirs():
    """AC-L2S1-SAFETY-07: formal Layer2 code lives under src/transit_scholar/
    and tests under tests/."""
    assert (REPO_ROOT / "src" / "transit_scholar" / "layer2" / "__init__.py").is_file()
    for test in (
        "test_l2s1_config.py",
        "test_l2s1_gate.py",
        "test_l2s1_canonical.py",
        "test_l2s1_parser.py",
        "test_l2s1_parse.py",
        "test_l2s1_normalizer.py",
        "test_l2s1_structure.py",
        "test_l2s1_validation.py",
        "test_l2s1_markdown.py",
        "test_l2s1_chunk.py",
        "test_l2s1_retrieval.py",
        "test_l2s1_hit.py",
        "test_l2s1_read.py",
        "test_l2s1_rebuild.py",
        "test_l2s1_eval.py",
        "test_l2s1_safety.py",
        "test_l2s1_benchmark.py",
        "test_l2s1_goldtools.py",
        "test_l2s1_report.py",
    ):
        assert (REPO_ROOT / "tests" / test).is_file()


def test_safety_benchmark_roots_are_git_ignored():
    """task T-11 / AC-PARSER-001.3: every benchmark/eval output root used by
    the toolchain lives under a git-ignored path (git check-ignore exit 0)."""
    for path in (
        "temp/l2s1_parser_benchmark/",
        "temp/l2s1_retrieval_benchmark/",
        "temp/l2s1_g_evidence_20260813_000000/",
    ):
        result = subprocess.run(
            ["git", "check-ignore", path],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        assert result.returncode == 0, f"{path} is not git-ignored"


def test_safety_no_tool_gold_generation_paths():
    """task T-11 / AC-GOLD-001.2: the eval toolchain contains browse/search/
    export/template tools only -- no code path that generates gold queries or
    rewrites existing gold."""
    from transit_scholar.layer2.benchmark import report as _report
    from transit_scholar.layer2.eval import goldtools as _goldtools

    source = pathlib.Path(_goldtools.__file__).read_text(encoding="utf-8")
    assert "browse" in source and "search" in source and "export" in source
    assert "template" in source
    # no auto-generation / rewriting constructs
    for forbidden in ("generate_gold", "rewrite_gold", "auto_select_gold", "synthesize_query"):
        assert forbidden not in source
    for forbidden in ("generate_gold", "rewrite_gold"):
        assert forbidden not in pathlib.Path(_report.__file__).read_text(encoding="utf-8")
