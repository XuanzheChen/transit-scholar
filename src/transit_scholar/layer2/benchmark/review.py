"""Human quality review materials for the parser benchmark (FR-PARSER-003).

``generate`` produces, per PDF, a compact review markdown (three-parser
fragments side by side in reading order + structure statistics) and a
machine-readable difference summary stored separately from any human
annotation. ``validate`` checks that human annotations are written as an
independent, parseable file whose ``annotator`` is a human (never a G/E tool
identifier), kept physically separate from the generated diff summaries.

Command::

    python -m transit_scholar.layer2.benchmark.review
      generate --root <benchmark output> [--papers name1,name2]
    python -m transit_scholar.layer2.benchmark.review
      validate --root <benchmark output>

Exit codes: 0 = success (validate: annotations structurally valid), 2 = usage
or missing input, 3 = validation failures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_VALIDATION_FAILED = 3

#: Tool identities that must never appear as a human ``annotator``.
RESERVED_ANNOTATORS = frozenset({"generator", "evaluator", "opencode", "codex"})

#: Human review items (FR-PARSER-003 / AC-PARSER-007).
REVIEW_ITEMS = (
    "reading_order",
    "section_hierarchy",
    "paragraph_integrity",
    "cross_page_paragraph",
    "text_completeness",
    "formula_restoration",
    "table_restoration",
    "caption_relation",
    "page_bbox_grounding",
    "header_footer_noise",
)

_FRAGMENT_LINES = 60


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.benchmark.review",
        description="Generate human review materials and validate annotations.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="generate review materials for a benchmark root")
    gen.add_argument("--root", required=True, help="parser benchmark output root")
    gen.add_argument("--papers", default=None, help="comma-separated pdf names to limit to")
    val = sub.add_parser("validate", help="validate the human annotations file")
    val.add_argument("--root", required=True, help="parser benchmark output root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate":
        return _generate_materials(args)
    return _validate(args)


def _generate_materials(args: argparse.Namespace) -> int:
    root = Path(args.root)
    units_dir = root / "units"
    if not units_dir.is_dir():
        print(f"benchmark root has no units/ directory: {root}", file=sys.stderr)
        return EXIT_USAGE
    papers = set(args.papers.split(",")) if args.papers else None
    records = _load_unit_records(root)
    if not records:
        print(f"no unit records found under {root}", file=sys.stderr)
        return EXIT_USAGE

    by_pdf: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        if papers is not None and record["pdf_name"] not in papers:
            continue
        by_pdf.setdefault(record["pdf_name"], {})[record["parser_name"]] = record

    review_dir = root / "review"
    diffs_dir = review_dir / "diffs"
    review_dir.mkdir(parents=True, exist_ok=True)
    diffs_dir.mkdir(parents=True, exist_ok=True)

    for pdf_name, parsers in sorted(by_pdf.items()):
        md = _render_review_md(pdf_name, parsers)
        (review_dir / f"{_safe_stem(pdf_name)}.md").write_text(md, encoding="utf-8")
        diff = _diff_summary(pdf_name, parsers)
        (diffs_dir / f"{_safe_stem(pdf_name)}.json").write_text(
            json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(f"review materials written to {review_dir}", file=sys.stderr)
    return EXIT_OK


def _render_review_md(pdf_name: str, parsers: dict[str, dict[str, Any]]) -> str:
    lines = [f"# Review: {pdf_name}", ""]
    lines.append("> Human annotations belong in `annotations.jsonl` (see "
                 "`validate --help`); the `diffs/` summaries here are generated "
                 "by the tool and are NOT human scores.")
    lines.append("")
    for parser_name in sorted(parsers):
        record = parsers[parser_name]
        lines.append(f"## {parser_name}")
        lines.append("")
        lines.append("### Structure stats")
        lines.append("")
        lines.append("| metric | value |")
        lines.append("|---|---|")
        for key in (
            "status", "page_count", "section_count", "block_count",
            "table_count", "figure_count", "equation_count", "caption_count",
            "caption_relation_completeness", "meaningful_page_ratio",
            "replacement_char_ratio", "duplicate_ratio",
            "provenance_page_coverage", "bbox_coverage",
        ):
            lines.append(f"| {key} | {record.get(key)} |")
        lines.append("")
        lines.append("### Markdown fragment (first lines)")
        lines.append("")
        fragment = _fragment(record, _FRAGMENT_LINES)
        lines.append("```markdown")
        lines.append(fragment)
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _fragment(record: dict[str, Any], max_lines: int) -> str:
    artifact_dir = Path(record["artifact_dir"]) if record.get("artifact_dir") else None
    if artifact_dir is None:
        return "(no artifact directory recorded)"
    markdown_path = artifact_dir / "paper.md"
    if not markdown_path.is_file():
        return "(paper.md missing)"
    try:
        text = markdown_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(paper.md unreadable: {exc})"
    content = "\n".join(line for line in text.splitlines() if line.strip())
    if not content:
        return "(empty paper.md)"
    return "\n".join(content.splitlines()[:max_lines])


def _diff_summary(pdf_name: str, parsers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, dict[str, Any]] = {}
    for parser_name, record in sorted(parsers.items()):
        stats[parser_name] = {
            key: record.get(key)
            for key in (
                "status", "page_count", "section_count", "block_count",
                "table_count", "figure_count", "equation_count", "caption_count",
                "caption_relation_completeness", "meaningful_page_ratio",
                "replacement_char_ratio", "duplicate_ratio",
            )
        }
    differences: dict[str, Any] = {}
    keys = ("page_count", "section_count", "block_count", "table_count",
            "figure_count", "equation_count", "caption_count")
    for key in keys:
        values = {
            name: stats[name].get(key) for name in stats
            if stats[name].get(key) is not None
        }
        if len(set(str(v) for v in values.values())) > 1:
            differences[key] = values
    return {
        "pdf": pdf_name,
        "generated_by": "transit_scholar.layer2.benchmark.review",
        "generated_at": _now_iso(),
        "per_parser_stats": stats,
        "differences": differences,
        "note": "machine-generated summary; not a human score",
    }


# ---------------------------------------------------------------------------
# Annotation validation (AC-PARSER-007)
# ---------------------------------------------------------------------------


def _validate(args: argparse.Namespace) -> int:
    root = Path(args.root)
    annotations_path = root / "review" / "annotations.jsonl"
    if not annotations_path.is_file():
        print(f"annotations file missing: {annotations_path}", file=sys.stderr)
        return EXIT_USAGE
    errors, records = validate_annotations(annotations_path)
    report = {
        "annotations_file": str(annotations_path),
        "annotations_count": len(records),
        "errors": errors,
        "valid": not errors,
    }
    (root / "review" / "annotation_validation.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if errors:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return EXIT_VALIDATION_FAILED
    print(
        f"annotations valid: {len(records)} record(s), "
        f"annotator(s) = {sorted({r['annotator'] for r in records})}",
        file=sys.stderr,
    )
    return EXIT_OK


def validate_annotations(annotations_path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Structural validation of the human annotation file.

    Every line is a JSON object with ``annotator`` / ``paper`` / ``parser`` /
    ``item`` / ``score`` / ``notes`` / ``date``. The ``annotator`` must be a
    human identity (never a reserved G/E tool identifier).
    """
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    lines = annotations_path.read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_no}: not valid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            errors.append(f"line {line_no}: annotation must be a JSON object")
            continue
        for field in ("annotator", "paper", "parser", "item"):
            if not record.get(field):
                errors.append(f"line {line_no}: missing required field {field!r}")
        annotator = str(record.get("annotator", "")).strip()
        if annotator.lower() in RESERVED_ANNOTATORS:
            errors.append(
                f"line {line_no}: annotator {annotator!r} is a reserved G/E tool "
                "identity; human annotations only"
            )
        item = record.get("item")
        if item is not None and item not in REVIEW_ITEMS:
            errors.append(f"line {line_no}: unknown review item {item!r}")
        score = record.get("score")
        if score is not None and not (isinstance(score, int) and 1 <= score <= 5):
            errors.append(f"line {line_no}: score must be an int in [1, 5] or null")
        date = record.get("date")
        if date and not isinstance(date, str):
            errors.append(f"line {line_no}: date must be a string")
        records.append(record)
    return errors, records


def _safe_stem(pdf_name: str) -> str:
    return pdf_name.rsplit(".", 1)[0] if "." in pdf_name else pdf_name


def _load_unit_records(root: Path) -> list[dict[str, Any]]:
    per_paper = root / "per_paper.jsonl"
    if not per_paper.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in per_paper.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _now_iso() -> str:
    from transit_scholar.layer2.util import now_utc_iso

    return now_utc_iso()


if __name__ == "__main__":
    raise SystemExit(main())
