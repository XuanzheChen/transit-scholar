"""L2S2 Package E report generation (AC-E-36..41).

Produces the machine-readable ``acceptance_report.json`` and the
human-readable ``acceptance_summary.md`` from an ``AcceptanceReport`` model.

Locked wording contract (AC-E-02/40): this tool never declares the L2S2 V1
freeze. ``freeze.declared_frozen`` is constantly false, the fixed wording
states that freezing is decided by the user/Planner, and the three-valued
``freeze_suggestion`` is a deterministic rule about data/error completeness
only — it is explicitly not a quality endorsement.

This module imports only stdlib, pydantic and the sibling Package E models.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .evaluate import AcceptanceIssue, PaperEvaluation
from .gold import GoldBenchmark
from .metrics import (
    AggregateMetrics,
    FieldAggregate,
    compute_overall_metrics,
    compute_per_field,
    compute_paper_metrics,
)

#: Fixed report schema version: constant across runs (AC-E-37).
REPORT_SCHEMA_VERSION = "1.0"

#: Fixed freeze wording (AC-E-40; Evaluator greps for these keywords).
FREEZE_MESSAGE = (
    "冻结由用户/Planner 决定，本工具不宣告冻结。"
    "freeze_suggestion 仅反映数据齐备性与 error 级 issue 情况，"
    "不构成质量背书。"
)

FreezeSuggestion = Literal["insufficient_data", "not_ready", "ready"]
TraceabilityMode = Literal["weak_only", "strict", "strict_failed"]


class AcceptanceReportError(Exception):
    """Report files could not be written (explicit failure, AC-E-36)."""


class TraceabilityBlock(BaseModel):
    """Overall traceability summary (AC-E-37)."""

    model_config = ConfigDict(extra="allow")

    mode: TraceabilityMode
    weak_traceability_rate: float | None = None
    strict_traceability_rate: float | None = None


class GoldReviewItem(BaseModel):
    """A gold-status-review candidate (AC-T001-S4/S6, F-01).

    Surfaced for the user to confirm or amend the gold; never auto-corrected.
    """

    model_config = ConfigDict(extra="allow")

    paper_id: str
    field_id: str
    expected_status: str | None = None
    predicted_status: str | None = None
    rationale: str


class FreezeBlock(BaseModel):
    """Freeze statement block (AC-E-40 + AC-T001-F21..F23)."""

    model_config = ConfigDict(extra="allow")

    declared_frozen: bool = False
    message: str = FREEZE_MESSAGE
    freeze_suggestion: FreezeSuggestion = "insufficient_data"
    blocking_error_count: int = 0
    diagnostic_warning_count: int = 0
    gold_review_count: int = 0
    exact_match_diagnostic_count: int = 0
    remaining_freezing_blockers: list[dict[str, Any]] = Field(default_factory=list)


class MetricsBlock(BaseModel):
    """Three-layer metrics aggregation (AC-E-34)."""

    model_config = ConfigDict(extra="allow")

    overall: AggregateMetrics
    per_paper: dict[str, AggregateMetrics] = Field(default_factory=dict)
    per_field: dict[str, FieldAggregate] = Field(default_factory=dict)


class AcceptanceReport(BaseModel):
    """Full acceptance report (AC-E-37)."""

    model_config = ConfigDict(extra="allow")

    benchmark_id: str | None = None
    description: str | None = None
    gold_version: str | None = None
    report_schema_version: str = REPORT_SCHEMA_VERSION
    gold_path: str | None = None
    schema_id: str | None = None
    schema_version: str | None = None
    papers: list[PaperEvaluation] = Field(default_factory=list)
    metrics: MetricsBlock
    issues: list[AcceptanceIssue] = Field(default_factory=list)
    traceability: TraceabilityBlock
    freeze: FreezeBlock
    gold_status_review: list[GoldReviewItem] = Field(default_factory=list)
    generated_at: str


# ---------------------------------------------------------------------------
# freeze / traceability rules
# ---------------------------------------------------------------------------


def _is_freeze_blocking(issue: AcceptanceIssue) -> bool:
    """A freeze-blocking issue is any error-level issue after FR-005
    reclassification (AC-T001-F21/F22). Value diagnostics and status
    explanations are warnings and never block."""
    return issue.severity == "error"


def _remaining_freezing_blockers(
    issues: list[AcceptanceIssue],
) -> list[dict[str, Any]]:
    """List every freeze blocker as ``{type, fields, message}`` (AC-T001-F22).
    Diagnostics (value_mismatch / judgement_conflict / explainable status
    warnings / gold-review candidates) are excluded because they are not
    error-level."""
    blockers: list[dict[str, Any]] = []
    for issue in issues:
        if not _is_freeze_blocking(issue):
            continue
        blockers.append(
            {
                "type": issue.type,
                "fields": list(issue.fields),
                "message": issue.message,
            }
        )
    return blockers


def _freeze_breakdown(
    issues: list[AcceptanceIssue], gold_review_items: list[GoldReviewItem]
) -> dict[str, int]:
    """Breakdown counts for the freeze block (AC-T001-F23):
    blocking-error, diagnostic-warning, gold-review, exact-match-diagnostic."""
    blocking = sum(1 for i in issues if _is_freeze_blocking(i))
    exact_match = sum(
        1
        for i in issues
        if i.severity == "warning" and i.type in ("value_mismatch", "judgement_conflict")
    )
    diagnostics = sum(
        1
        for i in issues
        if i.severity == "warning"
        and i.type not in ("value_mismatch", "judgement_conflict", "gold_status_review")
    )
    return {
        "blocking_error_count": blocking,
        "exact_match_diagnostic_count": exact_match,
        "diagnostic_warning_count": diagnostics,
        "gold_review_count": len(gold_review_items),
    }


def collect_gold_review_items(
    papers: list[PaperEvaluation],
) -> list[GoldReviewItem]:
    """Aggregate the gold-status-review candidates from per-field evaluation
    (AC-T001-S6/F23)."""
    items: list[GoldReviewItem] = []
    for paper in papers:
        for field_eval in paper.fields:
            if field_eval.gold_review:
                items.append(
                    GoldReviewItem(
                        paper_id=paper.paper_id,
                        field_id=field_eval.field_id,
                        expected_status=field_eval.expected_status,
                        predicted_status=field_eval.predicted_status,
                        rationale=field_eval.status_explanation
                        or "gold status 口径需要用户确认",
                    )
                )
    return items


def compute_freeze_suggestion(
    papers: list[PaperEvaluation],
    metrics: AggregateMetrics,
    issues: list[AcceptanceIssue],
    gold_review_items: list[GoldReviewItem],
) -> str:
    """Deterministic three-state freeze rule (AC-T001-F21, FR-006).

    Order:
    1. ``insufficient_data`` — paper_error / template / draft / 0 evaluated;
    2. gold-review candidates exist -> ``not_ready`` (gold status 口径需用户确认,
       the only allowed user-confirmation gate);
    3. any blocking error-level issue -> ``not_ready`` with blockers listed;
    4. otherwise ``ready`` (never includes a human-corrected exact-string
       mismatch as a blocker).
    """
    has_paper_error = any(p.paper_error is not None for p in papers)
    has_template = any(p.template for p in papers)
    has_draft = any(p.draft for p in papers)
    if (
        has_paper_error
        or has_template
        or has_draft
        or metrics.evaluated_field_count == 0
    ):
        return "insufficient_data"
    if gold_review_items:
        return "not_ready"
    selected_unevaluated = any(
        p.selected_for_v1 is True and (p.paper_error is not None or p.template)
        for p in papers
    )
    if selected_unevaluated:
        return "not_ready"
    if any(_is_freeze_blocking(issue) for issue in issues):
        return "not_ready"
    return "ready"


def compute_traceability(
    papers: list[PaperEvaluation],
    metrics: AggregateMetrics,
    canonical_reader: Any,
) -> TraceabilityBlock:
    """Overall traceability mode and rates (AC-E-27/28/37)."""
    if canonical_reader is None:
        mode: TraceabilityMode = "weak_only"
    elif any(
        field.strict_mode == "strict_failed"
        for paper in papers
        for field in paper.fields
    ):
        mode = "strict_failed"
    else:
        mode = "strict"
    return TraceabilityBlock(
        mode=mode,
        weak_traceability_rate=metrics.weak_traceability_rate,
        strict_traceability_rate=metrics.strict_traceability_rate,
    )


# ---------------------------------------------------------------------------
# report assembly
# ---------------------------------------------------------------------------


def _flatten_issues(
    papers: list[PaperEvaluation], gold_issues: list[AcceptanceIssue]
) -> list[AcceptanceIssue]:
    issues: list[AcceptanceIssue] = list(gold_issues)
    for paper in papers:
        issues.extend(paper.issues)
        for field_eval in paper.fields:
            issues.extend(field_eval.issues)
    return issues


def build_acceptance_report(
    gold: GoldBenchmark,
    papers: list[PaperEvaluation],
    gold_issues,
    *,
    canonical_reader: Any = None,
    gold_path: str | None = None,
    generated_at: str | None = None,
) -> AcceptanceReport:
    """Assemble the full three-layer report (AC-E-34..37)."""
    gold_acceptance_issues = [
        AcceptanceIssue.from_validation_issue(issue, "gold_validation")
        for issue in gold_issues
    ]
    overall = compute_overall_metrics(papers, extra_issue_count=len(gold_acceptance_issues))
    all_issues = _flatten_issues(papers, gold_acceptance_issues)
    gold_review_items = collect_gold_review_items(papers)
    breakdown = _freeze_breakdown(all_issues, gold_review_items)

    schema_id: str | None = None
    schema_version: str | None = None
    for entry in gold.papers:
        if entry.schema_id:
            schema_id = entry.schema_id
            schema_version = entry.schema_version
            break

    return AcceptanceReport(
        benchmark_id=gold.benchmark_id,
        description=gold.description,
        gold_version=gold.gold_version,
        report_schema_version=REPORT_SCHEMA_VERSION,
        gold_path=gold_path,
        schema_id=schema_id,
        schema_version=schema_version,
        papers=papers,
        metrics=MetricsBlock(
            overall=overall,
            per_paper={p.paper_id: compute_paper_metrics(p) for p in papers},
            per_field=compute_per_field(papers),
        ),
        issues=all_issues,
        traceability=compute_traceability(papers, overall, canonical_reader),
        freeze=FreezeBlock(
            declared_frozen=False,
            message=FREEZE_MESSAGE,
            freeze_suggestion=compute_freeze_suggestion(
                papers, overall, all_issues, gold_review_items
            ),
            blocking_error_count=breakdown["blocking_error_count"],
            diagnostic_warning_count=breakdown["diagnostic_warning_count"],
            gold_review_count=breakdown["gold_review_count"],
            exact_match_diagnostic_count=breakdown["exact_match_diagnostic_count"],
            remaining_freezing_blockers=_remaining_freezing_blockers(all_issues),
        ),
        gold_status_review=gold_review_items,
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def write_acceptance_report(
    report: AcceptanceReport, output_dir: str | Path
) -> tuple[Path, Path]:
    """Write ``acceptance_report.json`` and ``acceptance_summary.md``
    (AC-E-36).

    Creates the output directory when missing; write failures raise
    ``AcceptanceReportError`` explicitly. Returns both file paths.
    """
    out = Path(output_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AcceptanceReportError(
            f"could not create output directory {str(out)!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    json_path = out / "acceptance_report.json"
    md_path = out / "acceptance_summary.md"
    try:
        json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown_summary(report), encoding="utf-8")
        if report.gold_status_review:
            (out / "gold_status_review.json").write_text(
                json.dumps(
                    [item.model_dump() for item in report.gold_status_review],
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
    except OSError as exc:
        raise AcceptanceReportError(
            f"could not write acceptance report under {str(out)!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return json_path, md_path


# ---------------------------------------------------------------------------
# markdown summary
# ---------------------------------------------------------------------------


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def render_markdown_summary(report: AcceptanceReport) -> str:
    """Render the human-readable Markdown summary (AC-E-39/40).

    Six fixed sections: 冻结建议 / 总体指标 / 每篇论文简表 / top failure
    fields / failure analysis / 后续建议. Never contains the word 已冻结.
    """
    lines: list[str] = []
    overall = report.metrics.overall
    has_template = any(p.template for p in report.papers)
    has_draft = any(p.draft for p in report.papers)

    lines.append("# L2S2 Schema Acceptance Report")
    lines.append("")
    if report.benchmark_id:
        lines.append(f"- benchmark: {report.benchmark_id}")
    if report.gold_version:
        lines.append(f"- gold version: {report.gold_version}")
    if report.gold_path:
        lines.append(f"- gold path: {report.gold_path}")
    lines.append(f"- schema: {report.schema_id or '-'} "
                 f"(version {report.schema_version or '-'})")
    lines.append(f"- generated at: {report.generated_at}")
    lines.append(f"- report schema version: {report.report_schema_version}")
    lines.append("")

    # 1. freeze suggestion
    lines.append("## 1. 冻结建议 (Freeze Suggestion)")
    lines.append("")
    lines.append(f"- freeze_suggestion: **{report.freeze.freeze_suggestion}**")
    lines.append(
        f"- declared_frozen: {str(report.freeze.declared_frozen).lower()}"
    )
    lines.append(f"- {report.freeze.message}")
    lines.append("")
    lines.append(
        "- 规则说明: insufficient_data = 存在 paper_error / 模板(template)条目 / "
        "draft 条目（LLM 辅助草稿，非人工 gold）/ evaluated_field_count == 0；"
        "gold_status_review 存在 -> not_ready（gold status 口径需用户确认）；"
        "not_ready = 存在 error 级 issue 或任一 selected_for_v1 论文未被成功评估；"
        "ready 仅表示数据齐全且无 error 级 issue，不是质量背书。"
    )
    lines.append(
        "- 口径说明: value 字段的人工 value_judgement 优先于自动 exact string "
        "match；自动 exact mismatch 仅为诊断（warning），不会被人工判定为 correct "
        "的字段阻断 freeze。explicit/inferred 等 status 差异按冻结口径分类为 "
        "warning（可解释）或 error（阻断）。"
    )
    lines.append("")
    if report.freeze.gold_review_count > 0:
        lines.append(
            "> 注意：本报告包含 gold_status_review 条目：gold 自身的 "
            "expected_status 与 gold 内容在冻结口径下矛盾，需要用户确认或修正 "
            "gold。gold_review_count 存在时 freeze_suggestion 恒为 not_ready "
            "（用户确认门）。"
        )
        lines.append("")
    lines.append(
        "- 冻结阻断统计: blocking_error_count = "
        f"{report.freeze.blocking_error_count}; diagnostic_warning_count = "
        f"{report.freeze.diagnostic_warning_count}; gold_review_count = "
        f"{report.freeze.gold_review_count}; exact_match_diagnostic_count = "
        f"{report.freeze.exact_match_diagnostic_count}."
    )
    lines.append("")
    if report.freeze.remaining_freezing_blockers:
        lines.append("- 剩余冻结阻断项 (remaining_freezing_blockers):")
        lines.append("  | type | fields | message |")
        lines.append("  |---|---|---|")
        for blocker in report.freeze.remaining_freezing_blockers:
            fields = ", ".join(blocker.get("fields") or []) or "-"
            lines.append(
                f"  | {blocker.get('type')} | {fields} | {blocker.get('message')} |"
            )
        lines.append("")
    if has_template:
        lines.append(
            "> 注意：本报告包含模板条目（template），模板字段未经过人工标注，"
            "指标不代表真实论文质量。"
        )
        lines.append("")
    if has_draft:
        lines.append(
            "> 注意：本报告包含 draft 条目（LLM-assisted draft，非人工 gold），"
            "draft 字段未经过人工评估，不可用于宣告 L2S2 V1 冻结；"
            "必须由人工审核确认后才能转换为正式 gold。"
        )
        lines.append("")

    # 2. overall metrics
    lines.append("## 2. 总体指标 (Overall Metrics)")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    for name in (
        "paper_count",
        "field_count",
        "missing_prediction_count",
        "value_not_evaluated_count",
        "evaluated_field_count",
        "value_correct_count",
        "value_partial_count",
        "value_incorrect_count",
        "value_accuracy",
        "status_accuracy",
        "evidence_required_count",
        "evidence_present_rate",
        "weak_traceability_rate",
        "strict_traceability_rate",
        "not_found_correctness",
        "issue_count",
    ):
        value = getattr(overall, name)
        rendered = (
            _fmt_rate(value)
            if isinstance(value, float) or value is None
            else str(value)
        )
        lines.append(f"| {name} | {rendered} |")
    lines.append("")
    lines.append(
        f"- traceability mode: {report.traceability.mode}"
    )
    lines.append("")

    # 3. per-paper summary table
    lines.append("## 3. 每篇论文简表 (Per-paper Summary)")
    lines.append("")
    lines.append(
        "| paper_id | title | selected_for_v1 | run_kind | run_id | 字段数 | "
        "value_accuracy | status_accuracy | issue 数 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for paper in report.papers:
        metrics = report.metrics.per_paper.get(paper.paper_id)
        label = ""
        if paper.draft:
            label = " (draft)"
        elif paper.template:
            label = " (template)"
        lines.append(
            f"| {paper.paper_id} | {paper.title or '-'}{label} | "
            f"{paper.selected_for_v1} | {paper.run_kind} | {paper.run_id or '-'} | "
            f"{metrics.field_count if metrics else 0} | "
            f"{_fmt_rate(metrics.value_accuracy) if metrics else '-'} | "
            f"{_fmt_rate(metrics.status_accuracy) if metrics else '-'} | "
            f"{metrics.issue_count if metrics else 0} |"
        )
    lines.append("")

    # 4. top failure fields
    lines.append("## 4. Top Failure Fields")
    lines.append("")
    ranked = sorted(
        report.metrics.per_field.values(),
        key=lambda a: (a.issue_count, a.status_mismatch, a.missing, a.field_id),
        reverse=True,
    )
    lines.append(
        "| field_id | correct | partial | incorrect | not_evaluated | missing | "
        "status_mismatch | issue 数 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for agg in ranked[:10]:
        lines.append(
            f"| {agg.field_id} | {agg.correct} | {agg.partial} | {agg.incorrect} | "
            f"{agg.not_evaluated} | {agg.missing} | {agg.status_mismatch} | "
            f"{agg.issue_count} |"
        )
    lines.append("")

    # 5. failure analysis
    lines.append("## 5. Failure Analysis")
    lines.append("")
    error_issues = [i for i in report.issues if i.severity == "error"]
    if error_issues:
        by_type: dict[str, int] = {}
        for issue in error_issues:
            by_type[issue.type] = by_type.get(issue.type, 0) + 1
        for issue_type, count in sorted(by_type.items()):
            lines.append(f"- {issue_type}: {count}")
        lines.append("")
        for issue in error_issues[:20]:
            fields = f" [{', '.join(issue.fields)}]" if issue.fields else ""
            lines.append(f"- `{issue.type}`{fields} ({issue.source}): {issue.message}")
    else:
        lines.append("- 无 error 级 issue。")
    lines.append("")

    # 6. recommendations
    lines.append("## 6. 后续建议 (Recommendations)")
    lines.append("")
    if report.freeze.freeze_suggestion == "insufficient_data":
        lines.append(
            "- 存在模板条目、draft 条目、paper_error 或 0 个已评估字段：请先完成 "
            "人工 gold 标注（模板条目必须经人工填写并去除 template 标记；draft "
            "条目必须经人工审核确认并去除 draft 标记），并确保每篇 "
            "selected_for_v1 论文都有可读的 schema run。draft 是 LLM 辅助草稿，"
            "非人工 gold，不可作为冻结依据。"
        )
    elif report.freeze.freeze_suggestion == "not_ready":
        if report.freeze.gold_review_count > 0:
            lines.append(
                "- 当前阻断项是 gold status 口径需用户确认（gold_review_count = "
                f"{report.freeze.gold_review_count}）：请核对 "
                "gold_status_review 列表中的 expected_status 与 gold 自身内容，"
                "确认或修正 gold 后重跑本验收。这是本规则下唯一合法的用户确认门。"
            )
            if report.freeze.remaining_freezing_blockers:
                lines.append(
                    "- 此外仍存在 error 级阻断项（见上表 remaining_freezing_blockers），"
                    "请优先修复后重跑。"
                )
        elif report.freeze.remaining_freezing_blockers:
            lines.append(
                "- 存在 error 级 issue 或 selected_for_v1 论文未被成功评估：优先处理 "
                "上表 remaining_freezing_blockers 中的阻断项（缺失预测、证据回溯、"
                "阻断性 status mismatch 等），必要时通过 targeted recheck 修复后"
                "重跑本验收。"
            )
        else:
            lines.append(
                "- selected_for_v1 论文未被成功评估：请确保每篇所选论文都有可读的"
                " schema run 后重跑本验收。"
            )
        lines.append(
            "- 说明：值为人工判定 correct/partially_correct 的字段，其自动 exact "
            "string 差异仅是诊断（value_mismatch/judgement_conflict 为 warning），"
            "不作为冻结阻断项；explicit/inferred 等可解释 status 差异也不阻断冻结。"
        )
    else:
        lines.append(
            "- 数据齐全且无 error 级 issue。注意：ready 仅表示数据与错误齐备性，"
            "不构成质量背书；是否冻结由用户/Planner 根据本报告决定。"
        )
    lines.append("- 本工具不宣告 L2S2 V1 冻结（declared_frozen 恒为 false）。")
    lines.append("")
    return "\n".join(lines)
