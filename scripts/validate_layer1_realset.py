"""Layer1 real-set acceptance validation script (AC-REALSET).

Reads a user-local manifest of real transit/rail papers and runs the
first-layer import pipeline for every entry inside an isolated data root,
then writes a machine-readable report.

Usage (from the repository root):

    python scripts/validate_layer1_realset.py
    python scripts/validate_layer1_realset.py --manifest <path> --data-root <path>

Safety contract (AC-REALSET-004):

- never modifies any source PDF under ``real_papers/``;
- never deletes, clears or rebuilds ``data/stage7_acceptance/`` (or any
  other data root);
- writes only inside ``<data-root>/library/``, ``<data-root>/database/`` and
  ``<data-root>/realset_reports/``;
- real PDFs are never copied into any Git-tracked directory.

Exit code is always 0 for expected outcomes, including per-entry failures,
a missing/invalid manifest and an empty manifest — each of which produces a
clear message instead of a traceback.

The module deliberately imports ``transit_scholar`` lazily inside the run
functions: a standalone invocation points ``TRANSIT_SCHOLAR_DATA_DIR`` at the
chosen data root before the package is first imported, while in-process
callers (tests) keep the already-bound engine and settings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST = "data/stage7_acceptance/real_papers/manifest.json"
DEFAULT_DATA_ROOT = "data/stage7_acceptance"
DEFAULT_OUTPUT_DIR_NAME = "realset_reports"

# Frozen per-entry fields (AC-REALSET-03). ``status`` marks the high-level
# outcome (including schema_error / missing_file), ``import_status`` mirrors
# the pipeline import outcome vocabulary.
REPORT_ENTRY_FIELDS = (
    "id",
    "file",
    "status",
    "import_status",
    "current_stage",
    "fields_present",
    "metadata_quality_flags",
    "second_layer_ready",
    "second_layer_blockers",
    "duplicate_relation_summary",
    "trace",
    "gold_diff",
    "error_code",
    "error_message",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="validate_layer1_realset",
        description=(
            "Run the Layer1 real-set validation: import every PDF listed in a "
            "manifest inside an isolated data root and produce report.json / "
            "report.txt. Source PDFs are never modified."
        ),
    )
    parser.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST,
        help="path to the manifest.json (default: %(default)s)",
    )
    parser.add_argument(
        "--data-root",
        default=DEFAULT_DATA_ROOT,
        help="isolated data root for imports (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="report output directory (default: <data-root>/realset_reports)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Always returns exit code 0 for expected outcomes."""
    args = _parse_args(argv)
    data_root = Path(args.data_root).resolve()
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else data_root / DEFAULT_OUTPUT_DIR_NAME
    )
    manifest_path = Path(args.manifest)

    # Point the package at the isolated data root BEFORE any transit_scholar
    # import so the engine/database bind to the right tree in standalone runs.
    os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = str(data_root)

    report = run_realset_validation(
        manifest_path=manifest_path,
        data_root=data_root,
        output_dir=output_dir,
    )
    if report is None:
        # A clear stderr message was already emitted for the missing/invalid
        # manifest; expected outcome, exit 0 without writing a report.
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.txt").write_text(
        _render_report_txt(report), encoding="utf-8"
    )
    return 0


def run_realset_validation(
    *,
    manifest_path: str | Path,
    data_root: str | Path,
    output_dir: str | Path,
) -> dict[str, Any] | None:
    """Run the real-set validation and return the report dict.

    Returns ``None`` when the manifest directory/file is missing or the JSON
    is invalid (a clear message is printed to stderr); every other path,
    including an empty manifest, returns a report dict. In-process callers
    keep the already-imported engine/settings; the global ``settings.data_root``
    is pointed at ``data_root`` so files and database land in the isolated tree.
    """
    # Import lazily: standalone runs need TRANSIT_SCHOLAR_DATA_DIR set first.
    from transit_scholar.config import settings
    from transit_scholar.db.lifecycle import alembic_upgrade_head

    settings.data_root = Path(data_root)
    settings.init_directories()

    manifest = Path(manifest_path)
    if not manifest.is_file():
        print(
            f"Real-set manifest not found: {manifest} "
            "(no entries validated; nothing was modified)",
            file=sys.stderr,
        )
        return None

    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"Real-set manifest is not valid JSON: {manifest}: {exc} "
            "(no entries validated; nothing was modified)",
            file=sys.stderr,
        )
        return None

    if not isinstance(raw, list):
        print(
            f"Real-set manifest root must be a JSON array: {manifest} "
            "(no entries validated; nothing was modified)",
            file=sys.stderr,
        )
        return None

    # Make sure the isolated data root has a schema at Alembic head before any
    # import runs. Idempotent: an existing database is simply verified.
    alembic_upgrade_head()

    entries: list[dict[str, Any]] = []
    summary = {
        "total": len(raw),
        "imported": 0,
        "duplicate": 0,
        "failed": 0,
        "missing_file": 0,
        "schema_error": 0,
    }
    if not raw:
        print(
            "Real-set manifest contains no entries; wrote an empty report.",
            file=sys.stderr,
        )
    else:
        manifest_dir = manifest.parent
        for index, entry in enumerate(raw):
            report_entry, status = _process_manifest_entry(
                entry, index=index, manifest_dir=manifest_dir
            )
            entries.append(report_entry)
            if status in summary:
                summary[status] += 1

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest),
        "data_root": str(Path(data_root)),
        "output_dir": str(Path(output_dir)),
        "summary": summary,
        "entries": entries,
    }


