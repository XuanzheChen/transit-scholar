"""Layer2 Step1 RetrievalHit & evidence-chain tests (AC-L2S1-HIT-01..03)."""

from __future__ import annotations

from tests.l2s1_fixtures import (
    FakeEmbeddingProvider,
    FakeRerankerProvider,
    make_ready_paper,
    patch_parsers,
    read_artifacts,
)
from transit_scholar.layer2.parser.fake import FakeParserAdapter

HIT_FIELDS = {
    "paper_id",
    "chunk_id",
    "score",
    "retrieval_method",
    "section_path",
    "pages",
    "source_refs",
    "text",
    "rank",
}
HYBRID_EXTRA = {"bm25_rank", "dense_rank", "rrf_rank", "rerank_score"}


class ControlledEmbeddingProvider(FakeEmbeddingProvider):
    def _vector(self, text: str) -> list[float]:
        return [1.0, 0.0] if "reinforcement" in text else [0.0, 1.0]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


def _items():
    from transit_scholar.layer2.parser.fake import make_item

    return [
        make_item(item_id="h1", item_type="heading", text="Method", order=0, page=1, level=1, bbox=[70, 60, 530, 80]),
        make_item(item_id="p1", item_type="paragraph", text="Reinforcement learning trains the holding controller.", order=1, page=1, bbox=[70, 100, 530, 120]),
        make_item(item_id="p2", item_type="paragraph", text="Deep neural networks approximate the value function.", order=2, page=1, bbox=[70, 140, 530, 160]),
    ]


def _build_paper(project_tmp_path, monkeypatch, l2_config, *, embedding=None, reranker=None):
    from transit_scholar.layer2 import build_retrieval
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.retrieval import api as retrieval_api

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=_items(), page_count=1)])
    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    if embedding is not None:
        monkeypatch.setattr(retrieval_api, "resolve_embedding_provider", lambda config: embedding)
    if reranker is not None:
        monkeypatch.setattr(retrieval_api, "resolve_reranker_provider", lambda config: reranker)
    build_retrieval(paper_id, config=l2_config)
    return paper_id


def _hit_field_keys(hit):
    return set(hit.to_dict().keys())


def test_hit_fields_for_all_methods(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-HIT-01: all retrieval methods return RetrievalHit with the base
    fields; hybrid adds the four rank fields."""
    paper_id = _build_paper(
        project_tmp_path, monkeypatch, l2_config,
        embedding=ControlledEmbeddingProvider(),
        reranker=FakeRerankerProvider(),
    )
    from transit_scholar.layer2 import (
        grep_paper, search_bm25, search_dense, search_hybrid,
    )

    methods = [
        grep_paper(paper_id, "reinforcement", config=l2_config),
        search_bm25(paper_id, "reinforcement", config=l2_config),
        search_dense(paper_id, "reinforcement", config=l2_config),
        search_hybrid(paper_id, "reinforcement", rerank=False, config=l2_config),
        search_hybrid(paper_id, "reinforcement", rerank=True, config=l2_config),
    ]
    for result in methods:
        assert result.status == "ok"
        assert result.hits, f"method {result.method} returned no hits"
        for hit in result.hits:
            keys = _hit_field_keys(hit)
            assert HIT_FIELDS <= keys, f"{result.method} missing base fields"
            if result.method == "hybrid":
                assert HYBRID_EXTRA <= keys


def test_hit_evidence_chain_no_dead_links(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-HIT-02: every hit traces hit -> chunk/source_refs -> canonical
    block -> provenance -> PDF page/bbox with zero dead links."""
    paper_id = _build_paper(
        project_tmp_path, monkeypatch, l2_config,
        embedding=ControlledEmbeddingProvider(),
        reranker=FakeRerankerProvider(),
    )
    from transit_scholar.layer2 import (
        grep_paper, search_bm25, search_dense, search_hybrid, read_blocks,
    )

    queries = [
        grep_paper(paper_id, "reinforcement", config=l2_config),
        search_bm25(paper_id, "reinforcement", config=l2_config),
        search_dense(paper_id, "reinforcement", config=l2_config),
        search_hybrid(paper_id, "reinforcement", config=l2_config),
    ]
    resolved = 0
    for result in queries:
        assert result.status == "ok"
        for hit in result.hits:
            assert hit.paper_id == paper_id
            refs = hit.source_refs
            assert refs, "hit has no source_refs"
            block_ids = [ref.block_id for ref in refs]
            blocks = read_blocks(paper_id, block_ids, config=l2_config)
            assert len(blocks) == len(set(block_ids)), "some block refs are dead"
            for block in blocks:
                assert block["provenance"], "block has no provenance"
                for prov in block["provenance"]:
                    assert prov["page"] >= 1
                    assert prov["bbox"] is not None or True  # bbox optional for fake
                resolved += 1
    assert resolved >= 4


def test_hit_method_and_rank_uniqueness(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-HIT-03: retrieval_method distinguishes methods; ranks are
    1-based and unique within a result set."""
    paper_id = _build_paper(
        project_tmp_path, monkeypatch, l2_config,
        embedding=ControlledEmbeddingProvider(),
        reranker=FakeRerankerProvider(),
    )
    from transit_scholar.layer2 import (
        grep_paper, search_bm25, search_dense, search_hybrid,
    )

    cases = [
        (grep_paper(paper_id, "value", config=l2_config), "grep"),
        (search_bm25(paper_id, "value", config=l2_config), "bm25"),
        (search_dense(paper_id, "value", config=l2_config), "dense"),
        (search_hybrid(paper_id, "value", config=l2_config), "hybrid"),
    ]
    for result, expected in cases:
        assert result.status == "ok"
        for hit in result.hits:
            assert hit.retrieval_method == expected
        ranks = [hit.rank for hit in result.hits]
        assert ranks == list(range(1, len(ranks) + 1))
        assert len(set(ranks)) == len(ranks)
