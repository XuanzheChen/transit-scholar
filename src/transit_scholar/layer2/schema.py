"""Canonical Layer2 Step1 data model (FR-002, FR-011).

The canonical model is the only stable document interface between third-party
parsers and downstream retrieval/schema/agent code. Only document facts live
here; no domain semantics (reward_function / state_definition / action_space /
baseline / holding_limit) ever appears as a ``block_type``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

#: Allowed generic ``block_type`` values (FR-002 / AC-L2S1-CANONICAL-03).
BLOCK_TYPES: frozenset[str] = frozenset(
    {
        "paragraph",
        "heading",
        "list",
        "table",
        "figure",
        "caption",
        "equation",
        "footnote",
        "reference",
        "other",
    }
)

#: Block types that must NEVER appear in Step1 artifacts (domain semantics).
FORBIDDEN_BLOCK_TYPES: tuple[str, ...] = (
    "reward_function",
    "state_definition",
    "action_space",
    "baseline",
    "holding_limit",
)

#: Pipeline / validation status vocabulary.
PARSE_STATUS_VALUES: tuple[str, ...] = (
    "passed",
    "degraded",
    "failed",
    "needs_review",
)

RETRIEVAL_STATUS_VALUES: tuple[str, ...] = ("ok", "unavailable")


def _require(obj: dict[str, Any], key: str, where: str) -> None:
    if key not in obj:
        raise ValueError(f"{where} missing required key {key!r}")


# ---------------------------------------------------------------------------
# Canonical model
# ---------------------------------------------------------------------------


@dataclass
class CanonicalProvenance:
    page: int
    bbox: list[float] | None = None
    source_item_id: str | None = None
    char_start: int = 0
    char_end: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalProvenance":
        _require(data, "page", "provenance")
        return cls(
            page=int(data["page"]),
            bbox=data.get("bbox"),
            source_item_id=data.get("source_item_id"),
            char_start=int(data.get("char_start", 0)),
            char_end=int(data.get("char_end", 0)),
        )


@dataclass
class CanonicalBlock:
    block_id: str
    paper_id: str
    block_type: str
    section_id: str | None
    order: int
    text: str
    pages: list[int] = field(default_factory=list)
    provenance: list[CanonicalProvenance] = field(default_factory=list)
    source_items: list[str] = field(default_factory=list)
    relations: dict[str, Any] = field(default_factory=dict)
    content: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "paper_id": self.paper_id,
            "block_type": self.block_type,
            "section_id": self.section_id,
            "order": self.order,
            "text": self.text,
            "pages": list(self.pages),
            "provenance": [p.to_dict() for p in self.provenance],
            "source_items": list(self.source_items),
            "relations": dict(self.relations),
            "content": dict(self.content),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalBlock":
        for key in (
            "block_id",
            "paper_id",
            "block_type",
            "order",
            "text",
        ):
            _require(data, key, "canonical block")
        block_type = str(data["block_type"])
        if block_type not in BLOCK_TYPES:
            raise ValueError(f"illegal canonical block_type {block_type!r}")
        return cls(
            block_id=str(data["block_id"]),
            paper_id=str(data["paper_id"]),
            block_type=block_type,
            section_id=data.get("section_id"),
            order=int(data["order"]),
            text=str(data["text"]),
            pages=[int(p) for p in data.get("pages", [])],
            provenance=[
                CanonicalProvenance.from_dict(p)
                for p in data.get("provenance", [])
            ],
            source_items=list(data.get("source_items", [])),
            relations=dict(data.get("relations", {})),
            content=dict(data.get("content", {})),
        )


@dataclass
class CanonicalSection:
    section_id: str
    paper_id: str
    title: str
    level: int
    parent_section_id: str | None
    order: int
    heading_block_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalSection":
        for key in (
            "section_id",
            "paper_id",
            "title",
            "level",
            "parent_section_id",
            "order",
            "heading_block_id",
        ):
            _require(data, key, "canonical section")
        return cls(
            section_id=str(data["section_id"]),
            paper_id=str(data["paper_id"]),
            title=str(data["title"]),
            level=int(data["level"]),
            parent_section_id=data.get("parent_section_id"),
            order=int(data["order"]),
            heading_block_id=str(data["heading_block_id"]),
        )


@dataclass
class CanonicalDocument:
    paper_id: str
    file_id: str | None
    source_sha256: str
    parse_run_id: str
    parser_name: str
    parser_version: str
    parser_config_hash: str
    canonical_schema_version: str
    normalizer_version: str
    page_count: int
    language: str
    section_count: int
    block_count: int
    parse_status: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalDocument":
        required = (
            "paper_id",
            "file_id",
            "source_sha256",
            "parse_run_id",
            "parser_name",
            "parser_version",
            "parser_config_hash",
            "canonical_schema_version",
            "normalizer_version",
            "page_count",
            "language",
            "section_count",
            "block_count",
            "parse_status",
            "created_at",
        )
        for key in required:
            _require(data, key, "canonical document")
        return cls(**{key: data[key] for key in required})


# ---------------------------------------------------------------------------
# Derived data model
# ---------------------------------------------------------------------------


@dataclass
class SourceRef:
    block_id: str
    char_start: int
    char_end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRef":
        _require(data, "block_id", "source_ref")
        return cls(
            block_id=str(data["block_id"]),
            char_start=int(data.get("char_start", 0)),
            char_end=int(data.get("char_end", 0)),
        )


@dataclass
class RetrievalChunk:
    chunk_id: str
    paper_id: str
    parse_run_id: str
    chunker_version: str
    section_id: str | None
    section_path: list[str]
    pages: list[int]
    source_refs: list[SourceRef]
    body_text: str
    context_prefix: str
    retrieval_text: str
    token_count: int
    block_types: list[str]
    #: Canonical row range of this chunk's table row group, derived from
    #: ``content.cells[].row/row_span`` and ``n_rows``. ``None`` when the table
    #: has no reliable cells (fallback text split; never fabricated).
    table_row_start: int | None = None
    table_row_end: int | None = None
    #: Caption text bound to the chunk's structured parent (machine-readable
    #: structured metadata; may also appear in ``body_text``).
    caption_text: str | None = None
    #: True when ``context_prefix`` is a deterministic compact prefix because
    #: the full ``section_path`` could not fit in the token budget; the full
    #: path is always preserved in ``section_path``.
    context_prefix_compacted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "paper_id": self.paper_id,
            "parse_run_id": self.parse_run_id,
            "chunker_version": self.chunker_version,
            "section_id": self.section_id,
            "section_path": list(self.section_path),
            "pages": list(self.pages),
            "source_refs": [r.to_dict() for r in self.source_refs],
            "body_text": self.body_text,
            "context_prefix": self.context_prefix,
            "retrieval_text": self.retrieval_text,
            "token_count": self.token_count,
            "block_types": list(self.block_types),
            "table_row_start": self.table_row_start,
            "table_row_end": self.table_row_end,
            "caption_text": self.caption_text,
            "context_prefix_compacted": self.context_prefix_compacted,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetrievalChunk":
        for key in (
            "chunk_id",
            "paper_id",
            "parse_run_id",
            "chunker_version",
            "body_text",
            "retrieval_text",
        ):
            _require(data, key, "retrieval chunk")
        return cls(
            chunk_id=str(data["chunk_id"]),
            paper_id=str(data["paper_id"]),
            parse_run_id=str(data["parse_run_id"]),
            chunker_version=str(data["chunker_version"]),
            section_id=data.get("section_id"),
            section_path=list(data.get("section_path", [])),
            pages=[int(p) for p in data.get("pages", [])],
            source_refs=[
                SourceRef.from_dict(r) for r in data.get("source_refs", [])
            ],
            body_text=str(data["body_text"]),
            context_prefix=str(data.get("context_prefix", "")),
            retrieval_text=str(data["retrieval_text"]),
            token_count=int(data.get("token_count", 0)),
            block_types=list(data.get("block_types", [])),
            table_row_start=(
                int(data["table_row_start"]) if data.get("table_row_start") is not None else None
            ),
            table_row_end=(
                int(data["table_row_end"]) if data.get("table_row_end") is not None else None
            ),
            caption_text=data.get("caption_text"),
            context_prefix_compacted=bool(data.get("context_prefix_compacted", False)),
        )


@dataclass
class MarkdownMapEntry:
    md_line_start: int
    md_line_end: int
    block_ids: list[str]
    parse_run_id: str
    renderer_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "md_line_start": self.md_line_start,
            "md_line_end": self.md_line_end,
            "block_ids": list(self.block_ids),
            "parse_run_id": self.parse_run_id,
            "renderer_version": self.renderer_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarkdownMapEntry":
        for key in ("md_line_start", "md_line_end", "block_ids"):
            _require(data, key, "markdown_map entry")
        return cls(
            md_line_start=int(data["md_line_start"]),
            md_line_end=int(data["md_line_end"]),
            block_ids=list(data["block_ids"]),
            parse_run_id=str(data.get("parse_run_id", "")),
            renderer_version=str(data.get("renderer_version", "")),
        )


@dataclass
class RetrievalHit:
    paper_id: str
    chunk_id: str | None
    score: float | None
    retrieval_method: str
    section_path: list[str]
    pages: list[int]
    source_refs: list[SourceRef]
    text: str
    rank: int
    bm25_rank: int | None = None
    dense_rank: int | None = None
    rrf_rank: int | None = None
    rerank_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "chunk_id": self.chunk_id,
            "score": self.score,
            "retrieval_method": self.retrieval_method,
            "section_path": list(self.section_path),
            "pages": list(self.pages),
            "source_refs": [r.to_dict() for r in self.source_refs],
            "text": self.text,
            "rank": self.rank,
            "bm25_rank": self.bm25_rank,
            "dense_rank": self.dense_rank,
            "rrf_rank": self.rrf_rank,
            "rerank_score": self.rerank_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetrievalHit":
        return cls(
            paper_id=str(data["paper_id"]),
            chunk_id=data.get("chunk_id"),
            score=data.get("score"),
            retrieval_method=str(data["retrieval_method"]),
            section_path=list(data.get("section_path", [])),
            pages=[int(p) for p in data.get("pages", [])],
            source_refs=[
                SourceRef.from_dict(r) for r in data.get("source_refs", [])
            ],
            text=str(data.get("text", "")),
            rank=int(data.get("rank", 0)),
            bm25_rank=data.get("bm25_rank"),
            dense_rank=data.get("dense_rank"),
            rrf_rank=data.get("rrf_rank"),
            rerank_score=data.get("rerank_score"),
        )


@dataclass
class RetrievalResult:
    status: str  # ok / unavailable
    method: str
    hits: list[RetrievalHit] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "method": self.method,
            "hits": [h.to_dict() for h in self.hits],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetrievalResult":
        return cls(
            status=str(data["status"]),
            method=str(data["method"]),
            hits=[RetrievalHit.from_dict(h) for h in data.get("hits", [])],
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
            warnings=list(data.get("warnings", [])),
        )


@dataclass
class ParsePaperResult:
    paper_id: str
    file_id: str | None
    parse_run_id: str | None
    status: str  # passed / degraded / needs_review / failed / blocked
    parser_used: str | None
    output_dir: str | None
    warnings: list[str]
    blockers: list[str]
    error_code: str | None
    error_message: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Eval gold schema (FR-014)
# ---------------------------------------------------------------------------


@dataclass
class GoldQuery:
    paper_id: str
    query: str
    query_type: str
    gold_block_ids: list[str]
    gold_source_spans: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "query": self.query,
            "query_type": self.query_type,
            "gold_block_ids": list(self.gold_block_ids),
            "gold_source_spans": (
                list(self.gold_source_spans)
                if self.gold_source_spans is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoldQuery":
        for key in ("paper_id", "query", "query_type", "gold_block_ids"):
            _require(data, key, "gold query")
        return cls(
            paper_id=str(data["paper_id"]),
            query=str(data["query"]),
            query_type=str(data["query_type"]),
            gold_block_ids=list(data["gold_block_ids"]),
            gold_source_spans=data.get("gold_source_spans"),
        )
