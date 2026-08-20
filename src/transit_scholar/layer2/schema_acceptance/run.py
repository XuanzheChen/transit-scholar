"""L2S2 Package E CLI entry (AC-E-42..46).

Usage::

    python -m transit_scholar.layer2.schema_acceptance.run \
        --gold <gold.json> \
        --storage-root <dir> \
        --output-dir <dir> \
        [--run-id <id>] \
        [--schema-id <schema_id>]

The CLI only evaluates already-persisted schema runs: it never triggers
``extract_schema``, never reads PDFs, never touches ``data/**`` and never
makes any network / LLM / Jina call. There is deliberately no extraction
switch of any kind (AC-E-43).

Exit codes (AC-E-45): 0 = report written with no error-level findings;
1 = invalid gold, or paper errors / error-level issues present (report is
still written when evaluation ran); 2 = argument / path-guard errors.

The CLI only writes under ``--output-dir`` (AC-E-46).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .evaluate import evaluate_schema_gold
from .gold import GoldLoadError, load_schema_gold, validate_schema_gold
from .report import write_acceptance_report


def _guard_output_dir(
    output_dir: Path, storage_root: Path, gold_path: Path, repo_root: Path
) -> str | None:
    """Best-effort write-protection guards (AC-E-46).

    Refuses to write when the output directory is (or contains / is
    contained by) the storage root, when it is the gold file's directory,
    the repository root, or anywhere under the repository ``data/`` tree.
    """
    out = output_dir.resolve()
    storage = storage_root.resolve()
    if out == storage or storage in out.parents or out in storage.parents:
        return (
            "output-dir must not be the same as, or contain / be contained "
            "by, the storage root"
        )
    if out == repo_root:
        return "output-dir must not be the repository root"
    if out == gold_path.resolve().parent:
        return "output-dir must not be the gold file's directory"
    data_root = (repo_root / "data").resolve()
    if out == data_root or data_root in out.parents:
        return "output-dir must not be inside the repository data/ directory"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m transit_scholar.layer2.schema_acceptance.run",
        description=(
            "Evaluate persisted L2S2 schema runs against a gold benchmark "
            "and write acceptance_report.json + acceptance_summary.md. "
            "Offline by construction: no extraction, no network, no PDF IO."
        ),
    )
    parser.add_argument("--gold", required=True, help="path to the gold JSON file")
    parser.add_argument(
        "--storage-root",
        required=True,
        help="Package D schema storage root (read-only)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="directory for acceptance_report.json and acceptance_summary.md",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="evaluate this historical run id instead of the current run",
    )
    parser.add_argument(
        "--schema-id",
        default="bus_control_rl",
        help="default schema id for storage reads (default: bus_control_rl)",
    )
    args = parser.parse_args(argv)

    gold_path = Path(args.gold)
    storage_root = Path(args.storage_root)
    output_dir = Path(args.output_dir)
    repo_root = Path.cwd().resolve()

    guard_error = _guard_output_dir(output_dir, storage_root, gold_path, repo_root)
    if guard_error:
        print(f"error: {guard_error}", file=sys.stderr)
        return 2

    try:
        gold = load_schema_gold(gold_path)
    except GoldLoadError as exc:
        print(f"error: gold load failed ({exc.error_code}): {exc}", file=sys.stderr)
        return 1

    validation_issues = validate_schema_gold(gold)
    errors = [i for i in validation_issues if i.severity == "error"]
    warnings = [i for i in validation_issues if i.severity == "warning"]
    for issue in warnings:
        print(f"gold validation warning: {issue.type}: {issue.message}")
    if errors:
        for issue in errors:
            print(f"gold validation error: {issue.type}: {issue.message}", file=sys.stderr)
        print(
            f"error: gold validation failed with {len(errors)} error(s); "
            f"no report written",
            file=sys.stderr,
        )
        return 1

    report = evaluate_schema_gold(
        gold,
        storage_root=storage_root,
        schema_id=args.schema_id,
        run_id=args.run_id,
    )
    report.gold_path = str(gold_path)

    try:
        json_path, md_path = write_acceptance_report(report, output_dir)
    except Exception as exc:  # noqa: BLE001 - CLI must report and exit cleanly
        print(f"error: could not write report: {exc}", file=sys.stderr)
        return 1

    print(f"acceptance report: {json_path}")
    print(f"acceptance summary: {md_path}")
    overall = report.metrics.overall
    print(
        f"overall: papers={overall.paper_count} fields={overall.field_count} "
        f"evaluated={overall.evaluated_field_count} "
        f"value_accuracy={overall.value_accuracy} "
        f"status_accuracy={overall.status_accuracy} "
        f"traceability={report.traceability.mode} "
        f"freeze_suggestion={report.freeze.freeze_suggestion} "
        f"issues={overall.issue_count}"
    )
    print(
        f"freeze blocks: blocking_errors={report.freeze.blocking_error_count} "
        f"diagnostics={report.freeze.diagnostic_warning_count} "
        f"gold_review={report.freeze.gold_review_count} "
        f"exact_match_diagnostics={report.freeze.exact_match_diagnostic_count} "
        f"blockers={len(report.freeze.remaining_freezing_blockers)}"
    )

    has_paper_error = any(p.paper_error is not None for p in report.papers)
    has_error_issue = any(i.severity == "error" for i in report.issues)
    if has_paper_error or has_error_issue:
        print(
            "evaluation finished with paper errors / error-level issues "
            "(exit code 1; the report has been written)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
