"""Required real-PDF smoke for Layer2 Step1 (AC-L2S1-REALPDF-*).

Usage (from the repository root):

    python -m transit_scholar.layer2.smoke --real-papers data/stage7_acceptance/real_papers --limit 2

Behaviour:

- Points ``TRANSIT_SCHOLAR_DATA_DIR`` at an isolated smoke root
  (``data/stage7_acceptance/layer2_smoke`` by default) BEFORE importing the
  package, so the Layer1 DB and all Layer2 outputs stay out of the real
  ``data/`` tree.
- Constructs explicit Layer1-ready records for the first ``--limit`` PDFs in
  ``--real-papers`` (no copy, no import pipeline, no modification of the
  originals), then verifies each through the real ``get_second_layer_input``.
- Probes Docling/MinerU/PyMuPDF4LLM and truthfully records their real
  availability; parsing runs through a pinned parser (default the
  always-available ``pymupdf_native`` (fitz) adapter) so the heavy installed
  docling/mineru pipelines are never pulled in automatically.
- Runs grep / BM25 queries. With ``--allow-network``, a configured Jina key
  must also build dense vectors and complete dense / hybrid retrieval.
- Exit code 0 when >= 2 papers complete the flow; otherwise non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_REAL_PAPERS = "data/stage7_acceptance/real_papers"
DEFAULT_SMOKE_ROOT = "data/stage7_acceptance/layer2_smoke"

#: Queries run per real paper (title words + domain terms + a Chinese query).
BASE_QUERIES = (
    "reinforcement learning",
    "bus",
    "train",
    "scheduling",
    "holding",
    "What control or scheduling problem does this paper study?",
    "这篇论文研究了什么控制或调度问题？",
)

# Cloud smoke samples query forms instead of spending free-tier tokens on every
# offline keyword.
NETWORK_QUERY_SAMPLE = (
    "reinforcement learning",
    "What control or scheduling problem does this paper study?",
)
NETWORK_QUERY_PAUSE_SECONDS = 10.0
NETWORK_FUSION_CANDIDATE_K = 8


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.smoke",
        description="Layer2 Step1 real-PDF flow validation (>=2 PDFs).",
    )
    parser.add_argument("--real-papers", default=DEFAULT_REAL_PAPERS)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--data-root", default=DEFAULT_SMOKE_ROOT)
    parser.add_argument(
        "--parser",
        default="pymupdf_native",
        help=(
            "parser adapter used for the offline real-PDF flow (default "
            "pymupdf_native so the heavy installed docling/mineru pipelines "
            "are never pulled in automatically)"
        ),
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="opt in to network access / cloud embedding API keys",
    )
    return parser.parse_args(argv)


def _bootstrap_env(args: argparse.Namespace) -> None:
    data_root = Path(args.data_root).resolve()
    os.environ["TRANSIT_SCHOLAR_DATA_DIR"] = str(data_root)
    if args.allow_network:
        os.environ["TRANSIT_SCHOLAR_BLOCK_NETWORK"] = "false"
        os.environ["TRANSIT_SCHOLAR_RETRIEVAL_ALLOW_NETWORK"] = "true"
    else:
        os.environ.setdefault("TRANSIT_SCHOLAR_BLOCK_NETWORK", "1")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    data_root = Path(args.data_root).resolve()
    if args.allow_network:
        os.environ["TRANSIT_SCHOLAR_BLOCK_NETWORK"] = "false"
        os.environ["TRANSIT_SCHOLAR_RETRIEVAL_ALLOW_NETWORK"] = "true"
    else:
        os.environ.setdefault("TRANSIT_SCHOLAR_BLOCK_NETWORK", "1")

    from transit_scholar.config import settings
    from transit_scholar.db.lifecycle import alembic_upgrade_head

    settings.data_root = data_root
    settings.init_directories()
    alembic_upgrade_head()

    real_papers_dir = Path(args.real_papers)
    if not real_papers_dir.is_dir():
        print(f"ERROR: real-papers directory not found: {real_papers_dir}", file=sys.stderr)
        return 2

    pdfs = sorted(
        path
        for path in real_papers_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )
    if len(pdfs) < 2:
        print(
            f"ERROR: need at least 2 PDFs in {real_papers_dir}, found {len(pdfs)}",
            file=sys.stderr,
        )
        return 3

    selected = pdfs[: args.limit]
    print(f"Selected {len(selected)} real PDF(s) from {real_papers_dir}")
    for path in selected:
        print(f"  - {path.name}")

    report = run_smoke(
        selected,
        data_root=data_root,
        allow_network=args.allow_network,
        parser_override=args.parser,
    )

    report_dir = data_root
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "smoke_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nMachine-readable report: {report_dir / 'smoke_report.json'}")

    completed = report["summary"]["completed"]
    print(f"\nPapers that completed the full flow: {completed}")
    if completed >= 2:
        return 0
    print(
        "ERROR: fewer than 2 papers completed the full flow "
        f"(completed={completed}); failing the smoke.",
        file=sys.stderr,
    )
    return 4


def run_smoke(
    pdf_paths: list[Path],
    *,
    data_root: Path,
    allow_network: bool = False,
    parser_override: str = "pymupdf_native",
) -> dict[str, Any]:
    """Run the smoke flow over the given real PDFs and return the report."""
    from transit_scholar.config import settings
    from transit_scholar.db.engine import SessionLocal
    from transit_scholar.db.models import IngestionJob, Paper, PaperFile
    from transit_scholar.layer2 import (
        build_retrieval,
        grep_paper,
        parse_paper,
        search_bm25,
        search_dense,
        search_hybrid,
    )
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.parser.registry import probe_all
    from transit_scholar.workflow.service import get_second_layer_input

    settings.data_root = data_root
    config = Layer2Config.from_settings(settings)
    if allow_network:
        # The smoke validates reranking without sending all 30 production
        # candidates for every query through a free-tier API.
        object.__setattr__(
            config,
            "fusion_candidate_k",
            NETWORK_FUSION_CANDIDATE_K,
        )
    # Pin the deterministic offline parser so the heavy installed
    # docling/mineru pipelines are never pulled in automatically; their real
    # availability is still recorded truthfully in ``parser_probes``.
    object.__setattr__(config, "parser_override", parser_override)
    parser_probes = probe_all(config)

    entries: list[dict[str, Any]] = []
    completed = 0
    for pdf_path in pdf_paths:
        entry = _process_one(
            pdf_path,
            data_root=data_root,
            config=config,
            parser_probes=parser_probes,
            get_gate=get_second_layer_input,
            parse_fn=parse_paper,
            grep_fn=grep_paper,
            bm25_fn=search_bm25,
            dense_fn=search_dense,
            hybrid_fn=search_hybrid,
            build_retrieval_fn=build_retrieval,
            session_factory=SessionLocal,
            models=(Paper, PaperFile, IngestionJob),
            require_dense=allow_network,
        )
        entries.append(entry)
        if entry.get("flow_completed"):
            completed += 1

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_root": str(data_root),
        "real_papers_dir": str(pdf_paths[0].parent) if pdf_paths else None,
        "network_enabled": allow_network,
        "embedding_used": any(entry.get("embedding_used") for entry in entries),
        "parser_override": parser_override,
        "summary": {"papers": len(pdf_paths), "completed": completed},
        "parser_probes": parser_probes,
        "entries": entries,
    }


def _process_one(
    pdf_path: Path,
    *,
    data_root: Path,
    config,
    parser_probes: list[dict[str, object]],
    get_gate,
    parse_fn,
    grep_fn,
    bm25_fn,
    dense_fn,
    hybrid_fn,
    build_retrieval_fn,
    session_factory,
    models,
    require_dense: bool = False,
) -> dict[str, Any]:
    Paper, PaperFile, IngestionJob = models
    entry: dict[str, Any] = {
        "file": pdf_path.name,
        "status": "pending",
        "gate_status": None,
        "gate_blockers": [],
        "parse_status": None,
        "parser_used": None,
        "parse_run_id": None,
        "output_dir": None,
        "errors": [],
        "queries": [],
        "artifacts": {},
        "flow_completed": False,
        "embedding_used": False,
    }

    try:
        paper_id, file_id = _make_ready_record(
            pdf_path, data_root=data_root, session_factory=session_factory,
            models=models,
        )
        entry["paper_id"] = paper_id
        entry["file_id"] = file_id
    except Exception as exc:  # noqa: BLE001 - per-paper isolation
        entry["status"] = "failed"
        entry["errors"].append(f"ready-record construction failed: {exc}")
        return entry

    gate = get_gate(paper_id)
    entry["gate_status"] = gate.status
    entry["gate_blockers"] = list(gate.blockers)
    if gate.status != "ready":
        entry["status"] = "blocked"
        entry["errors"].append(
            f"gate blocked; skipped (never force-parsed): {gate.blockers}"
        )
        print(f"[{pdf_path.name}] gate blocked: {gate.blockers}")
        return entry

    print(f"[{pdf_path.name}] gate=ready source={gate.source_pdf_path}")
    parse_result = parse_fn(paper_id, config=config)
    entry["parse_status"] = parse_result.status
    entry["parser_used"] = parse_result.parser_used
    entry["parse_run_id"] = parse_result.parse_run_id
    entry["output_dir"] = parse_result.output_dir
    entry["warnings"] = list(parse_result.warnings)
    if parse_result.error_code:
        entry["errors"].append(
            f"{parse_result.error_code}: {parse_result.error_message}"
        )
    print(
        f"[{pdf_path.name}] parse={parse_result.status} "
        f"parser={parse_result.parser_used} run={parse_result.parse_run_id}"
    )

    if parse_result.status not in ("passed", "degraded"):
        entry["status"] = parse_result.status
        if parse_result.status == "needs_review":
            entry["errors"].append("parse needs review; no retrieval queries run")
        return entry

    build = build_retrieval_fn(paper_id, config=config)
    if build.get("status") != "ok":
        entry["errors"].append(f"build_retrieval failed: {build}")
    embedding_status = (build.get("manifest") or {}).get("embedding_status")
    entry["embedding_status"] = embedding_status
    entry["embedding_used"] = embedding_status == "ok"
    if require_dense and embedding_status != "ok":
        entry["status"] = "failed"
        entry["errors"].append(
            "network smoke requires embedding_status=ok, got "
            f"{embedding_status!r}"
        )
        return entry
    entry["artifacts"] = _artifact_facts(parse_result.output_dir)

    queries = list(BASE_QUERIES)
    title_word = None
    if gate.title:
        words = [w for w in gate.title.split() if w and len(w) > 3]
        if words:
            title_word = words[0]
            queries.insert(0, title_word)
    elif pdf_path.stem:
        queries.insert(0, pdf_path.stem)

    if require_dense:
        sampled = [query for query in NETWORK_QUERY_SAMPLE if query in queries]
        queries = list(dict.fromkeys(sampled))

    query_results: list[dict[str, Any]] = []
    for query in queries:
        row: dict[str, Any] = {"query": query}
        grep = grep_fn(paper_id, query, config=config)
        row["grep"] = _summarize_method(grep)
        bm25 = bm25_fn(paper_id, query, config=config)
        row["bm25"] = _summarize_method(bm25)
        dense = dense_fn(paper_id, query, config=config)
        row["dense"] = _summarize_method(dense)
        hybrid = hybrid_fn(paper_id, query, config=config)
        row["hybrid"] = _summarize_method(hybrid)
        query_results.append(row)
        _print_query_row(query, row)
        if require_dense and query != queries[-1]:
            time.sleep(NETWORK_QUERY_PAUSE_SECONDS)
    entry["queries"] = query_results
    if require_dense:
        dense_ok = any(row["dense"]["status"] == "ok" for row in query_results)
        hybrid_ok = any(row["hybrid"]["status"] == "ok" for row in query_results)
        if not dense_ok or not hybrid_ok:
            entry["status"] = "failed"
            entry["errors"].append(
                "network smoke requires at least one successful dense and hybrid query"
            )
            return entry
    entry["status"] = "completed"
    entry["flow_completed"] = True
    return entry


def _make_ready_record(
    pdf_path: Path,
    *,
    data_root: Path,
    session_factory,
    models,
) -> tuple[str, str]:
    import hashlib

    Paper, PaperFile, IngestionJob = models
    relative_path = os.path.relpath(pdf_path, start=data_root)
    digest = hashlib.sha256()
    with open(pdf_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    sha256 = digest.hexdigest()
    page_count = _pdf_page_count(pdf_path)

    with session_factory() as session:
        # Idempotent across smoke re-runs: reuse the existing record for the
        # same source bytes (paper_files.sha256 is UNIQUE).
        from sqlalchemy import select

        existing = session.execute(
            select(PaperFile).where(PaperFile.sha256 == sha256)
        ).scalar_one_or_none()
        if existing is not None:
            return existing.paper_id, existing.id

        paper = Paper(
            title=pdf_path.stem.replace("_", " "),
            status="active",
        )
        session.add(paper)
        session.flush()
        pf = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            original_filename=pdf_path.name,
            stored_filename=pdf_path.name,
            relative_path=relative_path,
            sha256=sha256,
            file_size_bytes=pdf_path.stat().st_size,
            mime_type="application/pdf",
            page_count=page_count,
        )
        session.add(pf)
        session.flush()
        session.add(
            IngestionJob(
                uploaded_filename=pdf_path.name,
                file_id=pf.id,
                paper_id=paper.id,
                status="accepted",
                current_stage="completed",
            )
        )
        session.commit()
        return paper.id, pf.id


def _pdf_page_count(pdf_path: Path) -> int | None:
    try:
        import fitz

        document = fitz.open(pdf_path)
        try:
            return document.page_count
        finally:
            document.close()
    except Exception:  # noqa: BLE001 - page count is best effort
        return None


def _artifact_facts(output_dir: str | None) -> dict[str, bool]:
    if not output_dir:
        return {}
    root = Path(output_dir)
    return {
        "document.json": (root / "document.json").is_file(),
        "sections.json": (root / "sections.json").is_file(),
        "blocks.jsonl": (root / "blocks.jsonl").is_file(),
        "parser_manifest.json": (root / "parser_manifest.json").is_file(),
        "paper.md": (root / "paper.md").is_file(),
        "markdown_map.jsonl": (root / "markdown_map.jsonl").is_file(),
        "retrieval_chunks.jsonl": (root / "retrieval_chunks.jsonl").is_file(),
    }


def _summarize_method(result) -> dict[str, Any]:
    if result.status != "ok":
        return {
            "status": result.status,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "hit_count": 0,
        }
    hits = result.hits
    return {
        "status": "ok",
        "hit_count": len(hits),
        "top": [
            {
                "rank": hit.rank,
                "chunk_id": hit.chunk_id,
                "pages": hit.pages,
                "text": hit.text[:180],
            }
            for hit in hits[:3]
        ],
    }


def _print_query_row(query: str, row: dict[str, Any]) -> None:
    def brief(kind: str) -> str:
        info = row[kind]
        if info["status"] != "ok":
            return f"{info['status']}({info['error_code']})"
        return f"ok/{info['hit_count']}hits"

    print(
        f"    query={query!r} grep={brief('grep')} bm25={brief('bm25')} "
        f"dense={brief('dense')} hybrid={brief('hybrid')}"
    )


if __name__ == "__main__":
    _args = _parse_args(sys.argv[1:])
    _bootstrap_env(_args)
    raise SystemExit(main(sys.argv[1:]))