def _process_manifest_entry(
    entry: Any, *, index: int, manifest_dir: Path
) -> tuple[dict[str, Any], str]:
    """Validate one manifest entry and process it (AC-REALSET-02/03).

    Returns ``(report_entry, summary_status)``. A schema-error entry is marked
    and skipped; a missing source file is marked ``missing_file``; everything
    else runs ``run_import_pipeline()`` and never crashes the whole run.
    """
    from transit_scholar.workflow.service import (
        get_paper,
        run_import_pipeline,
    )

    entry_id = entry.get("id") if isinstance(entry, dict) else None
    file_rel = entry.get("file") if isinstance(entry, dict) else None
    if (
        not isinstance(entry, dict)
        or not isinstance(entry_id, str)
        or not entry_id.strip()
        or not isinstance(file_rel, str)
        or not file_rel.strip()
    ):
        return _entry_base(
            entry_id=str(entry_id or f"<entry {index}>"),
            file=file_rel if isinstance(file_rel, str) else "",
            status="schema_error",
            error_code="MANIFEST_SCHEMA_ERROR",
            error_message=(
                "manifest entry missing required string fields id and/or file"
            ),
        ), "schema_error"

    pdf_path = manifest_dir / file_rel
    if not pdf_path.is_file():
        return _entry_base(
            entry_id=entry_id,
            file=file_rel,
            status="missing_file",
            error_code=None,
            error_message=None,
            trace=f"not imported: source file missing on disk: {pdf_path}",
        ), "missing_file"

    try:
        result = run_import_pipeline(pdf_path)
    except Exception as exc:  # noqa: BLE001 — per-entry isolation
        return _entry_base(
            entry_id=entry_id,
            file=file_rel,
            status="failed",
            import_status="failed",
            error_code="ENTRY_FAILED",
            error_message=f"pipeline raised: {exc}",
            trace=f"pipeline raised: {type(exc).__name__}",
        ), "failed"

    paper = get_paper(result.paper_id) if result.paper_id else None
    status, import_status = _status_map(result.status)

    entry_base = _entry_base(
        entry_id=entry_id,
        file=file_rel,
        status=status,
        import_status=import_status,
        current_stage=result.current_stage,
        error_code=result.error_code,
        error_message=result.error_message,
        metadata_quality_flags=list(result.metadata_quality_flags),
        second_layer_ready=result.second_layer_ready,
        second_layer_blockers=list(result.second_layer_blockers),
        duplicate_relation_summary=_relation_summary(paper),
        fields_present=_fields_present(paper),
        gold_diff=_gold_diff(entry, paper),
        trace=_trace_summary(result, paper),
    )
    return entry_base, status


