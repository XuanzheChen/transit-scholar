"""Layer2 Step1 config tests (AC-L2S1-CONFIG-01/02/03)."""

from __future__ import annotations

import os

from transit_scholar.layer2.config import Layer2Config

EXPECTED_CHUNK_TOKENS = (120, 400, 650, 900)
EXPECTED_RETRIEVAL = (20, 20, "rrf", 30, 8)
EXPECTED_EMBEDDING_ENV = (
    "TRANSIT_SCHOLAR_EMBEDDING_PROVIDER",
    "TRANSIT_SCHOLAR_EMBEDDING_API_KEY",
    "TRANSIT_SCHOLAR_EMBEDDING_MODEL",
    "TRANSIT_SCHOLAR_EMBEDDING_DIMENSION",
)
EXPECTED_RERANKER_ENV = (
    "TRANSIT_SCHOLAR_RERANKER_PROVIDER",
    "TRANSIT_SCHOLAR_RERANKER_API_KEY",
    "TRANSIT_SCHOLAR_RERANKER_MODEL",
)


def test_config_layer2_paths_created_by_init_directories(project_tmp_path):
    """AC-L2S1-CONFIG-01: Settings derives Layer2 paths from data_root and
    init_directories() creates them (idempotently)."""
    from transit_scholar.config import Settings

    s = Settings(data_root=project_tmp_path)
    assert s.layer2_dir == project_tmp_path / "layer2"
    assert s.layer2_parsed_dir == project_tmp_path / "layer2" / "parsed"
    assert s.layer2_retrieval_dir == project_tmp_path / "layer2" / "retrieval"

    s.init_directories()
    assert (project_tmp_path / "layer2" / "parsed").is_dir()
    assert (project_tmp_path / "layer2" / "retrieval").is_dir()
    # idempotent
    s.init_directories()
    assert (project_tmp_path / "layer2" / "parsed").is_dir()


def test_config_layer2_parsed_paper_dir_helper(project_tmp_path):
    from transit_scholar.config import Settings

    s = Settings(data_root=project_tmp_path)
    assert s.layer2_parsed_paper_dir("paper_x") == (
        project_tmp_path / "layer2" / "parsed" / "paper_x"
    )


def test_config_all_v1_defaults_centrally_readable():
    """AC-L2S1-CONFIG-02: every frozen V1 default is readable from Layer2Config
    through a single access path and the manifest snapshot."""
    from transit_scholar.config import Settings

    config = Layer2Config.from_settings(Settings(data_root="data"))

    assert config.canonical_schema_version == "1.0"
    assert config.normalizer_version == "1.0"
    assert config.parser_primary == "docling"
    assert config.parser_fallback == "mineru"
    assert config.parser_diagnostic == "pymupdf4llm"
    assert config.parser_native == "pymupdf_native"
    assert config.paragraph_reconstruction == "conservative"
    assert config.merge_cross_page_paragraph is True

    assert (config.min_tokens, config.target_tokens, config.soft_max_tokens, config.hard_max_tokens) == EXPECTED_CHUNK_TOKENS
    assert (config.bm25_top_k, config.dense_top_k, config.fusion, config.fusion_candidate_k, config.final_top_k) == EXPECTED_RETRIEVAL
    assert config.fixed_overlap_tokens == 0
    assert config.cross_section is False

    assert (
        config.degraded_if_meaningful_text_page_ratio_below,
        config.degraded_if_replacement_char_ratio_above,
        config.degraded_if_suspicious_duplicate_ratio_above,
    ) == (0.80, 0.01, 0.15)
    assert config.embedding_model_default == "jina-embeddings-v3"
    assert config.resolved_embedding_provider == "jina"
    assert config.embedding_dimension_default == 1024
    assert config.reranker_model_default == "jina-reranker-v3"
    assert config.resolved_reranker_provider == "jina"
    assert config.store == "lancedb"

    # manifest snapshot carries every value in a versioned form
    section = config.as_manifest_section()
    assert section["chunking"]["min_tokens"] == 120
    assert section["chunking"]["hard_max_tokens"] == 900
    assert section["retrieval"]["bm25_top_k"] == 20
    assert section["retrieval"]["fusion"] == "rrf"
    assert section["retrieval"]["fusion_candidate_k"] == 30
    assert section["retrieval"]["final_top_k"] == 8
    assert section["retrieval"]["rrf_bm25_weight"] == 1.0
    assert section["retrieval"]["rrf_dense_weight"] == 1.0
    assert config.rrf_bm25_weight == 1.0
    assert config.rrf_dense_weight == 1.0
    assert section["embedding"]["model"] == "jina-embeddings-v3"
    assert section["embedding"]["provider"] == "jina"
    assert section["embedding"]["default_api_key_env"] == "JINA_API_KEY"
    assert section["embedding"]["dimension"] == 1024


def test_config_changing_chunk_bounds_changes_behavior():
    """AC-L2S1-CONFIG-02: changing a config value changes behavior (chunk hash)."""
    from transit_scholar.config import Settings

    base = Layer2Config.from_settings(Settings(data_root="data"))
    changed = Layer2Config.from_settings(Settings(data_root="data"))
    object.__setattr__(changed, "target_tokens", 300)
    assert base.chunk_config_hash() != changed.chunk_config_hash()


