"""Gold annotation validation (FR-GOLD-001/002/003).

Machine checks over the P-annotated gold file:

1. structure -- required fields, valid ``query_type``, parseable block ids,
   in-range ``gold_source_spans``;
2. evidence -- every ``gold_block_ids`` exists in that paper's accepted run's
   canonical blocks; every span slices a non-empty substring;
3. scale -- paper count, query count, per-paper query range, all seven query
   types present, globally unique queries;
4. language -- zh/en split (CJK rule), |zh-en| <= 1, each language covers at
   least three query types;
5. ownership -- the annotator record exists and names a human (P), never a
   reserved G/E tool identity.

Command::

    python -m transit_scholar.layer2.eval.goldcheck
      --gold <gold.json> --data-root <root> [--annotator <gold_annotator.json>]
      [--out goldcheck_report.json]
      [--min-papers 10] [--min-queries 25] [--min-per-paper 2] [--max-per-paper 3]

Exit codes: 0 = all checks pass, 2 = usage/input error, 3 = validation failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from transit_scholar.layer2.eval.gold import QUERY_TYPES, load_gold_queries
from transit_scholar.layer2.schema import GoldQuery

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_VALIDATION_FAILED = 3

CJK_RANGES = (
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x3040, 0x30FF),
)

#: Reserved G/E tool identities (the human annotator must never be one of them).
RESERVED_ANNOTATORS = frozenset({"generator", "evaluator", "opencode", "codex"})


def classify_language(query: str) -> str:
    """CJK-based language classification: any CJK char -> ``zh`` else ``en``."""
    for char in query:
        codepoint = ord(char)
        for low, high in CJK_RANGES:
            if low <= codepoint <= high:
                return "zh"
    return "en"


def check_structure(gold: list[GoldQuery]) -> list[str]:
    errors: list[str] = []
    for index, entry in enumerate(gold):
        label = f"query[{index}]"
        if not entry.paper_id:
            errors.append(f"{label}: empty paper_id")
        if not entry.query:
            errors.append(f"{label}: empty query")
        if entry.query_type not in QUERY_TYPES:
            errors.append(f"{label}: illegal query_type {entry.query_type!r}")
        if not isinstance(entry.gold_block_ids, list) or not entry.gold_block_ids:
            errors.append(f"{label}: gold_block_ids must be a non-empty list")
        for block_id in entry.gold_block_ids:
            if not isinstance(block_id, str) or not block_id:
                errors.append(f"{label}: non-string gold_block_id {block_id!r}")
        for span in entry.gold_source_spans or []:
            for key in ("block_id", "char_start", "char_end"):
                if key not in span:
                    errors.append(f"{label}: span missing {key!r}")
                    continue
            if isinstance(span.get("char_start"), str) or isinstance(span.get("char_end"), str):
                errors.append(f"{label}: span char range must be integers")
    return errors


def check_evidence(
    gold: list[GoldQuery], blocks_by_paper: dict[str, dict[str, str]]
) -> list[str]:
    """``blocks_by_paper`` maps paper_id -> {block_id: block_text} for the
    accepted runs actually used by the evaluation."""
    errors: list[str] = []
    for index, entry in enumerate(gold):
        label = f"query[{index}]"
        blocks = blocks_by_paper.get(entry.paper_id, {})
        for block_id in entry.gold_block_ids:
            if block_id not in blocks:
                errors.append(
                    f"{label}: gold block {block_id!r} does not exist in paper "
                    f"{entry.paper_id!r} accepted run"
                )
        for span in entry.gold_source_spans or []:
            block_id = span.get("block_id")
            text = blocks.get(block_id)
            if text is None:
                errors.append(f"{label}: span block {block_id!r} missing")
                continue
            char_start, char_end = int(span["char_start"]), int(span["char_end"])
            if not (0 <= char_start < char_end <= len(text)):
                errors.append(
                    f"{label}: span out of range for block {block_id!r} "
                    f"({char_start}, {char_end} vs len={len(text)})"
                )
            elif not text[char_start:char_end].strip():
                errors.append(
                    f"{label}: span slices an empty substring in block {block_id!r}"
                )
    return errors


def check_scale(
    gold: list[GoldQuery],
    *,
    min_papers: int = 10,
    min_queries: int = 25,
    min_per_paper: int = 2,
    max_per_paper: int = 3,
    required_types: int = len(QUERY_TYPES),
) -> list[str]:
    errors: list[str] = []
    queries = [entry.query for entry in gold]
    if len(queries) != len(set(queries)):
        duplicates = sorted(
            {query for query in queries if queries.count(query) > 1}
        )
        errors.append(f"duplicate queries (evaluator key collisions): {duplicates}")
    papers: dict[str, int] = {}
    for entry in gold:
        papers[entry.paper_id] = papers.get(entry.paper_id, 0) + 1
    if len(papers) < min_papers:
        errors.append(f"paper_count={len(papers)} < {min_papers}")
    if len(gold) < min_queries:
        errors.append(f"query_count={len(gold)} < {min_queries}")
    for paper_id, count in sorted(papers.items()):
        if not (min_per_paper <= count <= max_per_paper):
            errors.append(
                f"paper {paper_id!r} has {count} queries; expected "
                f"[{min_per_paper}, {max_per_paper}] (exceptions need P's "
                "written note attached to the acceptance record)"
            )
    present_types = {entry.query_type for entry in gold}
    missing_types = set(QUERY_TYPES) - present_types
    if required_types and missing_types:
        errors.append(f"missing query types: {sorted(missing_types)}")
    return errors


def check_language(
    gold: list[GoldQuery], *, min_types_per_language: int = 3
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    zh_query_count = 0
    en_query_count = 0
    zh_types: dict[str, int] = {}
    en_types: dict[str, int] = {}
    per_query: list[dict[str, str]] = []
    for entry in gold:
        language = classify_language(entry.query)
        per_query.append(
            {"query_id": f"q_{len(per_query):04d}", "query": entry.query,
             "language": language, "query_type": entry.query_type}
        )
        if language == "zh":
            zh_query_count += 1
            zh_types[entry.query_type] = zh_types.get(entry.query_type, 0) + 1
        else:
            en_query_count += 1
            en_types[entry.query_type] = en_types.get(entry.query_type, 0) + 1
    if abs(zh_query_count - en_query_count) > 1:
        errors.append(f"|zh - en| = {abs(zh_query_count - en_query_count)} > 1")
    if len(zh_types) < min_types_per_language:
        errors.append(
            f"zh covers only {len(zh_types)} query type(s); "
            f"need >= {min_types_per_language}"
        )
    if len(en_types) < min_types_per_language:
        errors.append(
            f"en covers only {len(en_types)} query type(s); "
            f"need >= {min_types_per_language}"
        )
    summary = {
        "zh_count": zh_query_count,
        "en_count": en_query_count,
        "zh_types": sorted(zh_types),
        "en_types": sorted(en_types),
        "per_query_language": per_query,
    }
    return errors, summary


def check_annotator(annotator_path: Path | None) -> tuple[list[str], dict[str, Any]]:
    if annotator_path is None or not annotator_path.is_file():
        return ["annotator record file missing"], {}
    try:
        record = json.loads(annotator_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"annotator record not parseable: {exc}"], {}
    annotator = str(record.get("annotator", "")).strip()
    errors: list[str] = []
    if not annotator:
        errors.append("annotator record has empty annotator")
    if annotator.lower() in RESERVED_ANNOTATORS:
        errors.append(
            f"annotator {annotator!r} is a reserved G/E tool identity; P is "
            "the only allowed annotator"
        )
    return errors, {"annotator": annotator, "date": record.get("date")}


def run_goldcheck(
    gold: list[GoldQuery],
    *,
    blocks_by_paper: dict[str, dict[str, str]] | None = None,
    annotator_path: Path | None = None,
    min_papers: int = 10,
    min_queries: int = 25,
    min_per_paper: int = 2,
    max_per_paper: int = 3,
    min_types_per_language: int = 3,
    required_types: int = len(QUERY_TYPES),
) -> dict[str, Any]:
    """Run all gold checks and return the machine-readable report."""
    blocks_by_paper = blocks_by_paper or {}
    structure_errors = check_structure(gold)
    evidence_errors = check_evidence(gold, blocks_by_paper)
    scale_errors = check_scale(
        gold,
        min_papers=min_papers,
        min_queries=min_queries,
        min_per_paper=min_per_paper,
        max_per_paper=max_per_paper,
        required_types=required_types,
    )
    language_errors, language_summary = check_language(
        gold, min_types_per_language=min_types_per_language
    )
    annotator_errors, annotator_summary = check_annotator(annotator_path)
    all_errors = (
        structure_errors
        + evidence_errors
        + scale_errors
        + language_errors
        + annotator_errors
    )
    return {
        "valid": not all_errors,
        "errors": all_errors,
        "query_count": len(gold),
        "paper_count": len({entry.paper_id for entry in gold}),
        "query_type_count": len({entry.query_type for entry in gold}),
        "query_types": sorted({entry.query_type for entry in gold}),
        "zh_count": language_summary.get("zh_count"),
        "en_count": language_summary.get("en_count"),
        "zh_types": language_summary.get("zh_types"),
        "en_types": language_summary.get("en_types"),
        "per_query_language": language_summary.get("per_query_language", []),
        "annotator": annotator_summary,
        "checks": {
            "structure": {"ok": not structure_errors, "errors": structure_errors},
            "evidence": {"ok": not evidence_errors, "errors": evidence_errors},
            "scale": {"ok": not scale_errors, "errors": scale_errors},
            "language": {"ok": not language_errors, "errors": language_errors},
            "annotator": {"ok": not annotator_errors, "errors": annotator_errors},
        },
    }


def load_blocks_by_paper(data_root: str | Path) -> dict[str, dict[str, str]]:
    """Load ``{block_id: text}`` for every paper's accepted run."""
    from transit_scholar.config import Settings
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.paths import load_current, run_paths

    config = Layer2Config.from_settings(Settings(data_root=Path(data_root)))
    parsed_dir = config.layer2_parsed_dir
    result: dict[str, dict[str, str]] = {}
    if not parsed_dir.is_dir():
        return result
    for paper_dir in sorted(parsed_dir.iterdir()):
        if not paper_dir.is_dir():
            continue
        paper_id = paper_dir.name
        current_run = load_current(paper_dir)
        if current_run is None:
            continue
        rp = run_paths(config, paper_id, current_run)
        if not rp.blocks_path.is_file():
            continue
        blocks: dict[str, str] = {}
        for line in rp.blocks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            block = json.loads(line)
            blocks[block["block_id"]] = block.get("text") or ""
        result[paper_id] = blocks
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.eval.goldcheck",
        description="Validate the P-annotated gold file (structure, evidence, "
                    "scale, language balance, ownership).",
    )
    parser.add_argument("--gold", required=True, help="gold JSON array file")
    parser.add_argument("--data-root", required=True, help="parsed runs root (data_root)")
    parser.add_argument("--annotator", default=None, help="gold_annotator.json sidecar")
    parser.add_argument("--out", default=None, help="write goldcheck_report.json here")
    parser.add_argument("--min-papers", type=int, default=10)
    parser.add_argument("--min-queries", type=int, default=25)
    parser.add_argument("--min-per-paper", type=int, default=2)
    parser.add_argument("--max-per-paper", type=int, default=3)
    parser.add_argument("--min-types-per-language", type=int, default=3)
    parser.add_argument("--required-types", type=int, default=len(QUERY_TYPES))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gold_path = Path(args.gold)
    if not gold_path.is_file():
        print(f"gold file missing: {gold_path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        gold = load_gold_queries(gold_path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"gold file invalid: {exc}", file=sys.stderr)
        return EXIT_USAGE
    blocks_by_paper = load_blocks_by_paper(args.data_root)
    annotator_path = Path(args.annotator) if args.annotator else None
    report = run_goldcheck(
        gold,
        blocks_by_paper=blocks_by_paper,
        annotator_path=annotator_path,
        min_papers=args.min_papers,
        min_queries=args.min_queries,
        min_per_paper=args.min_per_paper,
        max_per_paper=args.max_per_paper,
        min_types_per_language=args.min_types_per_language,
        required_types=args.required_types,
    )
    report["gold_file"] = str(gold_path)
    report["parsed_root"] = str(Path(args.data_root).resolve())
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if not report["valid"]:
        for error in report["errors"]:
            print(f"- {error}", file=sys.stderr)
        return EXIT_VALIDATION_FAILED
    print(
        f"goldcheck passed: {report['query_count']} queries, "
        f"{report['paper_count']} papers, zh={report['zh_count']} "
        f"en={report['en_count']}",
        file=sys.stderr,
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