def _entry_base(
    *,
    entry_id: str,
    file: str,
    status: str,
    import_status: str | None = None,
    current_stage: str | None = None,
    fields_present: dict[str, bool] | None = None,
    metadata_quality_flags: list[str] | None = None,
    second_layer_ready: bool = False,
    second_layer_blockers: list[str] | None = None,
    duplicate_relation_summary: list[dict[str, Any]] | None = None,
    trace: str = "",
    gold_diff: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Build a report entry with every frozen field present (AC-REALSET-03)."""
    return {
        "id": entry_id,
        "file": file,
        "status": status,
        "import_status": import_status,
        "current_stage": current_stage,
        "fields_present": fields_present
        or {"title": False, "authors": False, "year": False, "doi": False,
            "arxiv": False, "abstract": False, "venue": False},
        "metadata_quality_flags": list(metadata_quality_flags or []),
        "second_layer_ready": second_layer_ready,
        "second_layer_blockers": list(second_layer_blockers or []),
        "duplicate_relation_summary": list(duplicate_relation_summary or []),
        "trace": trace or "not processed",
        "gold_diff": gold_diff or {},
        "error_code": error_code,
        "error_message": error_message,
    }


def _status_map(pipeline_status: str) -> tuple[str, str]:
    """Map a pipeline status to (report status, import_status)."""
    if pipeline_status == "duplicate":
        return "duplicate", "duplicate"
    if pipeline_status == "failed":
        return "failed", "failed"
    # completed / partial / awaiting_user_review: the file itself was imported.
    return "imported", "accepted"


def _fields_present(paper) -> dict[str, bool]:
    """Which metadata fields the paper currently carries (AC-REALSET-03)."""
    if paper is None:
        return {"title": False, "authors": False, "year": False, "doi": False,
                "arxiv": False, "abstract": False, "venue": False}
    return {
        "title": bool(paper.title and paper.title.strip()),
        "authors": bool(paper.authors),
        "year": paper.publication_year is not None,
        "doi": bool(paper.doi or paper.normalized_doi),
        "arxiv": bool(paper.arxiv_id and paper.arxiv_id.strip()),
        "abstract": bool(paper.abstract and paper.abstract.strip()),
        "venue": bool(paper.venue and paper.venue.strip()),
    }


def _relation_summary(paper) -> list[dict[str, Any]]:
    """Per-paper duplicate relation summary with type/status/confidence."""
    if paper is None:
        return []
    return [
        {
            "relation_id": r["relation_id"],
            "relation_type": r["relation_type"],
            "status": r["status"],
            "confidence": r["confidence"],
        }
        for r in paper.duplicate_relations
    ]


def _gold_diff(entry: dict[str, Any], paper) -> dict[str, Any]:
    """Diff of user-provided gold fields vs the system state (AC-REALSET-03).

    Only fields with a non-empty expected value are compared; ``null``/empty
    expected values are skipped. Authors compare as sets of names.
    """
    if paper is None:
        return {}
    diff: dict[str, Any] = {}

    expected_title = entry.get("expected_title")
    if expected_title is not None and str(expected_title).strip():
        expected = str(expected_title).strip()
        actual = (paper.title or "").strip()
        if actual != expected:
            diff["title"] = {"expected": expected, "actual": paper.title}

    expected_doi = entry.get("expected_doi")
    if expected_doi is not None and str(expected_doi).strip():
        expected = str(expected_doi).strip()
        actual = (paper.doi or paper.normalized_doi or "").strip()
        if actual != expected:
            diff["doi"] = {"expected": expected, "actual": paper.doi}

    expected_authors = entry.get("expected_authors")
    if isinstance(expected_authors, list) and expected_authors:
        expected = {str(name).strip() for name in expected_authors if str(name).strip()}
        actual = {
            a["full_name"].strip()
            for a in paper.authors
            if a.get("full_name") and a["full_name"].strip()
        }
        if actual != expected:
            diff["authors"] = {
                "expected": sorted(expected),
                "actual": sorted(actual),
            }

    expected_year = entry.get("expected_year")
    if expected_year is not None and str(expected_year).strip():
        try:
            expected_year_int = int(expected_year)
        except (TypeError, ValueError):
            expected_year_int = None
        if expected_year_int is not None and paper.publication_year != expected_year_int:
            diff["year"] = {
                "expected": expected_year_int,
                "actual": paper.publication_year,
            }

    return diff


def _trace_summary(result, paper) -> str:
    """A non-empty trace summary: rich trace service view when available."""
    if paper is None:
        return (
            f"pipeline:job={result.job_id}:stage={result.current_stage}:"
            f"status={result.status}"
        )
    try:
        from transit_scholar.workflow.trace import get_paper_trace

        trace = get_paper_trace(paper.paper_id)
        if trace is not None:
            gate_status = trace.second_layer_gate.get("status", "unknown")
            return (
                f"trace:paper={trace.paper_id}:steps={len(trace.steps)}:"
                f"gate={gate_status}:jobs={len(trace.ingestion_jobs)}:"
                f"candidates={trace.metadata_summary.total_candidates}"
            )
    except Exception:  # noqa: BLE001 — the report must never fail on trace
        pass
    return (
        f"pipeline:paper={result.paper_id}:job={result.job_id}:"
        f"stage={result.current_stage}:status={result.status}"
    )


def _render_report_txt(report: dict[str, Any]) -> str:
    """Human-readable rendering of the report dict (AC-REALSET-01)."""
    lines = [
        "TransitScholar Layer1 real-set validation report",
        f"Generated at (UTC): {report['generated_at_utc']}",
        f"Manifest: {report['manifest']}",
        f"Data root: {report['data_root']}",
        f"Output dir: {report['output_dir']}",
        "",
        f"Total entries: {report['summary']['total']}",
        (
            f"  imported={report['summary']['imported']} "
            f"duplicate={report['summary']['duplicate']} "
            f"failed={report['summary']['failed']} "
            f"missing_file={report['summary']['missing_file']} "
            f"schema_error={report['summary']['schema_error']}"
        ),
        "",
    ]
    for entry in report["entries"]:
        lines.append(
            f"[{entry['id']}] {entry['status']}"
            f" (import={entry['import_status']}, stage={entry['current_stage']})"
        )
        lines.append(f"  file: {entry['file']}")
        present = entry["fields_present"]
        lines.append(
            "  fields_present: "
            + ", ".join(
                f"{name}={'yes' if present.get(name) else 'no'}"
                for name in ("title", "authors", "year", "doi", "arxiv", "abstract", "venue")
            )
        )
        lines.append(f"  metadata_quality_flags: {entry['metadata_quality_flags']}")
        lines.append(
            f"  second_layer_ready: {entry['second_layer_ready']} "
            f"blockers: {entry['second_layer_blockers']}"
        )
        lines.append(f"  duplicate_relation_summary: {entry['duplicate_relation_summary']}")
        lines.append(f"  trace: {entry['trace']}")
        lines.append(f"  gold_diff: {entry['gold_diff']}")
        lines.append(f"  error: {entry['error_code']} {entry['error_message'] or ''}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
