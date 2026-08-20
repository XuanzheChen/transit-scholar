"""Layer2 Step1 retrieval evaluation tests (AC-L2S1-EVAL-01..04)."""

from __future__ import annotations

import math

import pytest

from transit_scholar.layer2.eval.gold import GoldQuery, validate_gold_query
from transit_scholar.layer2.eval.metrics import (
    mrr_at_k,
    ndcg_at_k,
    recall_at_k,
)
from transit_scholar.layer2.eval.fixtures import (
    build_fixture_gold_queries,
    build_fixture_paper_items,
)
from transit_scholar.layer2.eval.evaluate import evaluate_paper, metrics_from_hits
from transit_scholar.layer2.parser.fake import FakeParserAdapter
from tests.l2s1_fixtures import (
    FakeEmbeddingProvider,
    make_ready_paper,
    patch_parsers,
)


def test_eval_gold_schema_and_optional_spans():
    """AC-L2S1-EVAL-01: gold data structure supports the required fields and
    gold_source_spans is optional."""
    gold = validate_gold_query(
        {
            "paper_id": "p1",
            "query": "headway",
            "query_type": "exact_term",
            "gold_block_ids": ["blk_00001"],
        }
    )
    assert isinstance(gold, GoldQuery)
    assert gold.gold_source_spans is None

    with_spans = validate_gold_query(
        {
            "paper_id": "p1",
            "query": "headway",
            "query_type": "exact_term",
            "gold_block_ids": ["blk_00001"],
            "gold_source_spans": [{"block_id": "blk_00001", "char_start": 0, "char_end": 7}],
        }
    )
    assert with_spans.gold_source_spans is not None

    with pytest.raises(ValueError):
        validate_gold_query({"paper_id": "p1"})  # missing required fields


def test_eval_metrics_hand_computed():
    """AC-L2S1-EVAL-02: hand-computed tiny cases yield exact metric values."""
    # gold={a,b}; ranking=[a,b,c] -> perfect
    assert recall_at_k(["a", "b", "c"], {"a", "b"}, 5) == 1.0
    assert recall_at_k(["a", "b", "c"], {"a", "b"}, 10) == 1.0
    assert mrr_at_k(["a", "b", "c"], {"a", "b"}, 10) == 1.0
    assert ndcg_at_k(["a", "b", "c"], {"a", "b"}, 10) == 1.0

    # gold={c}; ranking=[a,b,c] -> recall 1.0, mrr 1/3, ndcg 0.5
    assert recall_at_k(["a", "b", "c"], {"c"}, 5) == 1.0
    assert recall_at_k(["a", "b", "c"], {"c"}, 10) == 1.0
    assert abs(mrr_at_k(["a", "b", "c"], {"c"}, 10) - 1.0 / 3.0) < 1e-9
    assert abs(ndcg_at_k(["a", "b", "c"], {"c"}, 10) - 0.5) < 1e-9

    # gold={a,b,c}; ranking=[a,x,b] -> recall@5 = 2/3, mrr=1
    assert abs(recall_at_k(["a", "x", "b"], {"a", "b", "c"}, 5) - 2.0 / 3.0) < 1e-9
    assert mrr_at_k(["a", "x", "b"], {"a", "b", "c"}, 10) == 1.0
    expected_ndcg = (1.0 / math.log2(2) + 1.0 / math.log2(4)) / (
        1.0 / math.log2(2) + 1.0 / math.log2(3) + 1.0 / math.log2(4)
    )
    assert abs(ndcg_at_k(["a", "x", "b"], {"a", "b", "c"}, 10) - expected_ndcg) < 1e-9


def _build_eval_paper(project_tmp_path, monkeypatch, l2_config):
    from transit_scholar.layer2 import build_retrieval
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.retrieval import api as retrieval_api

    items = build_fixture_paper_items()
    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, [FakeParserAdapter(items=items, page_count=2)])
    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    embedding = FakeEmbeddingProvider(dimension=8)
    monkeypatch.setattr(retrieval_api, "resolve_embedding_provider", lambda config: embedding)
    build_retrieval(paper_id, config=l2_config)
    return paper_id


