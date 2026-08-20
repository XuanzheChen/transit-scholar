"""Layer2 Step1: single-paper PDF parsing and retrieval infrastructure.

The package ``__init__`` is deliberately lazy: importing ``transit_scholar.layer2``
must not import ``transit_scholar.config`` (or the Layer1 stack) at module-load
time. Standalone invocations such as ``python -m transit_scholar.layer2.smoke``
need to point ``TRANSIT_SCHOLAR_DATA_DIR`` at an isolated root BEFORE the first
``transit_scholar.config`` import; lazy re-exports guarantee the env bootstrap
runs first.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, str] = {
    # config
    "Layer2Config": "transit_scholar.layer2.config",
    # pipeline
    "parse_paper": "transit_scholar.layer2.pipeline",
    "rebuild_derived": "transit_scholar.layer2.pipeline",
    # retrieval api
    "grep_paper": "transit_scholar.layer2.retrieval.api",
    "search_bm25": "transit_scholar.layer2.retrieval.api",
    "search_dense": "transit_scholar.layer2.retrieval.api",
    "search_hybrid": "transit_scholar.layer2.retrieval.api",
    "read_blocks": "transit_scholar.layer2.retrieval.api",
    "read_context": "transit_scholar.layer2.retrieval.api",
    "read_section": "transit_scholar.layer2.retrieval.api",
    "build_retrieval": "transit_scholar.layer2.retrieval.api",
    # schema
    "CanonicalDocument": "transit_scholar.layer2.schema",
    "CanonicalSection": "transit_scholar.layer2.schema",
    "CanonicalBlock": "transit_scholar.layer2.schema",
    "CanonicalProvenance": "transit_scholar.layer2.schema",
    "RetrievalChunk": "transit_scholar.layer2.schema",
    "RetrievalHit": "transit_scholar.layer2.schema",
    "RetrievalResult": "transit_scholar.layer2.schema",
    "ParsePaperResult": "transit_scholar.layer2.schema",
    "SourceRef": "transit_scholar.layer2.schema",
    "GoldQuery": "transit_scholar.layer2.schema",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
