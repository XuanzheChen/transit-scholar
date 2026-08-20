"""Deterministic completion-report update entry tests (P revision 4).

The report generator must render acceptance-result sections from machine
artifacts only and leave the marker-delimited structure intact; missing facts
render an explicit "machine fact unavailable" placeholder, never fabricated
numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

from transit_scholar.layer2.benchmark.report import (
    SECTION_MARKERS,
    facts_from_dir,
    render_section,
    update_doc,
)

TEMPLATE_DOC = """# Layer2 Step1 完成情况说明

## 验收结果

### 自动化测试
<!-- report:automated_tests -->
PLACEHOLDER
<!-- /report:automated_tests -->

### 真实 Parser benchmark
<!-- report:parser_benchmark -->
PLACEHOLDER
<!-- /report:parser_benchmark -->

### Gold 规模
<!-- report:gold -->
PLACEHOLDER
<!-- /report:gold -->

### 四路检索指标
<!-- report:retrieval_metrics -->
PLACEHOLDER
<!-- /report:retrieval_metrics -->

### 安全
<!-- report:safety -->
PLACEHOLDER
<!-- /report:safety -->

### 结论
<!-- report:conclusion -->
PLACEHOLDER
<!-- /report:conclusion -->
"""


def _write_facts_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pytest_summary.json").write_text(
        json.dumps({"collected": 600, "passed": 598, "failed": 2, "log": "pytest.log"}),
        encoding="utf-8",
    )
    (root / "parser_benchmark_aggregate.json").write_text(
        json.dumps(
            {
                "unit_total": 48,
                "parsers": {
                    "docling": {"N": 16, "success": 15, "failed": 1, "timeout": 0,
                                "failure_rate": 0.0625, "mean_caption_relation_completeness": 0.9},
                    "mineru": {"N": 16, "success": 16, "failed": 0, "timeout": 0,
                               "failure_rate": 0.0, "mean_caption_relation_completeness": 0.7},
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "goldcheck_report.json").write_text(
        json.dumps(
            {
                "valid": True, "query_count": 28, "paper_count": 12,
                "zh_count": 14, "en_count": 14, "query_type_count": 7, "errors": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "eval_report.json").write_text(
        json.dumps(
            {
                "overall": {
                    "bm25": {"recall@5": 0.8, "recall@10": 0.85, "mrr@10": 0.7, "ndcg@10": 0.6},
                    "dense": {"recall@5": 0.7, "recall@10": 0.8, "mrr@10": 0.6, "ndcg@10": 0.5},
                    "hybrid": {"recall@5": 0.9, "recall@10": 0.9, "mrr@10": 0.75, "ndcg@10": 0.65},
                    "hybrid_rerank": {"recall@5": 0.92, "recall@10": 0.92, "mrr@10": 0.8, "ndcg@10": 0.7},
                },
                "by_language": {
                    "zh": {"hybrid_rerank": {"recall@10": 0.88}},
                    "en": {"hybrid_rerank": {"recall@10": 0.95}},
                },
                "rules": [{"rule": 1, "passed": True}, {"rule": 2, "passed": True}],
                "unfinished_queries": [{"query_id": "q1", "variant": "dense"}],
            }
        ),
        encoding="utf-8",
    )
    (root / "safety_scan.json").write_text(
        json.dumps({"files_scanned": 1234, "matched_count": 0, "clean": True}),
        encoding="utf-8",
    )
    return root


def test_report_facts_from_dir(tmp_path):
    facts_dir = _write_facts_dir(tmp_path / "facts")
    facts = facts_from_dir(facts_dir)
    assert facts["pytest_summary"]["passed"] == 598
    assert facts["parser_benchmark_aggregate"]["unit_total"] == 48
    assert facts["goldcheck"]["valid"] is True
    assert facts["eval"]["overall"]["hybrid_rerank"]["recall@10"] == 0.92
    assert facts["scan"]["clean"] is True


def test_report_sections_render_from_facts(tmp_path):
    facts_dir = _write_facts_dir(tmp_path / "facts")
    facts = facts_from_dir(facts_dir)
    doc = tmp_path / "doc.md"
    doc.write_text(TEMPLATE_DOC, encoding="utf-8")
    result = update_doc(doc, facts)
    assert all(result["sections"].values())
    text = result["doc"]

    assert "598" in text  # pytest passed count
    assert "docling" in text and "failure_rate=0.0625" in text
    assert "gold 条数：28" in text
    assert "0.92" in text  # hybrid_rerank recall@10
    assert "报告生成器不硬编码质量阈值" in text
    assert "matched=0" in text
    assert "最终冻结状态以完成说明中记录的用户决策为准" in text
    # markers preserved for the next update
    for open_marker, _close in SECTION_MARKERS.values():
        assert open_marker in text


def test_report_missing_facts_renders_waiting_placeholders(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(TEMPLATE_DOC, encoding="utf-8")
    result = update_doc(doc, {})
    for name, changed in result["sections"].items():
        assert changed is True
    text = result["doc"]
    assert "对应机器事实尚未提供" in text
    # no fabricated numbers
    assert "0.92" not in text
    assert "598" not in text


def test_report_update_is_idempotent(tmp_path):
    facts_dir = _write_facts_dir(tmp_path / "facts")
    facts = facts_from_dir(facts_dir)
    doc = tmp_path / "doc.md"
    doc.write_text(TEMPLATE_DOC, encoding="utf-8")
    first = update_doc(doc, facts)
    doc.write_text(first["doc"], encoding="utf-8")
    second = update_doc(doc, facts)
    assert not any(second["sections"].values())  # nothing changed on re-run
    assert second["doc"] == first["doc"]


def test_report_render_rule_and_target_verdict(tmp_path):
    facts_dir = _write_facts_dir(tmp_path / "facts")
    facts = facts_from_dir(facts_dir)
    rendered = render_section("retrieval_metrics", facts)
    assert "AC-GOLD-006 规则 1: True" in rendered
    assert "报告生成器不硬编码质量阈值" in rendered

    # The renderer records the metric without imposing an obsolete global target.
    low = json.loads(
        (facts_dir / "eval_report.json").read_text(encoding="utf-8")
    )
    low["overall"]["hybrid_rerank"]["recall@10"] = 0.8
    (facts_dir / "eval_report.json").write_text(json.dumps(low), encoding="utf-8")
    low_facts = facts_from_dir(facts_dir)
    rendered = render_section("retrieval_metrics", low_facts)
    assert "最终默认路线 Recall@10=0.8" in rendered
    assert "报告生成器不硬编码质量阈值" in rendered
