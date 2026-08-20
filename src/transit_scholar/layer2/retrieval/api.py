"""Public single-paper retrieval API (FR-010/FR-011/FR-012).

Search functions return a ``RetrievalResult`` envelope so an explicit
``unavailable`` / ``dependency_missing`` state is conveyed in-band; read
functions return plain canonical data. No public API exposes LanceDB types.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from transit_scholar.config import settings as global_settings
from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.paths import (
    run_paths,
    retrieval_index_dir,
    retrieval_manifest_path,
)
from transit_scholar.layer2.retrieval.providers import (
    resolve_embedding_provider,
    resolve_reranker_provider,
)
from transit_scholar.layer2.retrieval.providers import UnavailableError
from transit_scholar.layer2.retrieval.store import (
    CHUNKS_INDEX_FILENAME,
    LANCEDB_DIRNAME,
    LanceDBStore,
    LocalStore,
    RetrievalStore,
    read_store_marker,
)
from transit_scholar.layer2.schema import (
    CanonicalBlock,
    CanonicalSection,
    MarkdownMapEntry,
    RetrievalChunk,
    RetrievalHit,
    RetrievalResult,
    SourceRef,
)

RETRIEVAL_MANIFEST_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def grep_paper(paper_id: str, pattern: str, *, config: Layer2Config | None = None) -> RetrievalResult:
    """Exact/regex grep over ``paper.md`` resolved through ``markdown_map.jsonl``."""
    config = config or Layer2Config.from_settings(global_settings)
    current_run = _current_run(config, paper_id)
    if current_run is None:
        return _unavailable("grep", "no_current_run", "paper has no active parse run")
    rp = run_paths(config, paper_id, current_run)
    if not rp.markdown_path.is_file() or not rp.markdown_map_path.is_file():
        return _unavailable("grep", "markdown_missing", "paper.md or markdown_map.jsonl missing")
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return _unavailable("grep", "invalid_pattern", f"invalid regex pattern: {exc}")

    lines = rp.markdown_path.read_text(encoding="utf-8").split("\n")
    entries = [
        MarkdownMapEntry.from_dict(json.loads(line))
        for line in rp.markdown_map_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    blocks = _load_blocks(rp)
    blocks_by_id = {b.block_id: b for b in blocks}
    sections = _load_sections(rp)
    section_paths = _build_section_paths(sections)

    hits: list[RetrievalHit] = []
    rank = 0
    for line_no, line in enumerate(lines, start=1):
        if not regex.search(line):
            continue
        entry = _entry_for_line(entries, line_no)
        if entry is None:
            continue
        for block_id in entry.block_ids:
            block = blocks_by_id.get(block_id)
            if block is None:
                continue
            rank += 1
            hits.append(
                RetrievalHit(
                    paper_id=paper_id,
                    chunk_id=None,
                    score=1.0,
                    retrieval_method="grep",
                    section_path=section_paths.get(block.section_id or "", []),
                    pages=list(block.pages),
                    source_refs=_grep_source_refs(block, regex),
                    text=line,
                    rank=rank,
                )
            )
    return RetrievalResult(status="ok", method="grep", hits=hits)


def search_bm25(
    paper_id: str,
    query: str,
    top_k: int = 20,
    filters: dict[str, Any] | None = None,
    *,
    config: Layer2Config | None = None,
) -> RetrievalResult:
    """BM25 search over retrieval chunks."""
    config = config or Layer2Config.from_settings(global_settings)
    store, error = _load_store(config, paper_id)
    if store is None:
        return _unavailable("bm25", error or "index_not_built", "retrieval index not built")
    result = store.search_bm25(query, top_k=top_k, filters=filters)
    if not result.ok:
        return _unavailable("bm25", result.error_code or "unavailable", result.error_message or "")
    return RetrievalResult(status="ok", method="bm25", hits=result.hits, warnings=result.warnings)


def search_dense(
    paper_id: str,
    query: str,
    top_k: int = 20,
    filters: dict[str, Any] | None = None,
    *,
    config: Layer2Config | None = None,
) -> RetrievalResult:
    """Dense vector search (cloud provider adapter; unavailable when unconfigured)."""
    config = config or Layer2Config.from_settings(global_settings)
    store, error = _load_store(config, paper_id)
    if store is None:
        return _unavailable("dense", error or "index_not_built", "retrieval index not built")
    result = store.search_dense(query, top_k=top_k, filters=filters)
    if not result.ok:
        return _unavailable(
            "dense", result.error_code or "unavailable", result.error_message or ""
        )
    return RetrievalResult(status="ok", method="dense", hits=result.hits, warnings=result.warnings)


def search_hybrid(
    paper_id: str,
    query: str,
    top_k: int = 8,
    rerank: bool = True,
    filters: dict[str, Any] | None = None,
    *,
    config: Layer2Config | None = None,
) -> RetrievalResult:
    """Hybrid BM25 + dense -> RRF -> optional rerank."""
    config = config or Layer2Config.from_settings(global_settings)
    store, error = _load_store(config, paper_id)
    if store is None:
        return _unavailable("hybrid", error or "index_not_built", "retrieval index not built")
    result = store.search_hybrid(
        query, top_k=top_k, rerank=rerank, filters=filters
    )
    if not result.ok:
        return _unavailable(
            "hybrid", result.error_code or "unavailable", result.error_message or ""
        )
    return RetrievalResult(status="ok", method="hybrid", hits=result.hits, warnings=result.warnings)


# ---------------------------------------------------------------------------
# Read back
# ---------------------------------------------------------------------------


def read_blocks(paper_id: str, block_ids: list[str], *, config: Layer2Config | None = None) -> list[dict[str, Any]]:
    """Return canonical blocks for the requested ids (current run)."""
    config = config or Layer2Config.from_settings(global_settings)
    current_run = _current_run(config, paper_id)
    if current_run is None:
        return []
    rp = run_paths(config, paper_id, current_run)
    blocks_by_id = {b.block_id: b for b in _load_blocks(rp)}
    return [
        blocks_by_id[block_id].to_dict()
        for block_id in block_ids
        if block_id in blocks_by_id
    ]


def read_context(
    paper_id: str,
    block_id: str,
    before: int = 2,
    after: int = 2,
    *,
    config: Layer2Config | None = None,
) -> list[dict[str, Any]]:
    """Return canonical neighbours in reading order (clamped at edges)."""
    config = config or Layer2Config.from_settings(global_settings)
    current_run = _current_run(config, paper_id)
    if current_run is None:
        return []
    rp = run_paths(config, paper_id, current_run)
    blocks = sorted(_load_blocks(rp), key=lambda b: b.order)
    positions = {b.block_id: index for index, b in enumerate(blocks)}
    if block_id not in positions:
        return []
    index = positions[block_id]
    lo = max(0, index - max(0, before))
    hi = min(len(blocks), index + max(0, after) + 1)
    return [b.to_dict() for b in blocks[lo:hi]]


def read_section(paper_id: str, section_id: str, *, config: Layer2Config | None = None) -> list[dict[str, Any]]:
    """Return all blocks of a section in reading order."""
    config = config or Layer2Config.from_settings(global_settings)
    current_run = _current_run(config, paper_id)
    if current_run is None:
        return []
    rp = run_paths(config, paper_id, current_run)
    blocks = sorted(_load_blocks(rp), key=lambda b: b.order)
    return [b.to_dict() for b in blocks if b.section_id == section_id]


# ---------------------------------------------------------------------------
# Derived index build
# ---------------------------------------------------------------------------


def build_retrieval(paper_id: str, *, config: Layer2Config | None = None) -> dict[str, Any]:
    """Build/rebuild the derived retrieval index from ``retrieval_chunks.jsonl``."""
    config = config or Layer2Config.from_settings(global_settings)
    current_run = _current_run(config, paper_id)
    if current_run is None:
        return {"status": "unavailable", "error_code": "no_current_run"}
    rp = run_paths(config, paper_id, current_run)
    if not rp.chunks_path.is_file():
        return {"status": "unavailable", "error_code": "chunks_missing"}
    chunks = [
        RetrievalChunk.from_dict(json.loads(line))
        for line in rp.chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not chunks:
        return {"status": "unavailable", "error_code": "chunks_empty"}

    store, warning = _make_store(config, paper_id)
    try:
        build_info = store.build(chunks)
    except UnavailableError as exc:
        return {
            "status": "unavailable",
            "error_code": exc.error_code or "unavailable",
            "error_message": exc.reason,
        }
    except Exception as exc:  # noqa: BLE001 - structured store failure
        return {
            "status": "unavailable",
            "error_code": "store_build_failed",
            "error_message": str(exc),
        }
    manifest = _retrieval_manifest(
        config, paper_id, current_run, build_info, warning
    )
    retrieval_manifest_path(config, paper_id).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {"status": "ok", "manifest": manifest}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unavailable(method: str, error_code: str, message: str) -> RetrievalResult:
    return RetrievalResult(
        status="unavailable", method=method, error_code=error_code, error_message=message
    )


def _current_run(config: Layer2Config, paper_id: str) -> str | None:
    from transit_scholar.layer2.paths import load_current

    return load_current(config.parsed_paper_dir(paper_id))


def _load_blocks(rp) -> list[CanonicalBlock]:
    if not rp.blocks_path.is_file():
        return []
    return [
        CanonicalBlock.from_dict(json.loads(line))
        for line in rp.blocks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_sections(rp) -> list[CanonicalSection]:
    if not rp.sections_path.is_file():
        return []
    return [
        CanonicalSection.from_dict(record)
        for record in json.loads(rp.sections_path.read_text(encoding="utf-8"))
    ]


def _build_section_paths(sections: list[CanonicalSection]) -> dict[str, list[str]]:
    by_id = {s.section_id: s for s in sections}
    paths: dict[str, list[str]] = {}
    for sec in sections:
        path: list[str] = []
        current: str | None = sec.section_id
        while current:
            node = by_id.get(current)
            if node is None:
                break
            path.insert(0, node.title)
            current = node.parent_section_id
        paths[sec.section_id] = path
    return paths


def _entry_for_line(entries: list[MarkdownMapEntry], line_no: int) -> MarkdownMapEntry | None:
    for entry in entries:
        if entry.md_line_start <= line_no <= entry.md_line_end:
            return entry
    return None


def _grep_source_refs(block: CanonicalBlock, regex: re.Pattern[str]) -> list[SourceRef]:
    if not block.text:
        return [SourceRef(block_id=block.block_id, char_start=0, char_end=0)]
    matches = list(regex.finditer(block.text))
    if not matches:
        return [SourceRef(block_id=block.block_id, char_start=0, char_end=len(block.text))]
    return [
        SourceRef(block_id=block.block_id, char_start=m.start(), char_end=m.end())
        for m in matches[:4]
    ]


def _load_store(
    config: Layer2Config, paper_id: str
) -> tuple[RetrievalStore | None, str | None]:
    """Reload the store that actually built the index (from ``store.json``).

    The store type is recorded at build time so search is deterministic and
    independent of whether LanceDB happens to be installed at load time.
    """
    index_dir = retrieval_index_dir(config, paper_id)
    if not index_dir.is_dir():
        return None, "index_not_built"
    try:
        embedding = resolve_embedding_provider(config)
        reranker = resolve_reranker_provider(config)
    except ImportError:
        return None, "provider_dependency_missing"

    store_type = read_store_marker(index_dir)
    if store_type == LanceDBStore.store_name:
        if not (index_dir / LANCEDB_DIRNAME).is_dir():
            return None, "index_not_built"
        try:
            store = LanceDBStore.open_existing(
                config,
                paper_id=paper_id,
                index_dir=index_dir,
                embedding_provider=embedding,
                reranker_provider=reranker,
            )
        except Exception as exc:  # noqa: BLE001 - structured index load failure
            return None, f"index_unreadable: {type(exc).__name__}"
        if store is None:
            return None, "index_not_built"
        return store, None

    # local (or an unmarked legacy index)
    if not (index_dir / CHUNKS_INDEX_FILENAME).is_file():
        return None, "index_not_built"
    store = LocalStore.from_index(
        config,
        paper_id=paper_id,
        index_dir=index_dir,
        embedding_provider=embedding,
        reranker_provider=reranker,
    )
    if store is None:
        return None, "index_not_built"
    return store, None


def _make_store(
    config: Layer2Config, paper_id: str
) -> tuple[RetrievalStore, str | None]:
    """Create the configured store.

    ``store="lancedb"`` returns a real ``LanceDBStore`` when ``lancedb`` is
    importable and falls back to ``LocalStore`` (truthfully recorded) when the
    dependency is absent. ``store="local"`` (or any other value) returns
    ``LocalStore``. The returned store is never a placeholder pretending to be
    a production backend: ``LanceDBStore`` performs a real build/load/search.
    """
    try:
        embedding = resolve_embedding_provider(config)
        reranker = resolve_reranker_provider(config)
    except ImportError:
        embedding = None
        reranker = None
    warning: str | None = None
    if config.store == LanceDBStore.store_name:
        try:
            import lancedb  # noqa: F401

            return (
                LanceDBStore(
                    config,
                    paper_id=paper_id,
                    index_dir=retrieval_index_dir(config, paper_id),
                    embedding_provider=embedding,
                    reranker_provider=reranker,
                ),
                None,
            )
        except ImportError:
            warning = (
                "lancedb dependency_missing: falling back to the local "
                "pure-Python store (BM25 only, no vector search)"
            )
    elif config.store != LocalStore.store_name:
        warning = (
            f"unknown retrieval store {config.store!r}: using the local "
            "pure-Python store"
        )
    return (
        LocalStore(
            config,
            paper_id=paper_id,
            index_dir=retrieval_index_dir(config, paper_id),
            embedding_provider=embedding,
            reranker_provider=reranker,
        ),
        warning,
    )


def _retrieval_manifest(
    config: Layer2Config,
    paper_id: str,
    parse_run_id: str,
    build_info: dict[str, Any],
    warning: str | None,
) -> dict[str, Any]:
    try:
        embedding = resolve_embedding_provider(config)
        reranker = resolve_reranker_provider(config)
    except ImportError:
        embedding = None
        reranker = None
    embedding_info = embedding.info if embedding else None
    reranker_info = reranker.info if reranker else None
    from transit_scholar.layer2.util import now_utc_iso

    return {
        "format_version": RETRIEVAL_MANIFEST_VERSION,
        "paper_id": paper_id,
        "parse_run_id": parse_run_id,
        "store": build_info.get("store", "local"),
        "chunker_version": config.chunker_version,
        "chunker_config_hash": config.chunk_config_hash(),
        "bm25_engine": build_info.get("bm25_engine", "local_bm25_v1"),
        "bm25_index_version": "v1",
        "embedding_model": config.resolved_embedding_model,
        "embedding_model_revision": embedding_info.revision if embedding_info else None,
        "embedding_dimension": config.resolved_embedding_dimension,
        "embedding_status": build_info.get("embedding_status", "not_configured"),
        "embedding_reason": build_info.get("embedding_reason"),
        "fusion_method": config.fusion,
        "fusion_candidate_k": config.fusion_candidate_k,
        "final_top_k": config.final_top_k,
        "rrf_weights": {
            "bm25": config.rrf_bm25_weight,
            "dense": config.rrf_dense_weight,
        },
        "reranker_model": config.resolved_reranker_model,
        "reranker_model_revision": reranker_info.revision if reranker_info else None,
        "warnings": ([warning] if warning else []),
        "created_at": now_utc_iso(),
    }
