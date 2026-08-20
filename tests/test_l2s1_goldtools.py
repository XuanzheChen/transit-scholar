"""Gold toolchain tests (task T-09, AC-GOLD-001/002/003).

Offline and deterministic: goldcheck's machine checks are exercised both as
pure functions (scale/language/structure/evidence/annotator) and through the
CLI with relaxed thresholds against a fake-parsed paper.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from transit_scholar.layer2.eval.gold import QUERY_TYPES, GoldQuery
from transit_scholar.layer2.eval.goldcheck import (
    RESERVED_ANNOTATORS,
    check_annotator,
    check_evidence,
    check_language,
    check_scale,
    check_structure,
    classify_language,
    run_goldcheck,
)
from tests.l2s1_fixtures import run_parse

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"

PAPER_IDS = [f"paper_{index:02d}" for index in range(10)]


def _synthetic_gold() -> list[GoldQuery]:
    """28 queries: 10 papers, 7 query types x 4, zh/en balanced (14/14)."""
    gold: list[GoldQuery] = []
    for type_index, query_type in enumerate(QUERY_TYPES):
        for variant in range(4):
            paper_id = PAPER_IDS[(type_index * 4 + variant) % 10]
            zh = variant % 2 == 0
            if zh:
                query = f"关于{query_type}的第{variant}个检索问题，用于跨语言验证。"
            else:
                query = f"English retrieval question about {query_type} variant {variant}."
            gold.append(
                GoldQuery(
                    paper_id=paper_id,
                    query=query,
                    query_type=query_type,
                    gold_block_ids=[f"blk_{type_index:04d}"],
                )
            )
    return gold


def test_t09_classify_language():
    assert classify_language("What is holding control?") == "en"
    assert classify_language("什么是驻站控制？") == "zh"
    assert classify_language("公交车队控制与强化学习") == "zh"
    assert classify_language("TD3") == "en"


def test_t09_check_scale_passes_on_synthetic_gold():
    gold = _synthetic_gold()
    assert len(gold) == 28
    errors = check_scale(gold)
    assert errors == []
    language_errors, summary = check_language(gold)
    assert language_errors == []
    assert summary["zh_count"] == 14 and summary["en_count"] == 14
    assert set(summary["zh_types"]) == set(QUERY_TYPES)
    assert set(summary["en_types"]) == set(QUERY_TYPES)


def test_t09_check_scale_detects_violations():
    gold = _synthetic_gold()
    # duplicate query -> evaluator key collision
    dup = [g for g in gold]
    dup[0] = GoldQuery(
        paper_id=dup[0].paper_id, query=dup[1].query,
        query_type=dup[1].query_type, gold_block_ids=["b1"],
    )
    errors = check_scale(dup)
    assert any("duplicate" in error for error in errors)
    # missing type
    missing_type = [g for g in gold if g.query_type != "exact_term"]
    errors = check_scale(missing_type)
    assert any("missing query types" in error for error in errors)
    # per-paper range violation
    crammed = [g for g in gold if g.paper_id != "paper_00"]
    crammed.extend(
        GoldQuery(paper_id="paper_00", query=f"extra query {index}",
                  query_type="exact_term", gold_block_ids=["b1"])
        for index in range(5)
    )
    errors = check_scale(crammed)
    assert any("paper 'paper_00' has" in error for error in errors)
    # too few papers / queries
    errors = check_scale([g for g in gold[:3]])
    assert any("paper_count=3 < 10" in error for error in errors)
    assert any("query_count=3 < 25" in error for error in errors)


def test_t09_check_language_balance_detection():
    gold = _synthetic_gold()
    # all English -> |zh - en| = 14 > 1 and en-only type coverage
    english_only = [
        GoldQuery(
            paper_id=g.paper_id,
            query=f"English {g.query_type} {index}",
            query_type=g.query_type,
            gold_block_ids=g.gold_block_ids,
        )
        for index, g in enumerate(gold)
    ]
    errors, _summary = check_language(english_only)
    assert any("|zh - en|" in error for error in errors)
    assert any("zh covers only 0" in error for error in errors)


def test_t09_check_structure_and_evidence():
    gold = _synthetic_gold()
    assert check_structure(gold) == []
    blocks_by_paper: dict[str, dict[str, str]] = {}
    for paper_id in PAPER_IDS:
        blocks_by_paper[paper_id] = {
            f"blk_{index:04d}": f"evidence text for type index {index}"
            for index in range(7)
        }
    assert check_evidence(gold, blocks_by_paper) == []

    broken = GoldQuery(
        paper_id="paper_00", query="x", query_type="exact_term",
        gold_block_ids=["does_not_exist"],
        gold_source_spans=[{"block_id": "blk_0000", "char_start": 5, "char_end": 400}],
    )
    structure_errors = check_structure([broken])
    evidence_errors = check_evidence([broken], blocks_by_paper)
    assert any("does not exist" in error for error in evidence_errors)
    assert any("out of range" in error for error in evidence_errors)


def test_t09_check_annotator_ownership(tmp_path):
    good = tmp_path / "gold_annotator.json"
    good.write_text(json.dumps({"annotator": "Planner(Codex)", "date": "2026-08-13"}), encoding="utf-8")
    errors, summary = check_annotator(good)
    assert errors == []
    assert summary["annotator"] == "Planner(Codex)"
    assert check_annotator(tmp_path / "missing.json")[0]
    bad = tmp_path / "bad.json"
    for reserved in RESERVED_ANNOTATORS:
        bad.write_text(json.dumps({"annotator": reserved}), encoding="utf-8")
        errors, _ = check_annotator(bad)
        assert any("reserved G/E tool identity" in error for error in errors)


def test_t09_goldtools_cli_roundtrip(project_tmp_path, monkeypatch, l2_config):
    """goldtools browse/search/export + goldcheck CLI wiring on a parsed
    paper (relaxed thresholds for the test corpus size)."""
    from transit_scholar.layer2.eval.fixtures import build_fixture_paper_items

    _, _, _, result = run_parse(
        project_tmp_path, build_fixture_paper_items(), monkeypatch=monkeypatch, page_count=2
    )
    paper_id = result.paper_id
    assert result.status == "passed"

    def _tool(*args, timeout=120):
        return subprocess.run(
            [str(VENV_PYTHON), "-m", "transit_scholar.layer2.eval.goldtools", *args],
            cwd=str(REPO_ROOT), env=dict(os.environ), capture_output=True, text=True, timeout=timeout,
        )

    browse = _tool("browse", "--data-root", str(project_tmp_path), paper_id, "--limit", "5")
    assert browse.returncode == 0, browse.stderr[-1000:]
    assert "blk_00001" in browse.stdout

    search = _tool("search", "--data-root", str(project_tmp_path), "reinforcement", "--paper", paper_id)
    assert search.returncode == 0, search.stderr[-1000:]
    assert "blk_" in search.stdout

    export = _tool(
        "export", "--data-root", str(project_tmp_path), paper_id,
        "--block-ids", "blk_00004", "--query", "公交车队控制与强化学习",
        "--type", "cross_language", "--language", "zh",
        "--span", "blk_00004:0:5", "--out", str(project_tmp_path / "scratch.jsonl"),
    )
    assert export.returncode == 0, export.stderr[-1000:]
    draft = json.loads(
        (project_tmp_path / "scratch.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert draft["paper_id"] == paper_id
    assert draft["query_type"] == "cross_language"
    assert draft["gold_block_ids"] == ["blk_00004"]
    assert draft["gold_source_spans"] == [
        {"block_id": "blk_00004", "char_start": 0, "char_end": 5}
    ]
    assert draft["_language_hint"] == "zh"

    # export out-of-range span must fail (span checked against real block text)
    bad_span = _tool(
        "export", "--data-root", str(project_tmp_path), paper_id,
        "--block-ids", "blk_00004", "--query", "q", "--type", "exact_term",
        "--span", "blk_00004:0:99999", "--out", str(project_tmp_path / "scratch2.jsonl"),
    )
    assert bad_span.returncode == 2

    # goldcheck CLI on a single-query gold with relaxed thresholds
    gold_file = project_tmp_path / "gold_small.json"
    annotator_file = project_tmp_path / "annotator.json"
    gold_file.write_text(
        json.dumps([draft], ensure_ascii=False), encoding="utf-8"
    )
    annotator_file.write_text(
        json.dumps({"annotator": "Planner(Codex)", "date": "2026-08-13"}), encoding="utf-8"
    )
    check = subprocess.run(
        [str(VENV_PYTHON), "-m", "transit_scholar.layer2.eval.goldcheck",
         "--gold", str(gold_file), "--data-root", str(project_tmp_path),
         "--annotator", str(annotator_file),
         "--min-papers", "1", "--min-queries", "1",
         "--min-per-paper", "1", "--max-per-paper", "5",
         "--min-types-per-language", "0", "--required-types", "0",
         "--out", str(project_tmp_path / "goldcheck_report.json")],
        cwd=str(REPO_ROOT), env=dict(os.environ), capture_output=True, text=True, timeout=120,
    )
    assert check.returncode == 0, check.stderr[-1000:]
    report = json.loads((project_tmp_path / "goldcheck_report.json").read_text(encoding="utf-8"))
    assert report["valid"] is True
    assert report["checks"]["evidence"]["ok"] is True
    assert report["annotator"]["annotator"] == "Planner(Codex)"

    # a gold pointing at a non-existent block fails with exit 3
    broken = [dict(draft)]
    broken[0]["gold_block_ids"] = ["no_such_block"]
    broken_file = project_tmp_path / "gold_broken.json"
    broken_file.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    broken_check = subprocess.run(
        [str(VENV_PYTHON), "-m", "transit_scholar.layer2.eval.goldcheck",
         "--gold", str(broken_file), "--data-root", str(project_tmp_path),
         "--annotator", str(annotator_file),
         "--min-papers", "1", "--min-queries", "1",
         "--min-per-paper", "1", "--max-per-paper", "5",
         "--min-types-per-language", "0"],
        cwd=str(REPO_ROOT), env=dict(os.environ), capture_output=True, text=True, timeout=120,
    )
    assert broken_check.returncode == 3


def test_t09_run_goldcheck_full_report(project_tmp_path):
    """run_goldcheck produces the full machine-readable report dict."""
    gold = _synthetic_gold()
    blocks_by_paper: dict[str, dict[str, str]] = {}
    for paper_id in PAPER_IDS:
        blocks_by_paper[paper_id] = {
            f"blk_{index:04d}": "text" for index in range(7)
        }
    annotator_path = project_tmp_path / "annotator.json"
    annotator_path.write_text(
        json.dumps({"annotator": "Planner(Codex)", "date": "2026-08-13"}),
        encoding="utf-8",
    )
    report = run_goldcheck(
        gold, blocks_by_paper=blocks_by_paper, annotator_path=annotator_path
    )
    assert report["valid"] is True
    assert report["query_count"] == 28
    assert report["paper_count"] == 10
    assert report["query_type_count"] == 7
    assert report["zh_count"] == 14
    assert report["en_count"] == 14
    assert set(report["checks"]) == {"structure", "evidence", "scale", "language", "annotator"}
