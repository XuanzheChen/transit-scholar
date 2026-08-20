"""Retrieval store boundary (FR-010).

``LanceDBStore`` is the V1 production store: it builds and searches a real
single-paper LanceDB table (local directory, full-text + optional vector
index) when ``lancedb`` is installed. ``LocalStore`` is the pure-Python
offline store used by automated tests and as a truthful fallback when LanceDB
is absent: deterministic BM25 + optional dense vectors when an embedding
provider is available.

Both stores build their index from ``retrieval_chunks.jsonl`` (via
``api.build_retrieval``) so derived indexes are always rebuildable. Each store
writes a ``store.json`` marker so ``api._load_store`` reloads the same store
that was built, independent of the current installation state. When a store
operation cannot be performed the store returns an accurate ``error_code``
(``not_implemented`` / ``dependency_missing``) -- it never fakes success.
"""

from __future__ import annotations

import abc
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.retrieval.bm25 import BM25Index
from transit_scholar.layer2.retrieval.fusion import fuse_hybrid
from transit_scholar.layer2.retrieval.providers import (
    EmbeddingProvider,
    RerankerProvider,
    UnavailableError,
)
from transit_scholar.layer2.schema import RetrievalChunk, RetrievalHit

CHUNKS_INDEX_FILENAME = "chunks.json"
VECTORS_INDEX_FILENAME = "vectors.json"
STORE_MARKER_FILENAME = "store.json"
LANCEDB_DIRNAME = "lancedb"
LANCEDB_TABLE = "chunks"

#: How many candidates LanceDB search fetches before in-memory filtering so
#: ``filters`` narrow the result set instead of silently truncating it.
_LANCEDB_FETCH_LIMIT = 1000


@dataclass
class StoreSearchResult:
    hits: list[RetrievalHit] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_code is None


class RetrievalStore(abc.ABC):
    """Abstract single-paper retrieval store."""

    store_name: str = "abstract"

    @abc.abstractmethod
    def build(self, chunks: list[RetrievalChunk]) -> dict[str, Any]:
        """Build the index from retrieval chunks (derived, rebuildable)."""

    @abc.abstractmethod
    def search_bm25(
        self, query: str, *, top_k: int = 20, filters: dict[str, Any] | None = None
    ) -> StoreSearchResult:
        ...

    @abc.abstractmethod
    def search_dense(
        self, query: str, *, top_k: int = 20, filters: dict[str, Any] | None = None
    ) -> StoreSearchResult:
        ...

    @abc.abstractmethod
    def search_hybrid(
        self,
        query: str,
        *,
        top_k: int = 8,
        rerank: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> StoreSearchResult:
        ...

    def close(self) -> None:  # noqa: B027 - optional hook
        return None


def _chunk_to_hit(chunk: RetrievalChunk, score: float | None, method: str, rank: int) -> RetrievalHit:
    return RetrievalHit(
        paper_id=chunk.paper_id,
        chunk_id=chunk.chunk_id,
        score=score,
        retrieval_method=method,
        section_path=list(chunk.section_path),
        pages=list(chunk.pages),
        source_refs=list(chunk.source_refs),
        text=chunk.retrieval_text,
        rank=rank,
    )


def _chunk_matches(chunk: RetrievalChunk, filters: dict[str, Any]) -> bool:
    for key, value in filters.items():
        if key == "section_id":
            if chunk.section_id != value:
                return False
        elif key == "pages":
            wanted = value if isinstance(value, (list, tuple)) else [value]
            if not any(int(p) in wanted for p in chunk.pages):
                return False
        elif key == "block_type":
            if value not in chunk.block_types:
                return False
        elif key == "parse_run_id":
            if chunk.parse_run_id != value:
                return False
        else:
            candidate = chunk.to_dict().get(key)
            if candidate != value:
                return False
    return True


def write_store_marker(index_dir: Path, store_name: str) -> None:
    """Record which store built this index (so load picks the same one)."""
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / STORE_MARKER_FILENAME).write_text(
        json.dumps({"store": store_name}) + "\n", encoding="utf-8"
    )


