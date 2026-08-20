"""Public single-paper retrieval API (re-exports)."""

from __future__ import annotations

from transit_scholar.layer2.retrieval.api import (
    build_retrieval,
    grep_paper,
    read_blocks,
    read_context,
    read_section,
    search_bm25,
    search_dense,
    search_hybrid,
)

__all__ = [
    "grep_paper",
    "search_bm25",
    "search_dense",
    "search_hybrid",
    "read_blocks",
    "read_context",
    "read_section",
    "build_retrieval",
]
