"""Parser corpus benchmark runner (FR-PARSER-001/002).

Command::

    python -m transit_scholar.layer2.benchmark.parser_runner
      --corpus <pdf dir> --output <root>
      [--parsers docling,mineru,pymupdf4llm]
      [--limit N] [--resume] [--per-paper-timeout SECONDS]

Exit codes:

- ``0``  every scheduled unit was processed (recorded failures/timeouts are
         part of the report, not runner-level errors);
- ``2``  corpus directory missing;
- ``3``  output root not writable;
- ``4``  invalid arguments / unknown parser name.

Isolation & correctness:

- Each (paper, parser) unit runs in a fresh worker subprocess so a hung unit
  can be killed by the parent timeout without breaking the batch (Windows-safe).
- The worker calls the parser *adapter directly* -- never ``parse_paper``'s
  fallback chain -- so the three parser results are never mixed.
- ``state.json`` records every unit key; ``--resume`` skips units whose input
  and config hash are unchanged, and re-runs anything missing/corrupted.
- Failures and timeouts stay in ``per_paper.jsonl`` and enter the failure-rate
  denominator; nothing is silently dropped.
- Real PDFs are read-only. All artifacts live under the git-ignored output root.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_CORPUS_MISSING = 2
EXIT_OUTPUT_NOT_WRITABLE = 3
EXIT_USAGE = 4

#: Test-only worker seams (documented; never used by production runners).
_ENV_FAKE_ITEMS = "L2S1_BENCH_FAKE_ITEMS"
_ENV_FAKE_PAGE_COUNT = "L2S1_BENCH_FAKE_PAGE_COUNT"
_ENV_SLEEP = "L2S1_BENCH_SLEEP_SECONDS"
_ENV_FAIL = "L2S1_BENCH_FAIL"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.benchmark.parser_runner",
        description=(
            "Run a per-(paper, parser) PDF parse benchmark over a corpus of "
            "real PDFs. Parsers are invoked directly (no fallback mixing); "
            "every unit is isolated in a subprocess and results are resumable."
        ),
    )
    parser.add_argument("--corpus", default=None, help="directory of PDFs")
    parser.add_argument("--output", default=None, help="git-ignored output root")
    parser.add_argument(
        "--parsers",
        default="docling,mineru,pymupdf4llm",
        help="comma-separated parser names (default: docling,mineru,pymupdf4llm)",
    )
    parser.add_argument("--limit", type=int, default=None, help="max units to run in this invocation")
    parser.add_argument("--resume", action="store_true", help="resume from an existing output root")
    parser.add_argument(
        "--per-paper-timeout",
        type=float,
        default=900.0,
        help="per-unit wall timeout in seconds (default 900)",
    )
    parser.add_argument(
        "--worker-unit",
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "worker_unit", None):
        return _worker_main(args.worker_unit)
    return _parent_main(args)


def _parent_main(args: argparse.Namespace) -> int:
    if not args.corpus or not args.output:
        print("--corpus and --output are required", file=sys.stderr)
        return EXIT_USAGE
    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"corpus directory missing: {corpus}", file=sys.stderr)
        return EXIT_CORPUS_MISSING

    pdfs = sorted(p for p in corpus.iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        print(f"no PDF files found in corpus: {corpus}", file=sys.stderr)
        return EXIT_USAGE

    output = Path(args.output)
    try:
        output.mkdir(parents=True, exist_ok=True)
        probe = output / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        print(f"output root not writable: {output}: {exc}", file=sys.stderr)
        return EXIT_OUTPUT_NOT_WRITABLE

    parser_names = [name.strip() for name in args.parsers.split(",") if name.strip()]
    if not parser_names:
        print("--parsers must name at least one parser", file=sys.stderr)
        return EXIT_USAGE
    if args.limit is not None and args.limit <= 0:
        print("--limit must be a positive integer", file=sys.stderr)
        return EXIT_USAGE

    from transit_scholar.layer2.benchmark.quality import (
        complete_failure_record,
        validate_unit_record,
    )
    from transit_scholar.layer2.util import sha256_file, stable_json_hash

    pdf_records: list[dict[str, str]] = []
    for pdf in pdfs:
        pdf_records.append({"name": pdf.name, "sha256": sha256_file(pdf)})
    corpus_sha256 = stable_json_hash(
        {"corpus": sorted((r["name"], r["sha256"]) for r in pdf_records)}
    )

    adapters = _build_adapters(parser_names)
    unknown = [name for name in parser_names if name not in adapters]
    if unknown:
        print(f"unknown parser(s): {', '.join(unknown)}", file=sys.stderr)
        return EXIT_USAGE

    units_dir = output / "units"
    units_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir = output / ".jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    state_path = output / "state.json"
    state: dict[str, dict[str, Any]] = {}
    if args.resume and state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    planned: list[dict[str, Any]] = []
    for pdf_record in pdf_records:
        for parser_name in parser_names:
            adapter = adapters[parser_name]
            unit_key = _unit_key(pdf_record["sha256"], parser_name, adapter)
            planned.append(
                {
                    "unit_key": unit_key,
                    "unit_dir_key": _unit_dir_key(unit_key),
                    "pdf_name": pdf_record["name"],
                    "pdf_sha256": pdf_record["sha256"],
                    "pdf_path": str(corpus / pdf_record["name"]),
                    "parser_name": parser_name,
                    "parser_version": adapter.version,
                    "parser_config_hash": adapter.config_hash,
                }
            )

    per_paper_path = output / "per_paper.jsonl"
    existing_records = _load_jsonl(per_paper_path) if per_paper_path.is_file() else []

    executed = 0
    started_at = time.time()
    unit_count = len(planned)
    done_units: list[dict[str, Any]] = []
    for unit in planned:
        key = unit["unit_key"]
        dir_key = unit["unit_dir_key"]
        state_entry = state.get(key)
        result_file = units_dir / dir_key / "result.json"
        if (
            args.resume
            and state_entry
            and state_entry.get("status") == "done"
            and result_file.is_file()
        ):
            state[key] = {**state_entry, "run": state_entry.get("run", 0), "skipped": True}
            continue
        if args.limit is not None and executed >= args.limit:
            break
        executed += 1

        payload = {
            **unit,
            "output_root": str(output),
        }
        payload_path = jobs_dir / f"{dir_key}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        unit_started = time.time()
        timeout_hit = False
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "transit_scholar.layer2.benchmark.parser_runner",
                    "--worker-unit",
                    str(payload_path),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=args.per_paper_timeout,
            )
            worker_ok = completed.returncode == 0
        except subprocess.TimeoutExpired as exc:
            timeout_hit = True
            worker_ok = False
            try:
                exc.kill()
            except Exception:  # noqa: BLE001 - already dead
                pass
        wall_runtime = time.time() - unit_started

        if timeout_hit:
            record: dict[str, Any] = {
                "unit_key": key,
                "unit_dir_key": dir_key,
                "pdf_name": unit["pdf_name"],
                "pdf_sha256": unit["pdf_sha256"],
                "parser_name": unit["parser_name"],
                "parser_version": unit["parser_version"],
                "parser_config_hash": unit["parser_config_hash"],
                "status": "timeout",
                "error_code": "PER_UNIT_TIMEOUT",
                "error_message": (
                    f"unit exceeded --per-paper-timeout "
                    f"{args.per_paper_timeout}s"
                ),
                "runtime_s": round(wall_runtime, 3),
                "wall_runtime_s": round(wall_runtime, 3),
                "artifact_dir": str(units_dir / dir_key),
            }
            record = complete_failure_record(record)
            result_file.parent.mkdir(parents=True, exist_ok=True)
            result_file.write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        elif not worker_ok:
            record = {
                "unit_key": key,
                "pdf_name": unit["pdf_name"],
                "pdf_sha256": unit["pdf_sha256"],
                "parser_name": unit["parser_name"],
                "parser_version": unit["parser_version"],
                "parser_config_hash": unit["parser_config_hash"],
                "status": "error",
                "error_code": "WORKER_FAILED",
                "error_message": (
                    completed.stderr[-2000:] if completed.stderr else "worker crashed"
                ),
                "runtime_s": round(wall_runtime, 3),
                "wall_runtime_s": round(wall_runtime, 3),
                "artifact_dir": str(units_dir / dir_key),
            }
            record = complete_failure_record(record)
            result_file.parent.mkdir(parents=True, exist_ok=True)
            result_file.write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        else:
            if not result_file.is_file():
                record = {
                    "unit_key": key,
                    "pdf_name": unit["pdf_name"],
                    "pdf_sha256": unit["pdf_sha256"],
                    "parser_name": unit["parser_name"],
                    "parser_version": unit["parser_version"],
                    "parser_config_hash": unit["parser_config_hash"],
                    "status": "error",
                    "error_code": "WORKER_NO_RESULT",
                    "error_message": "worker exited 0 without writing result.json",
                    "runtime_s": round(wall_runtime, 3),
                    "wall_runtime_s": round(wall_runtime, 3),
                    "artifact_dir": str(units_dir / dir_key),
                }
                record = complete_failure_record(record)
                result_file.parent.mkdir(parents=True, exist_ok=True)
                result_file.write_text(
                    json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            else:
                record = json.loads(result_file.read_text(encoding="utf-8"))
                record["wall_runtime_s"] = round(wall_runtime, 3)

        state[key] = {
            "status": "done",
            "result_file": str(result_file),
            "run": state.get(key, {}).get("run", 0) + 1,
        }
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        done_units.append(record)

        violations = validate_unit_record(record)
        print(
            f"[{record['pdf_name']}] {record['parser_name']}: "
            f"{record['status']} ({record.get('runtime_s', 0):.1f}s)"
            + (f" violations={violations}" if violations else ""),
            file=sys.stderr,
        )

    # Merge newly completed records with previously persisted ones (resume).
    merged = {r["unit_key"]: r for r in existing_records}
    for record in done_units:
        merged[record["unit_key"]] = record
    ordered = [
        merged[unit["unit_key"]] for unit in planned if unit["unit_key"] in merged
    ]

    with per_paper_path.open("w", encoding="utf-8") as handle:
        for record in ordered:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    aggregate = _aggregate(ordered)
    (output / "aggregate.json").write_text(
        json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    manifest = {
        "format_version": "transit-scholar-layer2-parser-benchmark-v1",
        "corpus_dir": str(corpus),
        "corpus_pdf_count": len(pdfs),
        "corpus_sha256": corpus_sha256,
        "corpus_files": pdf_records,
        "parsers": [
            {
                "name": name,
                "version": adapters[name].version,
                "config_hash": adapters[name].config_hash,
            }
            for name in parser_names
        ],
        "command_args": {
            "corpus": args.corpus,
            "output": args.output,
            "parsers": args.parsers,
            "limit": args.limit,
            "resume": args.resume,
            "per_paper_timeout": args.per_paper_timeout,
        },
        "unit_count": unit_count,
        "executed_count": executed,
        "resume_skipped": unit_count - executed,
        "exit_code_meaning": {
            "0": "all scheduled units processed (recorded failures/timeouts included)",
            "2": "corpus missing",
            "3": "output not writable",
            "4": "invalid arguments",
        },
        "started_at": _now_iso(),
        "finished_at": _now_iso(),
        "finished_after_s": round(time.time() - started_at, 3),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Worker (subprocess)
# ---------------------------------------------------------------------------


def _worker_main(payload_path: str) -> int:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    # Isolated data root BEFORE any transit_scholar import so the settings /
    # engine singletons never bind to the formal tree.
    data_root = Path(payload["output_root"]) / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = str(data_root)

    from transit_scholar.config import Settings
    from transit_scholar.layer2.benchmark.quality import (
        complete_failure_record,
        validate_unit_record,
    )
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.parser.base import get_adapter_factory
    from transit_scholar.layer2.util import sha256_file

    sleep_seconds = float(os.environ.get(_ENV_SLEEP, "0"))
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)

    config = Layer2Config.from_settings(Settings(data_root=data_root))
    factory = get_adapter_factory(payload["parser_name"])
    if factory is None:
        return _worker_error(payload, "UNKNOWN_PARSER", "parser not registered")
    adapter = factory(config)

    availability = adapter.availability()
    if not availability.available:
        return _worker_error(
            payload,
            "DEPENDENCY_MISSING",
            availability.reason or "parser unavailable",
            status="dependency_missing",
        )

    _apply_test_seams(adapter, payload["parser_name"])

    pdf_path = Path(payload["pdf_path"])
    pdf_sha256 = sha256_file(pdf_path)
    started = time.time()
    try:
        result = adapter.parse(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 - structured worker failure
        return _worker_error(
            payload, "ADAPTER_PARSE_RAISED", f"{type(exc).__name__}: {exc}", status="error"
        )
    parse_runtime = time.time() - started

    record: dict[str, Any] = {
        "unit_key": payload["unit_key"],
        "unit_dir_key": payload["unit_dir_key"],
        "pdf_name": payload["pdf_name"],
        "pdf_sha256": pdf_sha256,
        "parser_name": payload["parser_name"],
        "parser_version": payload["parser_version"],
        "parser_config_hash": payload["parser_config_hash"],
        "status": "failed",
        "error_code": None,
        "error_message": None,
        "warnings": list(result.warnings),
        "runtime_s": round(parse_runtime, 3),
        "artifact_dir": str(Path(payload["output_root"]) / "units" / payload["unit_dir_key"] / "artifacts"),
    }

    if result.status != "ok":
        record["status"] = result.status if result.status in ("dependency_missing",) else "error"
        record["error_code"] = result.error_code or "PARSER_FAILED"
        record["error_message"] = result.error_message or f"parser status {result.status}"
        return _worker_write(payload, complete_failure_record(record))

    from transit_scholar.layer2.markdown import MarkdownRenderer
    from transit_scholar.layer2.normalizer import Normalizer
    from transit_scholar.layer2.schema import CanonicalDocument
    from transit_scholar.layer2.validation import ParseValidator

    paper_id = f"bench_{payload['unit_key']}"
    file_id = f"benchfile_{payload['unit_key']}"
    run_id = f"bench_run_{payload['unit_key']}"

    normalized = Normalizer(config).normalize(
        result,
        paper_id=paper_id,
        file_id=file_id,
        source_sha256=pdf_sha256,
        parse_run_id=run_id,
        page_heights=_page_heights(pdf_path),
        created_at=_now_iso(),
    )
    validation = ParseValidator(config).validate(
        result, normalized.document, normalized.sections, normalized.blocks
    )
    normalized.document.parse_status = validation.status

    from transit_scholar.layer2.benchmark.quality import compute_unit_quality

    quality = compute_unit_quality(
        result,
        normalized.document,
        normalized.sections,
        normalized.blocks,
        validation,
        result.page_count,
        config,
    )
    record.update(quality)
    record["status"] = validation.status
    record["warnings"] = list(record.get("warnings") or []) + list(validation.warnings)

    artifact_dir = Path(payload["output_root"]) / "units" / payload["unit_dir_key"] / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_unit_artifacts(config, artifact_dir, paper_id, run_id, normalized)

    manifest = {
        "format_version": "transit-scholar-layer2-parser-benchmark-unit-v1",
        "paper_id": paper_id,
        "file_id": file_id,
        "parse_run_id": run_id,
        "source_sha256": pdf_sha256,
        "requested_parser": {
            "name": payload["parser_name"],
            "version": payload["parser_version"],
            "config_hash": payload["parser_config_hash"],
        },
        "actual_parser_config": result.info.config if result.info else {},
        "actual_parser_config_hash": result.info.config_hash if result.info else None,
        "parse_status": validation.status,
        "created_at": _now_iso(),
    }
    (artifact_dir / "parser_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    violations = validate_unit_record(record)
    if violations:
        record["status"] = "error"
        record["error_code"] = "RECORD_VALIDATION_FAILED"
        record["error_message"] = "; ".join(violations)
    return _worker_write(payload, record)


def _worker_error(
    payload: dict[str, Any], error_code: str, message: str, *, status: str = "error"
) -> int:
    record: dict[str, Any] = {
        "unit_key": payload["unit_key"],
        "unit_dir_key": payload["unit_dir_key"],
        "pdf_name": payload["pdf_name"],
        "pdf_sha256": payload["pdf_sha256"],
        "parser_name": payload["parser_name"],
        "parser_version": payload["parser_version"],
        "parser_config_hash": payload["parser_config_hash"],
        "status": status,
        "error_code": error_code,
        "error_message": message,
        "warnings": [],
        "runtime_s": 0.0,
        "artifact_dir": str(Path(payload["output_root"]) / "units" / payload["unit_dir_key"] / "artifacts"),
    }
    return _worker_write(payload, complete_failure_record(record))


def _worker_write(payload: dict[str, Any], record: dict[str, Any]) -> int:
    unit_dir = Path(payload["output_root"]) / "units" / payload["unit_dir_key"]
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "result.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0


def _apply_test_seams(adapter, parser_name: str) -> None:
    """Test-only deterministic seams for the fake parser (documented)."""
    if parser_name != "fake":
        return
    fail = os.environ.get(_ENV_FAIL)
    if fail == "error":
        adapter._status = "error"
        adapter._error_code = "SEAM_ERROR"
        adapter._error_message = "test seam forced error"
    items = os.environ.get(_ENV_FAKE_ITEMS)
    if items:
        from transit_scholar.layer2.parser.fake import make_item

        adapter._items = [make_item(**item) for item in json.loads(items)]
    page_count = os.environ.get(_ENV_FAKE_PAGE_COUNT)
    if page_count:
        adapter._page_count = int(page_count)


def _write_unit_artifacts(config, artifact_dir: Path, paper_id: str, run_id: str, normalized) -> None:
    import json as _json

    from transit_scholar.layer2.chunker import ChunkBuilder
    from transit_scholar.layer2.markdown import MarkdownRenderer

    normalized.document.section_count = len(normalized.sections)
    normalized.document.block_count = len(normalized.blocks)
    (artifact_dir / "document.json").write_text(
        _json.dumps(normalized.document.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "sections.json").write_text(
        _json.dumps([s.to_dict() for s in normalized.sections], indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "blocks.jsonl").write_text(
        "\n".join(_json.dumps(b.to_dict(), ensure_ascii=False) for b in normalized.blocks)
        + ("\n" if normalized.blocks else ""),
        encoding="utf-8",
    )
    markdown = MarkdownRenderer(config).render(
        normalized.blocks, normalized.sections, run_id
    )
    (artifact_dir / "paper.md").write_text(markdown.text, encoding="utf-8")
    (artifact_dir / "markdown_map.jsonl").write_text(
        "\n".join(_json.dumps(e.to_dict(), ensure_ascii=False) for e in markdown.entries)
        + ("\n" if markdown.entries else ""),
        encoding="utf-8",
    )
    chunks = ChunkBuilder(config).build(
        normalized.blocks,
        normalized.sections,
        paper_id=paper_id,
        parse_run_id=run_id,
    )
    (artifact_dir / "retrieval_chunks.jsonl").write_text(
        "\n".join(_json.dumps(c.to_dict(), ensure_ascii=False) for c in chunks)
        + ("\n" if chunks else ""),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_adapters(parser_names: list[str]) -> dict[str, Any]:
    from transit_scholar.config import Settings
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.parser.base import get_adapter_factory

    config = Layer2Config.from_settings(Settings(data_root="data"))
    adapters: dict[str, Any] = {}
    for name in parser_names:
        factory = get_adapter_factory(name)
        if factory is not None:
            adapters[name] = factory(config)
    return adapters


def _unit_key(pdf_sha256: str, parser_name: str, adapter) -> str:
    return (
        f"{pdf_sha256[:12]}|{parser_name}|"
        f"{adapter.version}|{adapter.config_hash[:12]}"
    )


def _unit_dir_key(unit_key: str) -> str:
    """Filesystem-safe digest of the composite unit key (``|`` is illegal in
    Windows file names)."""
    import hashlib

    return hashlib.sha256(unit_key.encode("utf-8")).hexdigest()[:20]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_parser: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_parser.setdefault(record["parser_name"], []).append(record)

    aggregate: dict[str, Any] = {"parsers": {}, "unit_total": len(records)}
    for parser_name, units in sorted(by_parser.items()):
        success = sum(1 for u in units if u["status"] in ("passed", "degraded"))
        failed = sum(1 for u in units if u["status"] in ("failed", "error"))
        timeout = sum(1 for u in units if u["status"] == "timeout")
        dependency = sum(1 for u in units if u["status"] == "dependency_missing")
        failure_rate = (failed + timeout + dependency) / len(units) if units else 0.0
        runtimes = [u.get("runtime_s", 0) for u in units if isinstance(u.get("runtime_s", 0), (int, float))]
        ratios = ["meaningful_page_ratio", "replacement_char_ratio", "duplicate_ratio",
                  "provenance_page_coverage", "bbox_coverage", "caption_relation_completeness"]
        means = {}
        for ratio in ratios:
            values = [u[ratio] for u in units if isinstance(u.get(ratio), (int, float))]
            means[f"mean_{ratio}"] = round(sum(values) / len(values), 6) if values else None
        aggregate["parsers"][parser_name] = {
            "N": len(units),
            "success": success,
            "failed": failed,
            "timeout": timeout,
            "dependency_missing": dependency,
            "failure_rate": round(failure_rate, 6),
            "mean_runtime_s": round(sum(runtimes) / len(runtimes), 3) if runtimes else None,
            "max_runtime_s": round(max(runtimes), 3) if runtimes else None,
            **means,
        }
    return aggregate


def _page_heights(pdf_path: Path) -> dict[int, float]:
    try:
        import fitz

        document = fitz.open(pdf_path)
        try:
            return {
                index + 1: document[index].rect.height
                for index in range(document.page_count)
            }
        finally:
            document.close()
    except Exception:  # noqa: BLE001 - optional geometry hint only
        return {}


def _now_iso() -> str:
    from transit_scholar.layer2.util import now_utc_iso

    return now_utc_iso()


if __name__ == "__main__":
    raise SystemExit(main())