def test_eval_four_variants_reported(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-EVAL-02: the evaluator reports BM25, Dense, Hybrid RRF and
    Hybrid RRF + Reranker variants."""
    paper_id = _build_eval_paper(project_tmp_path, monkeypatch, l2_config)
    gold = build_fixture_gold_queries(paper_id)

    from transit_scholar.layer2 import (
        search_bm25, search_dense, search_hybrid,
    )

    def search_fn(query, variant):
        if variant == "bm25":
            return search_bm25(paper_id, query, config=l2_config).hits
        if variant == "dense":
            return search_dense(paper_id, query, config=l2_config).hits
        if variant == "hybrid":
            return search_hybrid(paper_id, query, rerank=False, config=l2_config).hits
        if variant == "hybrid_rerank":
            return search_hybrid(paper_id, query, rerank=True, config=l2_config).hits
        return []

    report = evaluate_paper(paper_id, gold, search_fn=search_fn)
    assert report.query_count == len(gold)
    assert set(report.variants.keys()) == {
        "bm25", "dense", "hybrid", "hybrid_rerank",
    }
    assert set(report.summary.keys()) == {"bm25", "dense", "hybrid", "hybrid_rerank"}
    for variant in report.summary:
        for metric in ("recall@5", "recall@10", "mrr@10", "ndcg@10"):
            assert 0.0 <= report.summary[variant][metric] <= 1.0


def test_eval_deterministic(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-EVAL-03: running the eval twice yields identical metric output."""
    paper_id = _build_eval_paper(project_tmp_path, monkeypatch, l2_config)
    gold = build_fixture_gold_queries(paper_id)

    from transit_scholar.layer2 import search_bm25

    def search_fn(query, variant):
        return search_bm25(paper_id, query, config=l2_config).hits

    first = evaluate_paper(paper_id, gold, search_fn=search_fn)
    second = evaluate_paper(paper_id, gold, search_fn=search_fn)
    assert first.summary == second.summary
    assert first.variants == second.variants


def test_eval_cross_language_chinese_query(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-EVAL-04: a Chinese-query gold entry evaluates without error
    through the fake dense backend."""
    paper_id = _build_eval_paper(project_tmp_path, monkeypatch, l2_config)
    gold = build_fixture_gold_queries(paper_id)
    chinese = [g for g in gold if g.query_type == "cross_language"]
    assert chinese
    assert any("\u4e00" <= ch <= "\u9fff" for ch in chinese[0].query)

    from transit_scholar.layer2 import search_dense

    hits = search_dense(paper_id, chinese[0].query, config=l2_config)
    assert hits.status == "ok"
    report = evaluate_paper(
        paper_id,
        [chinese[0]],
        search_fn=lambda query, variant: search_dense(paper_id, query, config=l2_config).hits,
    )
    assert "ndcg@10" in report.summary["dense"]


# ---------------------------------------------------------------------------
# task-2026-08-13-001 T-10 (AC-GOLD-004/006)
# ---------------------------------------------------------------------------


def _hit(paper_id: str, chunk_id: str, block_ids: list[str]) -> RetrievalHit:
    from transit_scholar.layer2.schema import RetrievalHit, SourceRef

    return RetrievalHit(
        paper_id=paper_id,
        chunk_id=chunk_id,
        score=0.9,
        retrieval_method="fake",
        section_path=["S"],
        pages=[1],
        source_refs=[
            SourceRef(block_id=block_id, char_start=0, char_end=10)
            for block_id in block_ids
        ],
        text="body",
        rank=1,
    )


def _corpus_gold() -> list:
    from transit_scholar.layer2.eval.gold import GoldQuery

    return [
        GoldQuery(
            paper_id="p1", query="headway regularity",
            query_type="exact_term", gold_block_ids=["b1"],
        ),
        GoldQuery(
            paper_id="p1", query="什么是驻站控制方法？",
            query_type="cross_language", gold_block_ids=["b2"],
        ),
        GoldQuery(
            paper_id="p2", query="What is the holding method?",
            query_type="method_description", gold_block_ids=["b3"],
        ),
    ]


def test_t10_corpus_eval_four_way_report_and_unavailable(
    project_tmp_path, monkeypatch, l2_config
):
    """T-10 (AC-GOLD-004): the corpus runner reports overall/per_query/by_type/
    by_language across the four variants, keeps unavailable queries (never
    deletes them), records top_k >= 10 and outputs the five AC-GOLD-006 rules."""
    from transit_scholar.layer2.eval.run import run_corpus_eval
    from transit_scholar.layer2.retrieval import api as retrieval_api
    from transit_scholar.layer2.retrieval.providers import UnavailableError
    from transit_scholar.layer2.schema import RetrievalResult

    gold = _corpus_gold()
    output = project_tmp_path / "eval_out"
    perfect_blocks = {g.paper_id: set(g.gold_block_ids) for g in gold}
    failed_queries = {"什么是驻站控制方法？"}

    def fake_search(paper_id, query, variant, top_k):
        if variant in ("dense", "hybrid_rerank") and query in failed_queries:
            return RetrievalResult(
                status="unavailable", method=variant,
                error_code="rate_limited", error_message="429 exhausted (fake)",
            )
        blocks = sorted(perfect_blocks[paper_id])
        return RetrievalResult(
            status="ok", method=variant,
            hits=[_hit(paper_id, f"chunk_{block}", [block]) for block in blocks],
        )

    report = run_corpus_eval(
        gold, l2_config, output_dir=output, top_k=10,
        search_fn=fake_search, request_pause=0.0,
    )
    assert report["top_k"] == 10
    assert report["query_count"] == 3
    assert report["unfinished_query_count"] == 2  # dense + hybrid_rerank for the zh query
    assert len(report["unfinished_queries"]) == 2
    assert all(q["error_code"] == "rate_limited" for q in report["unfinished_queries"])
    assert report["completed_query_count"] == 3  # every query has at least one variant

    for layer in ("overall", "per_query", "by_type", "by_language"):
        assert layer in report, f"report missing {layer}"
    assert set(report["by_language"]) == {"zh", "en"}

    for variant in ("bm25", "dense", "hybrid", "hybrid_rerank"):
        metrics = report["overall"][variant]
        assert metrics["status"] == "ok"
        for key in ("recall@5", "recall@10", "mrr@10", "ndcg@10"):
            assert 0.0 <= metrics[key] <= 1.0

    # unavailable variants are recorded per query, not deleted
    zh_per_query = next(
        data for data in report["per_query"].values()
        if data["language"] == "zh"
    )
    assert zh_per_query["variants"]["dense"]["status"] == "unavailable"
    assert zh_per_query["variants"]["hybrid_rerank"]["status"] == "unavailable"

    assert len(report["rules"]) == 5
    for rule in report["rules"]:
        assert rule["passed"] in (True, False, None)
        assert rule["values"]

    assert report["parse_run_ids"]  # manifest-grade run ids recorded
    assert (output / "report.json").is_file()
    assert (output / "failed_queries.json").is_file()


def test_t10_corpus_eval_rejects_top_k_below_10(project_tmp_path, l2_config):
    from transit_scholar.layer2.eval.run import run_corpus_eval

    with pytest.raises(ValueError):
        run_corpus_eval(
            _corpus_gold(), l2_config, output_dir=project_tmp_path / "x",
            top_k=8, search_fn=lambda *a, **k: None,
        )


def test_t10_corpus_eval_real_backend_offline(
    project_tmp_path, monkeypatch, l2_config
):
    """T-10 integration: with the real search backend and no network, BM25
    completes while dense/hybrid truthfully report network_blocked and stay in
    the report."""
    paper_id = _build_eval_paper(project_tmp_path, monkeypatch, l2_config)
    gold = [
        g for g in build_fixture_gold_queries(paper_id)
        if g.paper_id == paper_id
    ]
    assert gold
    # force an unavailable (network-blocked) provider for the offline run
    from transit_scholar.layer2.retrieval import api as retrieval_api
    from transit_scholar.layer2.retrieval.providers import (
        UnavailableEmbeddingProvider,
        UnavailableRerankerProvider,
    )

    monkeypatch.setattr(
        retrieval_api, "resolve_embedding_provider",
        lambda config: UnavailableEmbeddingProvider("network_blocked", error_code="network_blocked"),
    )
    monkeypatch.setattr(
        retrieval_api, "resolve_reranker_provider",
        lambda config: UnavailableRerankerProvider("network_blocked"),
    )
    output = project_tmp_path / "eval_offline"
    from transit_scholar.layer2.eval.run import run_corpus_eval

    report = run_corpus_eval(
        gold, l2_config, output_dir=output, top_k=10,
        request_pause=0.0, allow_network=False,
    )
    assert report["network_enabled"] is False
    assert report["overall"]["bm25"]["status"] == "ok"
    assert report["overall"]["dense"]["status"] == "unavailable"
    assert report["overall"]["hybrid"]["status"] == "unavailable"
    assert report["overall"]["hybrid_rerank"]["status"] == "unavailable"
    # every query's unavailable variants are individually recorded
    for data in report["per_query"].values():
        assert data["variants"]["dense"]["error_code"] == "network_blocked"
    assert report["rules"]


# ---------------------------------------------------------------------------
# repair-03: rule 4 / rule 5 semantics and per-query warnings
# ---------------------------------------------------------------------------


def _agg(status="ok", completed=4, metrics=None):
    data: dict = {"status": status, "completed": completed}
    data.update(metrics or {})
    return data


def test_rule4_recall_or_mrr_semantics():
    """Rule 4 uses OR semantics: BM25 keeps its advantage when Recall OR MRR
    holds; it is false only when both comparisons fail; None only when no
    metric data exists for either variant."""
    from transit_scholar.layer2.eval.run import _rule4

    # recall loses (0.6 < 0.8) but MRR wins (0.5 >= 0.312222) -> true
    exact_mrr_wins = {
        "bm25": _agg(metrics={"recall@10": 0.6, "mrr@10": 0.5}),
        "dense": _agg(metrics={"recall@10": 0.8, "mrr@10": 0.312222}),
    }
    assert _rule4(exact_mrr_wins) is True

    # recall loses and MRR loses -> false
    exact_both_lose = {
        "bm25": _agg(metrics={"recall@10": 0.6, "mrr@10": 0.2}),
        "dense": _agg(metrics={"recall@10": 0.8, "mrr@10": 0.3}),
    }
    assert _rule4(exact_both_lose) is False

    # recall wins -> true regardless of MRR
    exact_recall_wins = {
        "bm25": _agg(metrics={"recall@10": 0.9, "mrr@10": 0.1}),
        "dense": _agg(metrics={"recall@10": 0.5, "mrr@10": 0.4}),
    }
    assert _rule4(exact_recall_wins) is True

    # no metrics at all -> None (unverifiable)
    assert _rule4({
        "bm25": _agg(status="unavailable", completed=0),
        "dense": _agg(status="unavailable", completed=0),
    }) is None


def test_rule5_uses_real_zh_group():
    """Rule 5 checks the real by_language.zh aggregates: true when at least
    one zh variant completed, false when the whole group is unavailable."""
    from transit_scholar.layer2.eval.run import _rule5

    zh_completed = {
        "hybrid": {"status": "ok", "completed": 13, "recall@10": 0.77},
        "hybrid_rerank": {"status": "ok", "completed": 13, "recall@10": 0.77},
    }
    assert _rule5({"zh": zh_completed, "en": {}}) is True

    zh_all_unavailable = {
        variant: _agg(status="unavailable", completed=0)
        for variant in ("bm25", "dense", "hybrid", "hybrid_rerank")
    }
    assert _rule5({"zh": zh_all_unavailable, "en": {}}) is False


def test_compute_rules_accepts_by_language_and_records_zh_values():
    from transit_scholar.layer2.eval.run import compute_rules

    overall = {
        "bm25": _agg(metrics={"recall@10": 0.6, "mrr@10": 0.4}),
        "dense": _agg(metrics={"recall@10": 0.8, "mrr@10": 0.4}),
        "hybrid": _agg(metrics={"recall@10": 0.84, "mrr@10": 0.5}),
        "hybrid_rerank": _agg(metrics={"recall@10": 0.84, "mrr@10": 0.6}),
    }
    by_type = {
        "exact_term": {
            "bm25": _agg(metrics={"recall@10": 0.6, "mrr@10": 0.5}),
            "dense": _agg(metrics={"recall@10": 0.8, "mrr@10": 0.31}),
        }
    }
    by_language = {
        "zh": {
            "hybrid": _agg(completed=13, metrics={"recall@10": 0.77}),
            "hybrid_rerank": _agg(completed=13, metrics={"recall@10": 0.77}),
        },
        "en": {},
    }
    rules = compute_rules(overall, by_type, by_language)
    by_number = {rule["rule"]: rule for rule in rules}
    assert len(rules) == 5
    assert by_number[4]["passed"] is True
    assert by_number[5]["passed"] is True
    assert by_number[5]["values"]["zh_completed_variants"] == 2
    assert by_number[5]["values"]["zh_variant_statuses"] == {
        "hybrid": "ok", "hybrid_rerank": "ok",
    }


def test_corpus_eval_records_per_query_warnings(project_tmp_path, l2_config):
    """Degradation warnings survive into the per-query variant records so the
    report keeps visible, structured evidence of rerank fallbacks."""
    from transit_scholar.layer2.eval.run import run_corpus_eval
    from transit_scholar.layer2.schema import RetrievalResult

    gold = _corpus_gold()
    perfect_blocks = {g.paper_id: set(g.gold_block_ids) for g in gold}

    def fake_search(paper_id, query, variant, top_k):
        warnings = []
        if variant == "hybrid_rerank":
            warnings.append(
                "rerank failed (rate_limited): rerank rate limited; "
                "falling back to RRF hybrid top-k"
            )
        return RetrievalResult(
            status="ok", method=variant,
            hits=[_hit(paper_id, f"chunk_{block}", [block])
                  for block in sorted(perfect_blocks[paper_id])],
            warnings=warnings,
        )

    output = project_tmp_path / "eval_warnings"
    report = run_corpus_eval(
        gold, l2_config, output_dir=output, top_k=10,
        search_fn=fake_search, request_pause=0.0,
    )
    for data in report["per_query"].values():
        rerank_variant = data["variants"]["hybrid_rerank"]
        assert "warnings" in rerank_variant
        assert any("rerank failed" in w for w in rerank_variant["warnings"])
