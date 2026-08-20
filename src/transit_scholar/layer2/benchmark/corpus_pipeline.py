"""Production-chain corpus parser runner (FR-PARSER / FR-GOLD groundwork).

Command::

    python -m transit_scholar.layer2.benchmark.corpus_pipeline
      --corpus <pdf dir> --output <root>
      [--limit N] [--resume] [--per-paper-timeout SECONDS]

Unlike ``parser_runner`` (which invokes each parser adapter directly for
quality comparison), this runner drives the real production chain per paper:
``get_second_layer_input`` gate -> ``parse_paper`` -> Docling primary ->
validation -> MinerU whole-document fallback -> accepted run.

Isolation & rules:

- Every paper runs in a worker subprocess with ``TRANSIT_SCHOLAR_DATA_DIR``
  pointed at a git-ignored isolated root. The isolated SQLite database created
  there is a *benchmark scratch DB*; the Layer1 formal database is never
  touched, and the real PDFs are read-only (paper files reference the real
  paths, the files are not copied).
- ``corpus_manifest.json`` maps ``pdf_sha256 -> paper_id -> parse_run_id`` and
  is the input for P's gold annotation and the four-way retrieval evaluation.
- Failures/timeouts keep their records; resume skips unchanged completed units.
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

#: Test-only seams (documented): pin the parser chain and feed the fake parser.
_ENV_PARSER_OVERRIDE = "L2S1_CORPUS_PARSER_OVERRIDE"
_ENV_FAKE_ITEMS = "L2S1_CORPUS_FAKE_ITEMS"
_ENV_FAKE_PAGE_COUNT = "L2S1_CORPUS_FAKE_PAGE_COUNT"
_ENV_SLEEP = "L2S1_CORPUS_SLEEP_SECONDS"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.benchmark.corpus_pipeline",
        description=(
            "Parse every PDF in a corpus through the production parse chain "
            "(gate -> primary -> validation -> fallback) in an isolated data "
            "root and emit corpus_manifest.json for gold annotation and "
            "retrieval evaluation."
        ),
    )
    parser.add_argument("--corpus", default=None, help="directory of PDFs")
    parser.add_argument("--output", default=None, help="git-ignored output root")
    parser.add_argument("--limit", type=int, default=None, help="max papers to run in this invocation")
    parser.add_argument("--resume", action="store_true", help="resume from an existing output root")
    parser.add_argument(
        "--per-paper-timeout",
        type=float,
        default=900.0,
        help="per-paper wall timeout in seconds (default 900)",
    )
    parser.add_argument("--worker-unit", default=None, help=argparse.SUPPRESS)
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
    if args.limit is not None and args.limit <= 0:
        print("--limit must be a positive integer", file=sys.stderr)
        return EXIT_USAGE

    from transit_scholar.layer2.util import sha256_file, stable_json_hash

    pdf_records: list[dict[str, str]] = []
    for pdf in pdfs:
        pdf_records.append({"name": pdf.name, "sha256": sha256_file(pdf)})
    corpus_sha256 = stable_json_hash(
        {"corpus": sorted((r["name"], r["sha256"]) for r in pdf_records)}
    )

    units_dir = output / "units"
    units_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir = output / ".jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    state: dict[str, dict[str, Any]] = {}
    if args.resume and state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    executed = 0
    started_at = time.time()
    for pdf_record in pdf_records:
        key = f"{pdf_record['sha256'][:12]}"
        result_file = units_dir / key / "result.json"
        state_entry = state.get(key)
        if (
            args.resume
            and state_entry
            and state_entry.get("status") == "done"
            and result_file.is_file()
        ):
            state[key] = {**state_entry, "skipped": True}
            continue
        if args.limit is not None and executed >= args.limit:
            break
        executed += 1

        payload = {
            "unit_key": key,
            "pdf_name": pdf_record["name"],
            "pdf_sha256": pdf_record["sha256"],
            "pdf_path": str(corpus / pdf_record["name"]),
            "output_root": str(output),
        }
        payload_path = jobs_dir / f"{key}.json"
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
                    "transit_scholar.layer2.benchmark.corpus_pipeline",
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
                "pdf_name": pdf_record["name"],
                "pdf_sha256": pdf_record["sha256"],
                "status": "timeout",
                "error_code": "PER_PAPER_TIMEOUT",
                "error_message": f"exceeded --per-paper-timeout {args.per_paper_timeout}s",
                "wall_runtime_s": round(wall_runtime, 3),
            }
        elif not worker_ok:
            record = {
                "unit_key": key,
                "pdf_name": pdf_record["name"],
                "pdf_sha256": pdf_record["sha256"],
                "status": "error",
                "error_code": "WORKER_FAILED",
                "error_message": completed.stderr[-2000:] if completed.stderr else "worker crashed",
                "wall_runtime_s": round(wall_runtime, 3),
            }
        elif not result_file.is_file():
            record = {
                "unit_key": key,
                "pdf_name": pdf_record["name"],
                "pdf_sha256": pdf_record["sha256"],
                "status": "error",
                "error_code": "WORKER_NO_RESULT",
                "error_message": "worker exited 0 without writing result.json",
                "wall_runtime_s": round(wall_runtime, 3),
            }
        else:
            record = json.loads(result_file.read_text(encoding="utf-8"))
            record["wall_runtime_s"] = round(wall_runtime, 3)
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        state[key] = {
            "status": "done",
            "result_file": str(result_file),
            "run": state.get(key, {}).get("run", 0) + 1,
        }
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            f"[{record['pdf_name']}] status={record['status']} "
            f"parser={record.get('parser_used')} run={record.get('parse_run_id')}",
            file=sys.stderr,
        )

    # Persist skip markers from resume so state.json reflects this invocation.
    state_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    papers = []
    for pdf_record in pdf_records:
        key = f"{pdf_record['sha256'][:12]}"
        result_file = units_dir / key / "result.json"
        if not result_file.is_file():
            papers.append({"pdf_name": pdf_record["name"], "pdf_sha256": pdf_record["sha256"],
                           "status": "not_run", "paper_id": None, "parse_run_id": None})
            continue
        record = json.loads(result_file.read_text(encoding="utf-8"))
        papers.append(
            {
                "pdf_name": record.get("pdf_name", pdf_record["name"]),
                "pdf_sha256": record.get("pdf_sha256", pdf_record["sha256"]),
                "paper_id": record.get("paper_id"),
                "parse_run_id": record.get("parse_run_id"),
                "parser_used": record.get("parser_used"),
                "status": record.get("status"),
                "error_code": record.get("error_code"),
            }
        )

    corpus_manifest = {
        "format_version": "transit-scholar-layer2-corpus-pipeline-v1",
        "corpus_dir": str(corpus),
        "corpus_pdf_count": len(pdfs),
        "corpus_sha256": corpus_sha256,
        "command_args": {
            "corpus": args.corpus,
            "output": args.output,
            "limit": args.limit,
            "resume": args.resume,
            "per_paper_timeout": args.per_paper_timeout,
        },
        "started_at": _now_iso(),
        "finished_at": _now_iso(),
        "finished_after_s": round(time.time() - started_at, 3),
        "papers": papers,
    }
    (output / "corpus_manifest.json").write_text(
        json.dumps(corpus_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Worker (subprocess)
# ---------------------------------------------------------------------------


def _worker_main(payload_path: str) -> int:
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    data_root = Path(payload["output_root"]) / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    # Bind every transit_scholar singleton to the isolated root BEFORE any
    # transit_scholar import happens in this fresh process.
    os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = str(data_root)

    sleep_seconds = float(os.environ.get(_ENV_SLEEP, "0"))
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)

    from transit_scholar.config import Settings
    from transit_scholar.db.base import Base
    from transit_scholar.db.engine import SessionLocal, engine
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.util import sha256_file

    settings = Settings(data_root=data_root)
    settings.init_directories()
    Base.metadata.create_all(engine)

    pdf_path = Path(payload["pdf_path"])
    if not pdf_path.is_file():
        return _write_result(payload, {
            "unit_key": payload["unit_key"],
            "pdf_name": payload["pdf_name"],
            "pdf_sha256": payload["pdf_sha256"],
            "status": "blocked",
            "error_code": "SOURCE_FILE_MISSING",
            "error_message": "corpus PDF missing on disk",
        })
    pdf_sha256 = sha256_file(pdf_path)

    from transit_scholar.db.models import IngestionJob, Paper, PaperFile

    started = time.time()
    with SessionLocal() as session:
        paper = Paper(title=payload["pdf_name"], status="active")
        session.add(paper)
        session.flush()
        paper_file = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            relative_path=str(pdf_path.resolve()),
            mime_type="application/pdf",
        )
        session.add(paper_file)
        session.flush()
        session.add(
            IngestionJob(
                uploaded_filename=payload["pdf_name"],
                file_id=paper_file.id,
                paper_id=paper.id,
                status="accepted",
                current_stage="completed",
            )
        )
        session.commit()
        paper_id = paper.id

    config = Layer2Config.from_settings(settings)
    override = os.environ.get(_ENV_PARSER_OVERRIDE)
    if override:
        object.__setattr__(config, "parser_override", override)
    _apply_test_seams(config)

    result = parse_paper(paper_id, config=config)
    record: dict[str, Any] = {
        "unit_key": payload["unit_key"],
        "pdf_name": payload["pdf_name"],
        "pdf_sha256": pdf_sha256,
        "paper_id": paper_id,
        "parse_run_id": result.parse_run_id,
        "parser_used": result.parser_used,
        "status": result.status,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "warnings": list(result.warnings),
        "runtime_s": round(time.time() - started, 3),
    }
    return _write_result(payload, record)


def _write_result(payload: dict[str, Any], record: dict[str, Any]) -> int:
    unit_dir = Path(payload["output_root"]) / "units" / payload["unit_key"]
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "result.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0


def _apply_test_seams(config) -> None:
    """Test-only deterministic seams (documented; never used in production)."""
    if config.parser_override != "fake":
        return
    items = os.environ.get(_ENV_FAKE_ITEMS)
    if items:
        from transit_scholar.layer2.parser.base import register_adapter
        from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item

        parsed_items = [make_item(**item) for item in json.loads(items)]
        page_count = int(os.environ.get(_ENV_FAKE_PAGE_COUNT, "1"))

        def _fake_factory(cfg):
            return FakeParserAdapter(cfg, items=parsed_items, page_count=page_count)

        register_adapter("fake", _fake_factory)


def _now_iso() -> str:
    from transit_scholar.layer2.util import now_utc_iso

    return now_utc_iso()


if __name__ == "__main__":
    raise SystemExit(main())
