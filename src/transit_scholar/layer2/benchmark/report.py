"""Deterministic completion-report update entry (P revision 4).

The completion note ``doc/20260813-Layer2Step1PDF解析与检索基础设施完成情况说明.md``
contains marker-delimited acceptance-result sections::

    <!-- report:automated_tests -->
    ...placeholder...
    <!-- /report:automated_tests -->

This module renders each section from machine artifacts only (pytest summary,
parser benchmark aggregate, human annotations, goldcheck report, retrieval
evaluation report, key scan). Missing artifacts render an explicit
"machine fact unavailable" placeholder; nothing is fabricated, and no model
round is needed to update the document after real results exist.

Command::

    python -m transit_scholar.layer2.benchmark.report
      --doc doc/20260813-...完成情况说明.md
      --facts-dir <dir>            # assemble facts from known artifact names
      [--facts <facts.json>]       # or pass facts directly
      [--out <path>]               # write to another file instead of in place

Exit codes: 0 = updated, 2 = usage/input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2

#: Marker names -> (open marker, close marker).
SECTION_MARKERS: dict[str, tuple[str, str]] = {
    "automated_tests": (
        "<!-- report:automated_tests -->",
        "<!-- /report:automated_tests -->",
    ),
    "parser_benchmark": (
        "<!-- report:parser_benchmark -->",
        "<!-- /report:parser_benchmark -->",
    ),
    "gold": ("<!-- report:gold -->", "<!-- /report:gold -->"),
    "retrieval_metrics": (
        "<!-- report:retrieval_metrics -->",
        "<!-- /report:retrieval_metrics -->",
    ),
    "safety": ("<!-- report:safety -->", "<!-- /report:safety -->"),
    "conclusion": ("<!-- report:conclusion -->", "<!-- /report:conclusion -->"),
}

WAITING = "对应机器事实尚未提供；本节不生成推测性结论。"

#: Known artifact files under a facts directory.
FACTS_FILES = {
    "pytest_summary": "pytest_summary.json",
    "parser_benchmark_aggregate": "parser_benchmark_aggregate.json",
    "parser_benchmark_manifest": "parser_benchmark_manifest.json",
    "annotations": "annotations.jsonl",
    "annotation_validation": "annotation_validation.json",
    "goldcheck": "goldcheck_report.json",
    "eval": "eval_report.json",
    "scan": "safety_scan.json",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.benchmark.report",
        description=(
            "Update the marker-delimited acceptance-result sections of the "
            "Layer2 Step1 completion note from machine artifacts."
        ),
    )
    parser.add_argument("--doc", required=True, help="path to the completion note markdown")
    parser.add_argument("--facts-dir", default=None, help="directory holding known artifact files")
    parser.add_argument("--facts", default=None, help="path to a facts JSON file")
    parser.add_argument("--out", default=None, help="write updated doc here instead of in place")
    parser.add_argument(
        "--section",
        default=None,
        help="only update this section (default: all)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    doc_path = Path(args.doc)
    if not doc_path.is_file():
        print(f"doc missing: {doc_path}", file=sys.stderr)
        return EXIT_USAGE
    facts: dict[str, Any] = {}
    if args.facts:
        facts_path = Path(args.facts)
        if not facts_path.is_file():
            print(f"facts file missing: {facts_path}", file=sys.stderr)
            return EXIT_USAGE
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
    if args.facts_dir:
        facts = _merge_facts(facts, facts_from_dir(Path(args.facts_dir)))

    sections = args.section.split(",") if args.section else None
    result = update_doc(doc_path, facts, sections=sections)
    out_path = Path(args.out) if args.out else doc_path
    out_path.write_text(result["doc"], encoding="utf-8")
    for name, changed in result["sections"].items():
        print(
            f"section {name}: {'updated' if changed else 'unchanged (already current)'}",
            file=sys.stderr,
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Facts assembly
# ---------------------------------------------------------------------------


def facts_from_dir(directory: Path) -> dict[str, Any]:
    """Assemble facts from known artifact file names under ``directory``."""
    facts: dict[str, Any] = {}
    for key, filename in FACTS_FILES.items():
        path = directory / filename
        if not path.is_file():
            continue
        if filename.endswith(".jsonl"):
            records: list[dict[str, Any]] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
            facts[key] = {"path": str(path), "records": records}
        elif filename.endswith(".json"):
            facts[key] = json.loads(path.read_text(encoding="utf-8"))
    return facts


def _merge_facts(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(extra)
    return merged


# ---------------------------------------------------------------------------
# Section rendering (facts -> markdown)
# ---------------------------------------------------------------------------


def render_section(name: str, facts: dict[str, Any]) -> str:
    renderer = {
        "automated_tests": _render_automated_tests,
        "parser_benchmark": _render_parser_benchmark,
        "gold": _render_gold,
        "retrieval_metrics": _render_retrieval_metrics,
        "safety": _render_safety,
        "conclusion": _render_conclusion,
    }[name]
    return renderer(facts)


def _render_automated_tests(facts: dict[str, Any]) -> str:
    summary = facts.get("pytest_summary")
    if not isinstance(summary, dict):
        return WAITING
    lines = [
        f"- pytest 结果：collected={summary.get('collected', '?')}，"
        f"passed={summary.get('passed', '?')}，failed={summary.get('failed', '?')}"
    ]
    if summary.get("log"):
        lines.append(f"- 全量测试日志：`{summary['log']}`")
    return "\n".join(lines) + "\n"


def _render_parser_benchmark(facts: dict[str, Any]) -> str:
    aggregate = facts.get("parser_benchmark_aggregate")
    manifest = facts.get("parser_benchmark_manifest")
    if not isinstance(aggregate, dict):
        return WAITING
    lines = [
        f"- 语料篇数：{manifest.get('corpus_pdf_count', '?') if isinstance(manifest, dict) else '?'}"
    ]
    for parser_name, stats in sorted(aggregate.get("parsers", {}).items()):
        lines.append(
            f"- {parser_name}：N={stats.get('N')}，success={stats.get('success')}，"
            f"failed={stats.get('failed')}，timeout={stats.get('timeout')}，"
            f"failure_rate={stats.get('failure_rate')}，"
            f"mean_caption_relation_completeness={stats.get('mean_caption_relation_completeness')}"
        )
    annotation_validation = facts.get("annotation_validation")
    if isinstance(annotation_validation, dict):
        lines.append(
            f"- 人工标注校验：{annotation_validation.get('annotations_count')} 条，"
            f"valid={annotation_validation.get('valid')}"
        )
    lines.append(
        "- Docling 已由用户确认为 V1 production primary；三解析器结果仅作历史工程记录，"
        "不再作为冻结门槛。"
    )
    return "\n".join(lines) + "\n"


def _render_gold(facts: dict[str, Any]) -> str:
    report = facts.get("goldcheck")
    if not isinstance(report, dict):
        return WAITING
    lines = [
        f"- gold 条数：{report.get('query_count', '?')}，"
        f"论文数：{report.get('paper_count', '?')}，"
        f"中文/英文：{report.get('zh_count', '?')}/{report.get('en_count', '?')}，"
        f"覆盖 query_type 数：{report.get('query_type_count', '?')}"
    ]
    lines.append(f"- goldcheck 校验：valid={report.get('valid')}，错误数={len(report.get('errors', []) or [])}")
    lines.append("- gold 标注者：P（Codex）人工标定；G 未生成或改写任何 gold。")
    return "\n".join(lines) + "\n"


def _render_retrieval_metrics(facts: dict[str, Any]) -> str:
    report = facts.get("eval")
    if not isinstance(report, dict):
        return WAITING
    lines: list[str] = []
    overall = report.get("overall", {})
    if overall:
        lines.append("| variant | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |")
        lines.append("|---|---|---|---|---|")
        for variant in ("bm25", "dense", "hybrid", "hybrid_rerank"):
            metrics = overall.get(variant) or {}
            lines.append(
                f"| {variant} | {metrics.get('recall@5')} | {metrics.get('recall@10')} | "
                f"{metrics.get('mrr@10')} | {metrics.get('ndcg@10')} |"
            )
    by_language = report.get("by_language", {})
    if by_language:
        for language, variants in sorted(by_language.items()):
            for variant in ("bm25", "dense", "hybrid", "hybrid_rerank"):
                metrics = variants.get(variant) or {}
                lines.append(
                    f"- {language}/{variant}: Recall@10={metrics.get('recall@10')} "
                    f"MRR@10={metrics.get('mrr@10')}"
                )
    rules = report.get("rules", [])
    for rule in rules:
        lines.append(f"- AC-GOLD-006 规则 {rule.get('rule')}: {rule.get('passed')}")
    unfinished = report.get("unfinished_queries", [])
    if unfinished:
        lines.append(f"- 未完成 query 数：{len(unfinished)}（unavailable 如实记录，不删除）")
    target = _hybrid_rerank_recall10(report)
    if target is not None:
        lines.append(
            f"- 最终默认路线 Recall@10={target}；是否通过以用户批准的当前验收口径为准，"
            "报告生成器不硬编码质量阈值。"
        )
    return "\n".join(lines) + "\n"


def _render_safety(facts: dict[str, Any]) -> str:
    scan = facts.get("scan")
    lines: list[str] = []
    if isinstance(scan, dict):
        lines.append(
            f"- 密钥扫描：files_scanned={scan.get('files_scanned')}，"
            f"matched={scan.get('matched_count')}，clean={scan.get('clean')}"
        )
    else:
        lines.append(WAITING)
    lines.append("- 真实 PDF 未修改/未跟踪、benchmark 产物隔离：以 git 检查记录为准。")
    return "\n".join(lines) + "\n"


def _render_conclusion(facts: dict[str, Any]) -> str:
    complete: list[str] = []
    waiting: list[str] = []
    if isinstance(facts.get("pytest_summary"), dict):
        complete.append("自动化测试")
    else:
        waiting.append("自动化测试")
    if isinstance(facts.get("parser_benchmark_aggregate"), dict):
        complete.append("真实 Parser benchmark")
    else:
        waiting.append("真实 Parser benchmark")
    if isinstance(facts.get("goldcheck"), dict):
        complete.append("P 标定 gold")
    else:
        waiting.append("P 标定 gold")
    if isinstance(facts.get("eval"), dict):
        complete.append("四路检索指标")
    else:
        waiting.append("四路检索指标")
    if isinstance(facts.get("scan"), dict):
        complete.append("密钥安全扫描")
    else:
        waiting.append("密钥安全扫描")
    lines: list[str] = []
    if complete:
        lines.append(f"- 已有机器事实：{'、'.join(complete)}。")
    if waiting:
        lines.append(f"- 尚缺机器事实：{'、'.join(waiting)}。")
    lines.append("- 本节只汇总机器事实；最终冻结状态以完成说明中记录的用户决策为准。")
    return "\n".join(lines) + "\n"


def _hybrid_rerank_recall10(report: dict[str, Any]) -> float | None:
    overall = report.get("overall", {})
    value = (overall.get("hybrid_rerank") or {}).get("recall@10")
    return float(value) if isinstance(value, (int, float)) else None


# ---------------------------------------------------------------------------
# Document update
# ---------------------------------------------------------------------------


def update_doc(
    doc_path: Path, facts: dict[str, Any], *, sections: list[str] | None = None
) -> dict[str, Any]:
    """Replace marker-delimited sections in the completion note.

    Returns ``{"doc": new_text, "sections": {name: changed}}``.
    """
    text = doc_path.read_text(encoding="utf-8")
    names = sections or list(SECTION_MARKERS)
    changed: dict[str, bool] = {}
    for name in names:
        if name not in SECTION_MARKERS:
            raise ValueError(f"unknown report section {name!r}")
        open_marker, close_marker = SECTION_MARKERS[name]
        start = text.find(open_marker)
        if start < 0:
            raise ValueError(f"doc {doc_path} has no marker {open_marker}")
        content_start = start + len(open_marker)
        end = text.find(close_marker, content_start)
        if end < 0:
            raise ValueError(f"doc {doc_path} has no closing marker {close_marker}")
        rendered = render_section(name, facts)
        previous = text[content_start:end]
        if previous.strip() == rendered.strip():
            changed[name] = False
            continue
        text = text[:content_start] + "\n" + rendered + text[end:]
        changed[name] = True
    return {"doc": text, "sections": changed}


if __name__ == "__main__":
    raise SystemExit(main())