def test_config_env_provider_resolution(monkeypatch):
    """AC-L2S1-CONFIG-03: reserved env vars are honored by Settings."""
    from transit_scholar.config import Settings

    monkeypatch.setenv("TRANSIT_SCHOLAR_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("TRANSIT_SCHOLAR_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("TRANSIT_SCHOLAR_EMBEDDING_MODEL", "qwen3-embedding")
    monkeypatch.setenv("TRANSIT_SCHOLAR_EMBEDDING_DIMENSION", "768")
    monkeypatch.setenv("TRANSIT_SCHOLAR_RERANKER_PROVIDER", "dashscope")
    monkeypatch.setenv("TRANSIT_SCHOLAR_RERANKER_API_KEY", "rerank-key")
    monkeypatch.setenv("TRANSIT_SCHOLAR_RERANKER_MODEL", "qwen3-reranker")

    s = Settings(data_root="data")
    config = Layer2Config.from_settings(s)
    assert config.embedding_provider == "dashscope"
    assert config.embedding_api_key == "test-key"
    assert config.embedding_model == "qwen3-embedding"
    assert config.embedding_dimension == 768
    assert config.reranker_provider == "dashscope"
    assert config.reranker_api_key == "rerank-key"
    assert config.reranker_model == "qwen3-reranker"


def test_config_jina_key_is_shared_by_default_providers(monkeypatch):
    """The zero-payment default uses one Jina key for embedding and rerank."""
    for name in (
        "TRANSIT_SCHOLAR_EMBEDDING_PROVIDER",
        "TRANSIT_SCHOLAR_EMBEDDING_API_KEY",
        "TRANSIT_SCHOLAR_RERANKER_PROVIDER",
        "TRANSIT_SCHOLAR_RERANKER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JINA_API_KEY", "jina-test-key")
    monkeypatch.setenv("TRANSIT_SCHOLAR_RETRIEVAL_ALLOW_NETWORK", "true")
    monkeypatch.setenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", "false")

    from transit_scholar.config import Settings
    from transit_scholar.layer2.retrieval.providers import (
        resolve_embedding_provider,
        resolve_reranker_provider,
    )

    config = Layer2Config.from_settings(Settings(data_root="data"))
    embedding = resolve_embedding_provider(config)
    reranker = resolve_reranker_provider(config)
    assert embedding.available is True
    assert embedding.info is not None
    assert embedding.info.provider == "jina"
    assert embedding.info.model == "jina-embeddings-v3"
    assert embedding.dimension() == 1024
    assert reranker.available is True
    assert reranker.info is not None
    assert reranker.info.provider == "jina"
    assert reranker.info.model == "jina-reranker-v3"


def test_config_docling_artifacts_path_resolution(monkeypatch):
    """DOCLING_ARTIFACTS_PATH is resolved by the public helper: ``None`` when
    unset/blank, the expanded path string when set. The value feeds the
    Docling adapter config/config_hash so the manifest reflects reality."""
    from transit_scholar.layer2.config import (
        DOCLING_ARTIFACTS_PATH_ENV,
        resolve_docling_artifacts_path,
    )

    assert DOCLING_ARTIFACTS_PATH_ENV == "DOCLING_ARTIFACTS_PATH"

    monkeypatch.delenv(DOCLING_ARTIFACTS_PATH_ENV, raising=False)
    assert resolve_docling_artifacts_path() is None

    monkeypatch.setenv(DOCLING_ARTIFACTS_PATH_ENV, "   ")
    assert resolve_docling_artifacts_path() is None

    monkeypatch.setenv(DOCLING_ARTIFACTS_PATH_ENV, r"C:\models\docling")
    assert resolve_docling_artifacts_path() == r"C:\models\docling"

    monkeypatch.setenv(DOCLING_ARTIFACTS_PATH_ENV, "~/hf/models")
    resolved = resolve_docling_artifacts_path()
    assert resolved is not None
    assert "~" not in resolved  # expanduser applied


def test_config_env_unset_resolves_to_none_and_unavailable(monkeypatch):
    """AC-L2S1-CONFIG-03: unset env vars resolve to None and the retrieval
    boundary reports unavailable instead of raising KeyError."""
    for name in (
        "TRANSIT_SCHOLAR_EMBEDDING_PROVIDER",
        "TRANSIT_SCHOLAR_EMBEDDING_API_KEY",
        "TRANSIT_SCHOLAR_EMBEDDING_MODEL",
        "TRANSIT_SCHOLAR_EMBEDDING_DIMENSION",
        "TRANSIT_SCHOLAR_RERANKER_PROVIDER",
        "TRANSIT_SCHOLAR_RERANKER_API_KEY",
        "TRANSIT_SCHOLAR_RERANKER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    from transit_scholar.config import Settings

    s = Settings(data_root="data")
    config = Layer2Config.from_settings(s)
    assert config.embedding_api_key is None
    assert config.reranker_api_key is None

    from transit_scholar.layer2.retrieval.providers import (
        resolve_embedding_provider,
        resolve_reranker_provider,
    )

    embedding = resolve_embedding_provider(config)
    assert embedding.available is False
    assert embedding.reason == "missing_api_key"

    reranker = resolve_reranker_provider(config)
    assert reranker.available is False
    assert reranker.reason == "missing_api_key"
