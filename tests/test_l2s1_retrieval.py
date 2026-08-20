"""Layer2 Step1 retrieval API tests (AC-L2S1-RETRIEVAL-01..08)."""

from __future__ import annotations

import inspect
import io
import json
import shutil

import pytest

from transit_scholar.layer2 import build_retrieval
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from transit_scholar.layer2.paths import retrieval_index_dir
from transit_scholar.layer2.retrieval import api as retrieval_api
from transit_scholar.layer2.retrieval.providers import UnavailableError
from tests.l2s1_fixtures import (
    FakeEmbeddingProvider,
    FakeRerankerProvider,
    make_ready_paper,
    patch_parsers,
    read_artifacts,
)


class ControlledEmbeddingProvider(FakeEmbeddingProvider):
    """Embeddings that rank chunks containing ``reinforcement`` closest to the
    query, giving deterministic control over dense ordering."""

    def _vector(self, text: str) -> list[float]:
        if "reinforcement" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class RateLimitedRerankerProvider(FakeRerankerProvider):
    def rerank(self, query: str, documents: list[str], top_k: int):
        raise UnavailableError("rerank rate limited", error_code="rate_limited")


class DroppingRerankerProvider(FakeRerankerProvider):
    """Reranker that returns fewer documents than requested: the protected-set
    padding must bring back the missing RRF top-k members."""

    def __init__(self, keep: int):
        super().__init__()
        self._keep = max(0, keep)

    def rerank(self, query: str, documents: list[str], top_k: int):
        scored = super().rerank(query, documents, top_k)
        return scored[: self._keep]


def _paper_with_items(project_tmp_path, monkeypatch, items, *, l2_config):
    paper_id, file_id, pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=items, page_count=1)])
    from transit_scholar.layer2.pipeline import parse_paper

    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    return paper_id


def _build_index(project_tmp_path, monkeypatch, l2_config, items, *, embedding=None, reranker=None):
    paper_id = _paper_with_items(project_tmp_path, monkeypatch, items, l2_config=l2_config)
    if embedding is not None:
        monkeypatch.setattr(retrieval_api, "resolve_embedding_provider", lambda config: embedding)
    if reranker is not None:
        monkeypatch.setattr(retrieval_api, "resolve_reranker_provider", lambda config: reranker)
    build_retrieval(paper_id, config=l2_config)
    return paper_id


def _default_items():
    return [
        make_item(item_id="h1", item_type="heading", text="Method", order=0, page=1, level=1, bbox=[70, 60, 530, 80]),
        make_item(item_id="p1", item_type="paragraph", text="Reinforcement learning trains the holding controller.", order=1, page=1, bbox=[70, 100, 530, 120]),
        make_item(item_id="p2", item_type="paragraph", text="Deep neural networks approximate the value function.", order=2, page=1, bbox=[70, 140, 530, 160]),
        make_item(item_id="p3", item_type="paragraph", text="Bus headway regularity is measured by waiting time.", order=3, page=1, bbox=[70, 180, 530, 200]),
    ]


def test_retrieval_api_signatures_and_no_lancedb_leak():
    """AC-L2S1-RETRIEVAL-01: signatures match and no public API leaks LanceDB."""
    from transit_scholar.layer2.retrieval import api

    for name, signature in (
        ("grep_paper", "(paper_id: str, pattern: str, *, config: 'Layer2Config | None' = None) -> 'RetrievalResult'"),
        ("search_bm25", "(paper_id: str, query: str, top_k: int = 20, filters: dict[str, Any] | None = None, *, config: 'Layer2Config | None' = None) -> 'RetrievalResult'"),
        ("search_dense", "(paper_id: str, query: str, top_k: int = 20, filters: dict[str, Any] | None = None, *, config: 'Layer2Config | None' = None) -> 'RetrievalResult'"),
        ("search_hybrid", "(paper_id: str, query: str, top_k: int = 8, rerank: bool = True, filters: dict[str, Any] | None = None, *, config: 'Layer2Config | None' = None) -> 'RetrievalResult'"),
        ("read_blocks", "(paper_id: str, block_ids: list[str], *, config: 'Layer2Config | None' = None) -> 'list[dict[str, Any]]'"),
        ("read_context", "(paper_id: str, block_id: str, before: int = 2, after: int = 2, *, config: 'Layer2Config | None' = None) -> 'list[dict[str, Any]]'"),
        ("read_section", "(paper_id: str, section_id: str, *, config: 'Layer2Config | None' = None) -> 'list[dict[str, Any]]'"),
    ):
        fn = getattr(api, name)
        params = inspect.signature(fn).parameters
        assert "paper_id" in params
        if name in ("search_bm25", "search_dense"):
            assert params["top_k"].default == 20
        if name == "search_hybrid":
            assert params["top_k"].default == 8
            assert params["rerank"].default is True
        if name == "read_context":
            assert params["before"].default == 2
            assert params["after"].default == 2