def read_store_marker(index_dir: Path) -> str | None:
    """Return the recorded store name or ``None`` when no marker exists."""
    path = index_dir / STORE_MARKER_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    store = data.get("store")
    return store if isinstance(store, str) else None


class LocalStore(RetrievalStore):
    """Pure-Python deterministic store used offline (tests + smoke)."""

    store_name = "local"

    def __init__(
        self,
        config: Layer2Config,
        *,
        paper_id: str,
        index_dir: Path,
        embedding_provider: EmbeddingProvider | None = None,
        reranker_provider: RerankerProvider | None = None,
    ) -> None:
        self._config = config
        self._paper_id = paper_id
        self._index_dir = index_dir
        self._embedding_provider = embedding_provider
        self._reranker_provider = reranker_provider
        self._chunks: list[RetrievalChunk] = []
        self._bm25: BM25Index | None = None
        self._vectors: list[list[float]] | None = None

    # ------------------------------------------------------------- build

    @classmethod
    def from_index(
        cls,
        config: Layer2Config,
        *,
        paper_id: str,
        index_dir: Path,
        embedding_provider: EmbeddingProvider | None = None,
        reranker_provider: RerankerProvider | None = None,
    ) -> "LocalStore | None":
        """Load a previously built local index (rebuildable from chunks)."""
        chunks_path = index_dir / CHUNKS_INDEX_FILENAME
        if not chunks_path.is_file():
            return None
        store = cls(
            config,
            paper_id=paper_id,
            index_dir=index_dir,
            embedding_provider=embedding_provider,
            reranker_provider=reranker_provider,
        )
        store._chunks = [
            RetrievalChunk.from_dict(json.loads(line))
            for line in chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        store._bm25 = BM25Index.build(
            [c.retrieval_text for c in store._chunks]
        )
        vectors_path = index_dir / VECTORS_INDEX_FILENAME
        if vectors_path.is_file():
            try:
                store._vectors = json.loads(vectors_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                store._vectors = None
        return store

    def build(self, chunks: list[RetrievalChunk]) -> dict[str, Any]:
        self._chunks = list(chunks)
        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._bm25 = BM25Index.build(
            [c.retrieval_text for c in self._chunks]
        )
        vectors: list[list[float]] | None = None
        embedding_status = "not_configured"
        embedding_reason: str | None = None
        provider = self._embedding_provider
        if provider is None:
            embedding_status = "not_configured"
            embedding_reason = "no embedding provider configured"
        elif not provider.available:
            embedding_status = "unavailable"
            embedding_reason = provider.reason
        else:
            try:
                vectors = provider.embed_documents(
                    [c.retrieval_text for c in self._chunks]
                )
                embedding_status = "ok"
            except UnavailableError as exc:
                embedding_status = "unavailable"
                embedding_reason = exc.reason
            except Exception as exc:  # noqa: BLE001 - provider failure is structured
                embedding_status = "unavailable"
                embedding_reason = f"{type(exc).__name__}: {exc}"
        self._vectors = vectors

        write_store_marker(self._index_dir, self.store_name)
        (self._index_dir / CHUNKS_INDEX_FILENAME).write_text(
            "\n".join(
                json.dumps(c.to_dict(), ensure_ascii=False) for c in self._chunks
            )
            + ("\n" if self._chunks else ""),
            encoding="utf-8",
        )
        if vectors is not None:
            (self._index_dir / VECTORS_INDEX_FILENAME).write_text(
                json.dumps(vectors), encoding="utf-8"
            )
        else:
            path = self._index_dir / VECTORS_INDEX_FILENAME
            if path.exists():
                path.unlink()
        return {
            "store": self.store_name,
            "chunk_count": len(self._chunks),
            "embedding_status": embedding_status,
            "embedding_reason": embedding_reason,
            "bm25_engine": "local_bm25_v1",
        }

    # ------------------------------------------------------------- search

    def search_bm25(
        self, query: str, *, top_k: int = 20, filters: dict[str, Any] | None = None
    ) -> StoreSearchResult:
        if self._bm25 is None or not self._chunks:
            return StoreSearchResult(
                error_code="no_index",
                error_message="retrieval index is not built for this paper",
            )
        candidates = list(self._chunks)
        if filters:
            candidates = [c for c in candidates if _chunk_matches(c, filters)]
        indices = [self._chunks.index(c) for c in candidates]
        scores = [(idx, self._bm25.score(query, idx)) for idx in indices]
        scores = [(idx, s) for idx, s in scores if s > 0]
        scores.sort(key=lambda pair: pair[1], reverse=True)
        hits: list[RetrievalHit] = []
        for rank, (idx, score) in enumerate(scores[:top_k], start=1):
            hits.append(_chunk_to_hit(self._chunks[idx], score, "bm25", rank))
        return StoreSearchResult(hits=hits)

    def search_dense(
        self, query: str, *, top_k: int = 20, filters: dict[str, Any] | None = None
    ) -> StoreSearchResult:
        provider = self._embedding_provider
        if provider is None or not provider.available:
            return StoreSearchResult(
                error_code=provider.reason if provider else "not_configured",
                error_message="dense retrieval unavailable",
            )
        if self._vectors is None or not self._chunks:
            return StoreSearchResult(
                error_code="no_index",
                error_message="dense index is not built for this paper",
            )
        try:
            query_vec = provider.embed_query(query)
        except UnavailableError as exc:
            return StoreSearchResult(
                error_code=exc.error_code, error_message=exc.reason
            )
        candidates = list(range(len(self._chunks)))
        if filters:
            candidates = [
                idx
                for idx in candidates
                if _chunk_matches(self._chunks[idx], filters)
            ]
        scored = [
            (idx, _cosine(self._vectors[idx], query_vec))
            for idx in candidates
            if len(self._vectors[idx]) == len(query_vec)
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        hits: list[RetrievalHit] = []
        for rank, (idx, score) in enumerate(scored[:top_k], start=1):
            hits.append(_chunk_to_hit(self._chunks[idx], score, "dense", rank))
        return StoreSearchResult(hits=hits)

    def search_hybrid(
        self,
        query: str,
        *,
        top_k: int = 8,
        rerank: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> StoreSearchResult:
        bm25 = self.search_bm25(
            query, top_k=self._config.bm25_top_k, filters=filters
        )
        dense = self.search_dense(
            query, top_k=self._config.dense_top_k, filters=filters
        )
        if dense.error_code is not None:
            return StoreSearchResult(
                error_code=dense.error_code,
                error_message=f"hybrid unavailable: {dense.error_message}",
            )
        try:
            hits, warnings = fuse_hybrid(
                query,
                bm25.hits,
                dense.hits,
                self._config,
                reranker_provider=self._reranker_provider,
                rerank=rerank,
                top_k=top_k,
            )
        except UnavailableError as exc:
            return StoreSearchResult(
                error_code=exc.error_code,
                error_message=f"hybrid rerank unavailable: {exc.reason}",
            )
        return StoreSearchResult(hits=hits, warnings=warnings)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


class LanceDBStore(RetrievalStore):
    """V1 production store backed by a real single-paper LanceDB table.

    The table lives under ``index_dir/lancedb/``; ``build`` writes the chunk
    payload plus (when an embedding provider is available) a vector column and
    creates a full-text index for BM25. ``open_existing`` reloads a previously
    built index so search does not depend on how the index was originally
    built. When a capability genuinely cannot be performed the store returns an
    accurate ``not_implemented`` / ``dependency_missing`` status -- it never
    fakes success.
    """

    store_name = "lancedb"

    def __init__(
        self,
        config: Layer2Config,
        *,
        paper_id: str,
        index_dir: Path,
        embedding_provider: EmbeddingProvider | None = None,
        reranker_provider: RerankerProvider | None = None,
    ) -> None:
        self._config = config
        self._paper_id = paper_id
        self._index_dir = index_dir
        self._embedding_provider = embedding_provider
        self._reranker_provider = reranker_provider
        self._db = None
        self._table = None

    # ------------------------------------------------------------- lifecycle

    def _lancedb_dir(self) -> Path:
        return self._index_dir / LANCEDB_DIRNAME

    def _connect(self):
        if self._db is None:
            import lancedb

            self._db = lancedb.connect(str(self._lancedb_dir()))
        return self._db

    @classmethod
    def open_existing(
        cls,
        config: Layer2Config,
        *,
        paper_id: str,
        index_dir: Path,
        embedding_provider: EmbeddingProvider | None = None,
        reranker_provider: RerankerProvider | None = None,
    ) -> "LanceDBStore | None":
        """Open a previously built LanceDB index, or ``None`` if absent."""
        if not (index_dir / LANCEDB_DIRNAME).is_dir():
            return None
        store = cls(
            config,
            paper_id=paper_id,
            index_dir=index_dir,
            embedding_provider=embedding_provider,
            reranker_provider=reranker_provider,
        )
        db = store._connect()
        if LANCEDB_TABLE not in _lancedb_table_names(db):
            return None
        store._table = db.open_table(LANCEDB_TABLE)
        return store

    # ------------------------------------------------------------- build

    def build(self, chunks: list[RetrievalChunk]) -> dict[str, Any]:
        lancedb = self._require_lancedb()
        vectors: list[list[float]] | None = None
        embedding_status = "not_configured"
        embedding_reason: str | None = None
        provider = self._embedding_provider
        if provider is None:
            embedding_status = "not_configured"
            embedding_reason = "no embedding provider configured"
        elif not provider.available:
            embedding_status = "unavailable"
            embedding_reason = provider.reason
        else:
            try:
                vectors = provider.embed_documents(
                    [c.retrieval_text for c in chunks]
                )
                embedding_status = "ok"
            except UnavailableError as exc:
                embedding_status = "unavailable"
                embedding_reason = exc.reason
            except Exception as exc:  # noqa: BLE001 - provider failure is structured
                embedding_status = "unavailable"
                embedding_reason = f"{type(exc).__name__}: {exc}"

        self._index_dir.mkdir(parents=True, exist_ok=True)
        write_store_marker(self._index_dir, self.store_name)
        db = self._connect()
        if LANCEDB_TABLE in _lancedb_table_names(db):
            db.drop_table(LANCEDB_TABLE)
        records = [
            _lancedb_record(chunk, vectors[index] if vectors is not None else None)
            for index, chunk in enumerate(chunks)
        ]
        table = db.create_table(LANCEDB_TABLE, data=records)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=DeprecationWarning)
                table.create_fts_index("retrieval_text")
            fts_status = "ok"
            fts_reason = None
        except Exception as exc:  # noqa: BLE001 - FTS index is best effort
            fts_status = "unavailable"
            fts_reason = f"{type(exc).__name__}: {exc}"
        self._table = table
        return {
            "store": self.store_name,
            "chunk_count": len(chunks),
            "embedding_status": embedding_status,
            "embedding_reason": embedding_reason,
            "bm25_engine": "lancedb_fts_v1",
            "fts_status": fts_status,
            "fts_reason": fts_reason,
        }

    # ------------------------------------------------------------- search

    def search_bm25(
        self, query: str, *, top_k: int = 20, filters: dict[str, Any] | None = None
    ) -> StoreSearchResult:
        if self._table is None:
            return StoreSearchResult(
                error_code="no_index",
                error_message="lancedb index is not built for this paper",
            )
        try:
            rows = (
                self._table.search(query, query_type="fts")
                .limit(_LANCEDB_FETCH_LIMIT)
                .to_list()
            )
        except Exception as exc:  # noqa: BLE001 - structured unavailable
            return StoreSearchResult(
                error_code="fts_unavailable",
                error_message=f"lancedb full-text search unavailable: {exc}",
            )
        hits: list[RetrievalHit] = []
        for row in rows:
            chunk = _chunk_from_payload(row)
            if chunk is None:
                continue
            if filters and not _chunk_matches(chunk, filters):
                continue
            rank = len(hits) + 1
            hits.append(_chunk_to_hit(chunk, float(row.get("_score", 0.0)), "bm25", rank))
            if rank >= top_k:
                break
        return StoreSearchResult(hits=hits)

    def search_dense(
        self, query: str, *, top_k: int = 20, filters: dict[str, Any] | None = None
    ) -> StoreSearchResult:
        provider = self._embedding_provider
        if provider is None or not provider.available:
            return StoreSearchResult(
                error_code=provider.reason if provider else "not_configured",
                error_message="dense retrieval unavailable",
            )
        if self._table is None:
            return StoreSearchResult(
                error_code="no_index",
                error_message="lancedb index is not built for this paper",
            )
        field_names = getattr(self._table.schema, "names", []) or []
        if "vector" not in field_names:
            return StoreSearchResult(
                error_code="not_implemented",
                error_message="lancedb index has no vector column (no embedding provider at build time)",
            )
        try:
            query_vec = provider.embed_query(query)
        except UnavailableError as exc:
            return StoreSearchResult(
                error_code=exc.error_code, error_message=exc.reason
            )
        try:
            rows = (
                self._table.search(query_vec)
                .limit(_LANCEDB_FETCH_LIMIT)
                .to_list()
            )
        except Exception as exc:  # noqa: BLE001 - structured unavailable
            return StoreSearchResult(
                error_code="vector_search_unavailable",
                error_message=f"lancedb vector search unavailable: {exc}",
            )
        scored: list[tuple[float, RetrievalChunk]] = []
        for row in rows:
            chunk = _chunk_from_payload(row)
            if chunk is None:
                continue
            if filters and not _chunk_matches(chunk, filters):
                continue
            distance = float(row.get("_distance", 1.0))
            scored.append((1.0 / (1.0 + distance), chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        hits = [
            _chunk_to_hit(chunk, score, "dense", rank)
            for rank, (score, chunk) in enumerate(scored[:top_k], start=1)
        ]
        return StoreSearchResult(hits=hits)

    def search_hybrid(
        self,
        query: str,
        *,
        top_k: int = 8,
        rerank: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> StoreSearchResult:
        bm25 = self.search_bm25(
            query, top_k=self._config.bm25_top_k, filters=filters
        )
        dense = self.search_dense(
            query, top_k=self._config.dense_top_k, filters=filters
        )
        if dense.error_code is not None:
            return StoreSearchResult(
                error_code=dense.error_code,
                error_message=f"hybrid unavailable: {dense.error_message}",
            )
        try:
            hits, warnings = fuse_hybrid(
                query,
                bm25.hits,
                dense.hits,
                self._config,
                reranker_provider=self._reranker_provider,
                rerank=rerank,
                top_k=top_k,
            )
        except UnavailableError as exc:
            return StoreSearchResult(
                error_code=exc.error_code,
                error_message=f"hybrid rerank unavailable: {exc.reason}",
            )
        return StoreSearchResult(hits=hits, warnings=warnings)

    # ------------------------------------------------------------- helpers

    def _require_lancedb(self):
        try:
            import lancedb  # noqa: F401

            return lancedb
        except ImportError as exc:
            raise UnavailableError(
                "dependency_missing: lancedb is not installed",
                error_code="dependency_missing",
            ) from exc


def _lancedb_table_names(db) -> list[str]:
    """List table names across lancedb versions (``list_tables`` >= 0.37)."""
    if hasattr(db, "list_tables"):
        result = db.list_tables()
        names = getattr(result, "tables", result)
        return list(names) if names else []
    return list(db.table_names())


def _lancedb_record(chunk: RetrievalChunk, vector: list[float] | None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "chunk_id": chunk.chunk_id,
        "paper_id": chunk.paper_id,
        "parse_run_id": chunk.parse_run_id,
        "section_id": chunk.section_id or "",
        "retrieval_text": chunk.retrieval_text,
        "block_types": json.dumps(chunk.block_types, ensure_ascii=False),
        "payload": json.dumps(chunk.to_dict(), ensure_ascii=False),
    }
    if vector is not None:
        record["vector"] = list(vector)
    return record


def _chunk_from_payload(row: dict[str, Any]) -> RetrievalChunk | None:
    payload = row.get("payload")
    if not isinstance(payload, str):
        return None
    try:
        return RetrievalChunk.from_dict(json.loads(payload))
    except (ValueError, json.JSONDecodeError):
        return None
