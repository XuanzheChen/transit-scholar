"""Layer2 Step1 centralized V1 configuration.

All V1 defaults required by FR-001 / AC-L2S1-CONFIG-02 live here (or in the
per-run manifests) so no magic constant is scattered through the codebase:
canonical schema version, normalizer version, parser names, paragraph
reconstruction strategy, validation thresholds, chunk token bounds, retrieval
top_k / fusion / reranker parameters and the embedding/reranker provider
config plus their API-key environment variable names.

Keys are read through the existing ``config.py`` dotenv boundary
(``_load_project_dotenv``, ``override=False``); key values are never written
to manifests, logs, retrieval results or error messages.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from transit_scholar.config import Settings


#: Official Docling environment variable for a local model directory. When
#: set, the Docling adapter must make it effective through the installed
#: Docling public ``PdfPipelineOptions.artifacts_path`` API (never silently
#: ignore it). The path is not a secret; it is recorded in parser config /
#: config hash / benchmark manifest so the manifest describes facts.
DOCLING_ARTIFACTS_PATH_ENV = "DOCLING_ARTIFACTS_PATH"


def resolve_docling_artifacts_path() -> str | None:
    """Resolve the ``DOCLING_ARTIFACTS_PATH`` env value (``None`` if unset).

    Returns the expanded path string when the variable is set to a non-empty
    value. Directory validation happens where the value is actually applied
    (the Docling adapter) so the structured error carries parser context.
    """
    raw = os.environ.get(DOCLING_ARTIFACTS_PATH_ENV, "").strip()
    if not raw:
        return None
    return str(Path(raw).expanduser())


#: Reserved environment variable names for the cloud embedding/reranker
#: provider boundary (FR-010).
EMBEDDING_PROVIDER_ENV = "TRANSIT_SCHOLAR_EMBEDDING_PROVIDER"
EMBEDDING_API_KEY_ENV = "TRANSIT_SCHOLAR_EMBEDDING_API_KEY"
EMBEDDING_MODEL_ENV = "TRANSIT_SCHOLAR_EMBEDDING_MODEL"
EMBEDDING_DIMENSION_ENV = "TRANSIT_SCHOLAR_EMBEDDING_DIMENSION"
RERANKER_PROVIDER_ENV = "TRANSIT_SCHOLAR_RERANKER_PROVIDER"
RERANKER_API_KEY_ENV = "TRANSIT_SCHOLAR_RERANKER_API_KEY"
RERANKER_MODEL_ENV = "TRANSIT_SCHOLAR_RERANKER_MODEL"
JINA_API_KEY_ENV = "JINA_API_KEY"
BLOCK_NETWORK_ENV = "TRANSIT_SCHOLAR_BLOCK_NETWORK"
RETRIEVAL_ALLOW_NETWORK_ENV = "TRANSIT_SCHOLAR_RETRIEVAL_ALLOW_NETWORK"

#: All reserved Layer2 env var names, exposed for tests and safety scans.
RESERVED_ENV_NAMES = (
    EMBEDDING_PROVIDER_ENV,
    EMBEDDING_API_KEY_ENV,
    EMBEDDING_MODEL_ENV,
    EMBEDDING_DIMENSION_ENV,
    RERANKER_PROVIDER_ENV,
    RERANKER_API_KEY_ENV,
    RERANKER_MODEL_ENV,
    JINA_API_KEY_ENV,
)


@dataclass(frozen=True)
class Layer2Config:
    """Immutable V1 Layer2 configuration.

    ``Settings`` provides the data-root derived paths and the env-driven
    provider fields; every parameter that has a frozen V1 default value lives
    on this dataclass so it can be versioned (``config_hash``) and written
    into the parse / retrieval manifests.
    """

    # --- canonical -----------------------------------------------------------
    canonical_schema_version: str = "1.0"
    normalizer_version: str = "1.0"
    paragraph_reconstruction: str = "conservative"
    merge_cross_page_paragraph: bool = True

    # --- markdown ------------------------------------------------------------
    renderer_version: str = "1.0"
    inline_machine_metadata: bool = False
    page_markers: bool = False
    save_figure_assets: bool = True

    # --- chunking version ------------------------------------------------------
    chunker_version: str = "1.0"

    # --- parser --------------------------------------------------------------
    parser_primary: str = "docling"
    parser_fallback: str = "mineru"
    parser_diagnostic: str = "pymupdf4llm"
    parser_native: str = "pymupdf_native"
    #: Optional deterministic parser pin (e.g. the smoke sets
    #: ``pymupdf_native`` so the offline real-PDF flow never falls into the
    #: heavy installed docling/mineru pipelines).
    parser_override: str | None = None
    llm_parser_repair: bool = False
    multi_parser_item_voting: bool = False

    # --- validation thresholds ------------------------------------------------
    degraded_if_meaningful_text_page_ratio_below: float = 0.80
    degraded_if_replacement_char_ratio_above: float = 0.01
    degraded_if_suspicious_duplicate_ratio_above: float = 0.15
    degraded_if_zero_headings_min_pages: int = 4
    failed_if_meaningful_text_page_ratio_below: float = 0.05
    minimum_meaningful_text_chars: int = 10
    reading_order_jump_threshold: int = 3

    # --- chunking -------------------------------------------------------------
    shared_for_bm25_and_dense: bool = True
    min_tokens: int = 120
    target_tokens: int = 400
    soft_max_tokens: int = 650
    hard_max_tokens: int = 900
    fixed_overlap_tokens: int = 0
    cross_section: bool = False

    # --- retrieval ------------------------------------------------------------
    store: str = "lancedb"
    bm25_top_k: int = 20
    dense_top_k: int = 20
    fusion: str = "rrf"
    fusion_candidate_k: int = 30
    final_top_k: int = 8
    rerank_enabled: bool = True
    #: Per-list reciprocal-rank-fusion weights (global, apply to every query;
    #: the 1.0/1.0 default is exactly the unweighted RRF of V1).
    rrf_bm25_weight: float = 1.0
    rrf_dense_weight: float = 1.0

    # --- embedding / reranker cloud defaults ---------------------------------
    embedding_provider_default: str = "jina"
    embedding_model_default: str = "jina-embeddings-v3"
    embedding_dimension_default: int = 1024
    reranker_provider_default: str = "jina"
    reranker_model_default: str = "jina-reranker-v3"

    # --- data-root derived paths (copied from Settings at construction) -------
    data_root: Path = Path("data")
    layer2_dir: Path = Path("data/layer2")
    layer2_parsed_dir: Path = Path("data/layer2/parsed")
    layer2_retrieval_dir: Path = Path("data/layer2/retrieval")

    # --- provider resolution (env-driven) --------------------------------------
    embedding_provider: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    jina_api_key: str | None = None
    reranker_provider: str | None = None
    reranker_api_key: str | None = None
    reranker_model: str | None = None
    retrieval_allow_network: bool = False
    block_network: bool = False

    @classmethod
    def from_settings(cls, s: Settings) -> "Layer2Config":
        """Build a Layer2Config from a ``Settings`` instance."""
        data_root = Path(s.data_root)
        return cls(
            data_root=data_root,
            layer2_dir=data_root / "layer2",
            layer2_parsed_dir=data_root / "layer2" / "parsed",
            layer2_retrieval_dir=data_root / "layer2" / "retrieval",
            embedding_provider=s.layer2_embedding_provider,
            embedding_api_key=s.layer2_embedding_api_key,
            embedding_model=s.layer2_embedding_model,
            embedding_dimension=s.layer2_embedding_dimension,
            jina_api_key=s.jina_api_key,
            reranker_provider=s.layer2_reranker_provider,
            reranker_api_key=s.layer2_reranker_api_key,
            reranker_model=s.layer2_reranker_model,
            retrieval_allow_network=s.layer2_retrieval_allow_network,
            block_network=s.layer2_block_network,
        )

    @property
    def resolved_embedding_model(self) -> str:
        return self.embedding_model or self.embedding_model_default

    @property
    def resolved_embedding_provider(self) -> str:
        return self.embedding_provider or self.embedding_provider_default

    @property
    def resolved_embedding_dimension(self) -> int:
        return self.embedding_dimension or self.embedding_dimension_default

    @property
    def resolved_reranker_model(self) -> str:
        return self.reranker_model or self.reranker_model_default

    @property
    def resolved_reranker_provider(self) -> str:
        return self.reranker_provider or self.reranker_provider_default

    def parsed_paper_dir(self, paper_id: str) -> Path:
        return self.layer2_parsed_dir / paper_id

    def retrieval_paper_dir(self, paper_id: str) -> Path:
        return self.layer2_retrieval_dir / paper_id

    def chunk_config_hash(self) -> str:
        return _stable_hash(
            {
                "shared": self.shared_for_bm25_and_dense,
                "min": self.min_tokens,
                "target": self.target_tokens,
                "soft_max": self.soft_max_tokens,
                "hard_max": self.hard_max_tokens,
                "overlap": self.fixed_overlap_tokens,
                "cross_section": self.cross_section,
            }
        )

    def as_manifest_section(self) -> dict[str, object]:
        """Versioned config snapshot written into parser/retrieval manifests."""
        return {
            "canonical_schema_version": self.canonical_schema_version,
            "normalizer_version": self.normalizer_version,
            "renderer_version": self.renderer_version,
            "chunker_version": self.chunker_version,
            "parser_primary": self.parser_primary,
            "parser_fallback": self.parser_fallback,
            "parser_diagnostic": self.parser_diagnostic,
            "parser_native": self.parser_native,
            "parser_override": self.parser_override,
            "paragraph_reconstruction": self.paragraph_reconstruction,
            "merge_cross_page_paragraph": self.merge_cross_page_paragraph,
            "validation": {
                "meaningful_text_page_ratio_below": (
                    self.degraded_if_meaningful_text_page_ratio_below
                ),
                "replacement_char_ratio_above": (
                    self.degraded_if_replacement_char_ratio_above
                ),
                "suspicious_duplicate_ratio_above": (
                    self.degraded_if_suspicious_duplicate_ratio_above
                ),
                "zero_headings_min_pages": self.degraded_if_zero_headings_min_pages,
                "hard_empty_text_page_ratio_below": (
                    self.failed_if_meaningful_text_page_ratio_below
                ),
            },
            "chunking": {
                "shared_for_bm25_and_dense": self.shared_for_bm25_and_dense,
                "min_tokens": self.min_tokens,
                "target_tokens": self.target_tokens,
                "soft_max_tokens": self.soft_max_tokens,
                "hard_max_tokens": self.hard_max_tokens,
                "fixed_overlap_tokens": self.fixed_overlap_tokens,
                "cross_section": self.cross_section,
            },
            "retrieval": {
                "store": self.store,
                "bm25_top_k": self.bm25_top_k,
                "dense_top_k": self.dense_top_k,
                "fusion": self.fusion,
                "fusion_candidate_k": self.fusion_candidate_k,
                "final_top_k": self.final_top_k,
                "rerank_enabled": self.rerank_enabled,
                "rrf_bm25_weight": self.rrf_bm25_weight,
                "rrf_dense_weight": self.rrf_dense_weight,
            },
            "embedding": {
                "model": self.resolved_embedding_model,
                "dimension": self.resolved_embedding_dimension,
                "provider": self.resolved_embedding_provider,
                "provider_env": EMBEDDING_PROVIDER_ENV,
                "api_key_env": EMBEDDING_API_KEY_ENV,
                "default_api_key_env": JINA_API_KEY_ENV,
                "model_env": EMBEDDING_MODEL_ENV,
                "dimension_env": EMBEDDING_DIMENSION_ENV,
            },
            "reranker": {
                "model": self.resolved_reranker_model,
                "provider": self.resolved_reranker_provider,
                "provider_env": RERANKER_PROVIDER_ENV,
                "api_key_env": RERANKER_API_KEY_ENV,
                "default_api_key_env": JINA_API_KEY_ENV,
                "model_env": RERANKER_MODEL_ENV,
            },
        }


def _stable_hash(payload: dict[str, object]) -> str:
    """Deterministic config hash over a JSON-serializable payload."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def config_hash(payload: dict[str, object]) -> str:
    """Public deterministic config-hash helper (also used by parser adapters)."""
    return _stable_hash(payload)