def test_retrieval_api_returns_and_no_lancedb_objects(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-RETRIEVAL-01: search calls return RetrievalResult; no LanceDB
    type appears in return values."""
    embedding = FakeEmbeddingProvider(available=False)
    paper_id = _build_index(
        project_tmp_path, monkeypatch, l2_config, _default_items(), embedding=embedding
    )
    from transit_scholar.layer2 import search_bm25, search_dense, search_hybrid

    for result in (
        search_bm25(paper_id, "reinforcement", config=l2_config),
        search_dense(paper_id, "reinforcement", config=l2_config),
        search_hybrid(paper_id, "reinforcement", config=l2_config),
    ):
        text = json.dumps(result.to_dict(), default=str)
        assert "lancedb" not in text.lower()


def test_retrieval_hybrid_rrf_fake_backend(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-RETRIEVAL-02: search_hybrid returns <= 8 hits with RRF ordering
    under a fake backend."""
    embedding = ControlledEmbeddingProvider()
    reranker = FakeRerankerProvider()
    paper_id = _build_index(
        project_tmp_path, monkeypatch, l2_config, _default_items(),
        embedding=embedding, reranker=reranker,
    )
    from transit_scholar.layer2 import search_hybrid

    result = search_hybrid(paper_id, "reinforcement learning", config=l2_config)
    assert result.status == "ok"
    assert len(result.hits) <= l2_config.final_top_k
    # ranks are 1-based unique and RRF order is monotonic by score desc
    scores = [h.score for h in result.hits]
    assert scores == sorted(scores, reverse=True)
    assert [h.rank for h in result.hits] == list(range(1, len(result.hits) + 1))


def test_retrieval_dense_unavailable_without_key(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-RETRIEVAL-03: without an API key, dense/hybrid return explicit
    unavailable status -- never a fake successful result."""
    paper_id = _build_index(
        project_tmp_path, monkeypatch, l2_config, _default_items()
    )
    from transit_scholar.layer2 import search_dense, search_hybrid

    dense = search_dense(paper_id, "reinforcement", config=l2_config)
    assert dense.status == "unavailable"
    assert dense.error_code == "missing_api_key"

    hybrid = search_hybrid(paper_id, "reinforcement", config=l2_config)
    assert hybrid.status == "unavailable"
    assert hybrid.error_code == "missing_api_key"


def test_retrieval_provider_import_error_unavailable(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-RETRIEVAL-03: a provider import failure yields unavailable, not a
    crash or a fake result."""
    paper_id = _build_index(
        project_tmp_path, monkeypatch, l2_config, _default_items()
    )

    def _boom(config):
        raise ImportError("provider SDK not installed")

    monkeypatch.setattr(retrieval_api, "resolve_embedding_provider", _boom)
    from transit_scholar.layer2 import search_dense

    result = search_dense(paper_id, "reinforcement", config=l2_config)
    assert result.status == "unavailable"
    assert result.error_code == "provider_dependency_missing"


def test_retrieval_key_never_leaks(project_tmp_path, monkeypatch, l2_config, capsys):
    """AC-L2S1-RETRIEVAL-04: API keys never appear in artifacts, captured
    stdout, or exceptions."""
    import copy

    secret = "SUPERSECRETKEYVALUESCAN123456789"
    config_keyed = copy.copy(l2_config)
    object.__setattr__(config_keyed, "embedding_api_key", secret)
    object.__setattr__(config_keyed, "reranker_api_key", secret)

    paper_id = _build_index(
        project_tmp_path, monkeypatch, config_keyed, _default_items()
    )
    from transit_scholar.layer2 import search_dense, search_hybrid

    search_dense(paper_id, "reinforcement", config=config_keyed)
    search_hybrid(paper_id, "reinforcement", config=config_keyed)
    build_retrieval(paper_id, config=config_keyed)

    captured = capsys.readouterr()
    blob = captured.out + captured.err

    # scan artifacts under the paper's layer2 tree
    for path in (l2_config.layer2_dir).rglob("*"):
        if path.is_file():
            try:
                blob += path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    assert secret not in blob


def test_retrieval_rebuild_from_chunks_identical(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-RETRIEVAL-06: deleting the index and rebuilding from
    retrieval_chunks.jsonl yields identical top-k results."""
    embedding = ControlledEmbeddingProvider()
    paper_id = _build_index(
        project_tmp_path, monkeypatch, l2_config, _default_items(), embedding=embedding
    )
    from transit_scholar.layer2 import search_bm25, search_dense

    first_bm25 = search_bm25(paper_id, "reinforcement learning", config=l2_config)
    first_dense = search_dense(paper_id, "reinforcement learning", config=l2_config)
    assert first_bm25.status == "ok"
    assert first_dense.status == "ok"

    index_dir = retrieval_index_dir(l2_config, paper_id)
    shutil.rmtree(index_dir)
    build_retrieval(paper_id, config=l2_config)

    second_bm25 = search_bm25(paper_id, "reinforcement learning", config=l2_config)
    second_dense = search_dense(paper_id, "reinforcement learning", config=l2_config)
    assert [(h.chunk_id, h.score) for h in second_bm25.hits] == [
        (h.chunk_id, h.score) for h in first_bm25.hits
    ]
    assert [(h.chunk_id, h.score) for h in second_dense.hits] == [
        (h.chunk_id, h.score) for h in first_dense.hits
    ]


def test_retrieval_hybrid_rerank_ordering_and_fields(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-RETRIEVAL-07: hybrid runs BM25 + dense -> RRF -> optional rerank;
    rerank=False returns RRF order; rerank=True reflects a controlled reorder
    and populates bm25_rank/dense_rank/rrf_rank/rerank_score."""
    embedding = ControlledEmbeddingProvider()
    paper_id = _build_index(
        project_tmp_path, monkeypatch, l2_config, _default_items(),
        embedding=embedding, reranker=FakeRerankerProvider(),
    )
    from transit_scholar.layer2 import search_hybrid

    no_rerank = search_hybrid(paper_id, "reinforcement learning", rerank=False, config=l2_config)
    assert no_rerank.status == "ok"
    for hit in no_rerank.hits:
        assert hit.bm25_rank is not None
        assert hit.dense_rank is not None
        assert hit.rrf_rank is not None
        assert hit.rerank_score is None

    # reranker that flips ordering: boost the previously-last RRF candidate
    top_candidates = [h.chunk_id for h in no_rerank.hits]
    assert top_candidates
    last_id = top_candidates[-1]
    biased = FakeRerankerProvider()
    biased._bias = {str(no_rerank.hits.index(h)): 100.0 for h in no_rerank.hits if h.chunk_id == last_id}

    monkeypatch.setattr(retrieval_api, "resolve_reranker_provider", lambda config: biased)
    reranked = search_hybrid(paper_id, "reinforcement learning", rerank=True, config=l2_config)
    assert reranked.status == "ok"
    assert reranked.hits[0].rerank_score is not None
    assert any(h.chunk_id == last_id for h in reranked.hits[:2])
    assert all(h.rrf_rank is not None for h in reranked.hits)


def test_retrieval_hybrid_rerank_failure_is_structured(
    project_tmp_path, monkeypatch, l2_config
):
    """A transient reranker provider failure degrades to the RRF hybrid top-k
    with ``status=ok`` and a structured warning -- the query must stay
    available instead of being dropped."""
    paper_id = _build_index(
        project_tmp_path,
        monkeypatch,
        l2_config,
        _default_items(),
        embedding=ControlledEmbeddingProvider(),
        reranker=RateLimitedRerankerProvider(),
    )

    from transit_scholar.layer2 import search_hybrid

    no_rerank = search_hybrid(
        paper_id, "reinforcement learning", rerank=False, config=l2_config
    )
    result = search_hybrid(
        paper_id,
        "reinforcement learning",
        config=l2_config,
    )
    assert result.status == "ok"
    assert [h.chunk_id for h in result.hits] == [h.chunk_id for h in no_rerank.hits]
    assert result.warnings
    warning = result.warnings[0]
    assert "rerank failed" in warning
    assert "rate_limited" in warning
    assert "rerank rate limited" in warning


def test_retrieval_hybrid_rerank_protected_set(
    project_tmp_path, monkeypatch, l2_config
):
    """The reranker only reorders the hybrid top-k protected set; members the
    provider omits are padded back in RRF order, so
    ``set(hybrid_rerank) == set(hybrid)`` for any provider output."""
    paper_id = _build_index(
        project_tmp_path,
        monkeypatch,
        l2_config,
        _default_items(),
        embedding=ControlledEmbeddingProvider(),
        reranker=DroppingRerankerProvider(keep=1),
    )
    from transit_scholar.layer2 import search_hybrid

    no_rerank = search_hybrid(
        paper_id, "reinforcement learning", rerank=False, config=l2_config
    )
    reranked = search_hybrid(
        paper_id, "reinforcement learning", rerank=True, config=l2_config
    )
    assert reranked.status == "ok"
    assert len(reranked.hits) == len(no_rerank.hits)
    assert {h.chunk_id for h in reranked.hits} == {h.chunk_id for h in no_rerank.hits}
    assert [h.rank for h in reranked.hits] == list(range(1, len(reranked.hits) + 1))


def test_fusion_rrf_weighted_regression():
    """RRF weights are global fusion parameters: the 1.0/1.0 default matches
    the unweighted V1 order, and a changed BM25 weight deterministically
    changes the fused order."""
    from transit_scholar.config import Settings
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.retrieval.fusion import fuse_hybrid
    from transit_scholar.layer2.schema import RetrievalHit

    def hit(chunk_id: str) -> RetrievalHit:
        return RetrievalHit(
            paper_id="p1", chunk_id=chunk_id, score=1.0,
            retrieval_method="hybrid", section_path=["S"], pages=[1],
            source_refs=[], text=chunk_id, rank=1,
        )

    bm25 = [hit("c_a"), hit("c_b"), hit("c_c")]
    dense = [hit("c_b"), hit("c_c"), hit("c_a")]
    config = Layer2Config.from_settings(Settings(data_root="data"))
    assert config.rrf_bm25_weight == 1.0
    assert config.rrf_dense_weight == 1.0

    default_hits, default_warnings = fuse_hybrid(
        "q", bm25, dense, config, rerank=False, top_k=3
    )
    assert default_warnings == []
    assert [h.chunk_id for h in default_hits] == ["c_b", "c_a", "c_c"]

    boosted = Layer2Config.from_settings(Settings(data_root="data"))
    object.__setattr__(boosted, "rrf_bm25_weight", 2.0)
    boosted_hits, _ = fuse_hybrid("q", bm25, dense, boosted, rerank=False, top_k=3)
    assert [h.chunk_id for h in boosted_hits] == ["c_a", "c_b", "c_c"]
    assert [h.chunk_id for h in default_hits] != [h.chunk_id for h in boosted_hits]


def test_store_selection_truthful_both_install_states(
    monkeypatch, l2_lancedb_config, l2_config
):
    """Store selection is install-state independent: with ``lancedb`` present
    the configured LanceDB store is used; without it, an explicit local
    fallback with a truthful warning -- never a fake lancedb success."""
    import importlib.util

    from transit_scholar.layer2.retrieval import api as retrieval_api
    from tests.l2s1_fixtures import FakeEmbeddingProvider, FakeRerankerProvider

    monkeypatch.setattr(
        retrieval_api,
        "resolve_embedding_provider",
        lambda config: FakeEmbeddingProvider(available=False),
    )
    monkeypatch.setattr(
        retrieval_api,
        "resolve_reranker_provider",
        lambda config: FakeRerankerProvider(available=False),
    )

    store, warning = retrieval_api._make_store(l2_lancedb_config, "paper_x")
    if importlib.util.find_spec("lancedb") is not None:
        assert store.store_name == "lancedb"
        assert isinstance(store, retrieval_api.LanceDBStore)
        assert warning is None
    else:
        assert store.store_name == "local"
        assert warning and "dependency_missing" in warning

    local_store, local_warning = retrieval_api._make_store(l2_config, "paper_x")
    assert local_store.store_name == "local"
    assert local_warning is None


def test_build_retrieval_lancedb_absent_falls_back_local(
    project_tmp_path, monkeypatch, l2_lancedb_config
):
    """When lancedb is (simulated) unavailable, ``store=lancedb`` falls back to
    the local store truthfully and BM25 still works."""
    import json
    import sys

    from transit_scholar.layer2 import build_retrieval, search_bm25
    from transit_scholar.layer2.paths import retrieval_manifest_path
    from transit_scholar.layer2.retrieval import api as retrieval_api
    from tests.l2s1_fixtures import FakeEmbeddingProvider

    monkeypatch.setitem(sys.modules, "lancedb", None)
    monkeypatch.setattr(
        retrieval_api,
        "resolve_embedding_provider",
        lambda config: FakeEmbeddingProvider(available=False),
    )

    paper_id = _paper_with_items(
        project_tmp_path, monkeypatch, _default_items(), l2_config=l2_lancedb_config
    )
    build = build_retrieval(paper_id, config=l2_lancedb_config)
    assert build["status"] == "ok"
    manifest = json.loads(
        retrieval_manifest_path(l2_lancedb_config, paper_id).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["store"] == "local"
    assert any("dependency_missing" in w for w in manifest["warnings"])

    bm25 = search_bm25(paper_id, "reinforcement", config=l2_lancedb_config)
    assert bm25.status == "ok"


def test_retrieval_lancedb_real_build_load_search(
    project_tmp_path, monkeypatch, l2_lancedb_config
):
    """The production LanceDB store performs a real single-paper
    build/load/search roundtrip and rebuilds identically from chunks
    (skipped when ``lancedb`` is not installed)."""
    pytest.importorskip("lancedb")
    paper_id = _build_index(
        project_tmp_path, monkeypatch, l2_lancedb_config, _default_items(),
        embedding=ControlledEmbeddingProvider(),
        reranker=FakeRerankerProvider(),
    )
    from transit_scholar.layer2 import search_bm25, search_dense, search_hybrid

    bm25 = search_bm25(paper_id, "reinforcement learning", config=l2_lancedb_config)
    assert bm25.status == "ok"
    assert bm25.hits
    dense = search_dense(paper_id, "reinforcement learning", config=l2_lancedb_config)
    assert dense.status == "ok"
    assert dense.hits
    hybrid = search_hybrid(paper_id, "reinforcement learning", config=l2_lancedb_config)
    assert hybrid.status == "ok"
    assert len(hybrid.hits) <= l2_lancedb_config.final_top_k

    first = [(h.chunk_id, round(h.score, 6)) for h in bm25.hits]
    index_dir = retrieval_index_dir(l2_lancedb_config, paper_id)
    shutil.rmtree(index_dir)
    assert build_retrieval(paper_id, config=l2_lancedb_config)["status"] == "ok"
    second = search_bm25(paper_id, "reinforcement learning", config=l2_lancedb_config)
    assert second.status == "ok"
    assert [(h.chunk_id, round(h.score, 6)) for h in second.hits] == first


def test_retrieval_filters_narrow_results(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-RETRIEVAL-08: filters narrow or preserve the result set and never
    widen it."""
    from transit_scholar.layer2.parser.fake import make_item as _mi

    items = [
        _mi(item_id="h1", item_type="heading", text="Alpha", order=0, page=1, level=1, bbox=[70, 60, 530, 80]),
        _mi(item_id="p1", item_type="paragraph", text="Alpha section discusses reinforcement learning agents.", order=1, page=1, bbox=[70, 100, 530, 120]),
        _mi(item_id="h2", item_type="heading", text="Beta", order=2, page=1, level=1, bbox=[70, 140, 530, 160]),
        _mi(item_id="p2", item_type="paragraph", text="Beta section discusses reinforcement learning baselines.", order=3, page=1, bbox=[70, 180, 530, 200]),
    ]
    paper_id = _build_index(project_tmp_path, monkeypatch, l2_config, items)
    # fetch section ids from the parse result's sections file
    from transit_scholar.layer2.paths import load_current, run_paths

    current = load_current(l2_config.parsed_paper_dir(paper_id))
    rp = run_paths(l2_config, paper_id, current)
    sections = json.loads(rp.sections_path.read_text(encoding="utf-8"))
    assert len(sections) == 2
    alpha_section = sections[0]["section_id"]

    from transit_scholar.layer2 import search_bm25, search_dense

    unfiltered = search_bm25(paper_id, "reinforcement", config=l2_config)
    assert unfiltered.status == "ok"
    filtered = search_bm25(
        paper_id, "reinforcement", filters={"section_id": alpha_section}, config=l2_config
    )
    assert filtered.status == "ok"
    assert len(filtered.hits) <= len(unfiltered.hits)
    for hit in filtered.hits:
        assert hit.section_path
        assert hit.section_path[0] == "Alpha"
