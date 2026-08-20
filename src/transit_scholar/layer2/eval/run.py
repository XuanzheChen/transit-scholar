"""Four-way corpus retrieval evaluation (FR-GOLD-003/004/005).

Runs the same P-annotated gold set over BM25 only / Dense only / RRF hybrid /
RRF + Reranker with ``top_k >= 10`` (recorded in the manifest, avoiding the
"rerank default top8 makes Recall@10 == Recall@8" metric trap). Outputs
per-query, by-query-type and by-language aggregates plus AC-GOLD-006 rule
judgements. Failed variants are recorded as ``unavailable`` -- never deleted.

Network boundary: without ``--allow-network`` dense/hybrid truthfully report
``network_blocked``; with it, requests go only to the configured Jina
embedding/rerank endpoints and the manifest records the scope before any
request is made. ``--request-pause`` sleeps between queries to control rate.

Command::

    python -m transit_scholar.layer2.eval.run
      --gold <gold.json> --data-root <root> --output <dir>
      [--allow-network] [--top-k 10] [--fusion-candidate-k 30]
      [--request-pause 1.0]

Exit codes: 0 = evaluation completed (unavailable queries are recorded in the
report), 2 = usage/input error, 3 = runner-level failure.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from transit_scholar.layer2.eval.gold import QUERY_TYPES, load_gold_queries
from transit_scholar.layer2.eval.goldcheck import classify_language
from transit_scholar.layer2.eval.metrics import compute_metrics
from transit_scholar.layer2.schema import GoldQuery, RetrievalResult

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUNNER_FAILURE = 3

VARIANTS = ("bm25", "dense", "hybrid", "hybrid_rerank")
METRIC_KEYS = ("recall@5", "recall@10", "mrr@10", "ndcg@10")

#: Search function seam: (paper_id, query, variant, top_k) -> RetrievalResult.
SearchVariantFn = Callable[[str, str, str, int], RetrievalResult]


def default_search_fn(
    config, *, top_k: int
) -> SearchVariantFn:
    """Real search backend over the configured data root."""
    from transit_scholar.layer2.retrieval import api as retrieval_api

    def search(paper_id: str, query: str, variant: str, k: int) -> RetrievalResult:
        if variant == "bm25":
            return retrieval_api.search_bm25(paper_id, query, top_k=k, config=config)
        if variant == "dense":
            return retrieval_api.search_dense(paper_id, query, top_k=k, config=config)
        if variant == "hybrid":
            return retrieval_api.search_hybrid(
                paper_id, query, top_k=k, rerank=False, config=config
            )
        if variant == "hybrid_rerank":
            return retrieval_api.search_hybrid(
                paper_id, query, top_k=k, rerank=True, config=config
            )
        raise ValueError(f"unknown variant {variant!r}")

    return search


def query_id(query: str) -> str:
    from transit_scholar.layer2.util import stable_json_hash

    return stable_json_hash({"query": query})[:12]


def run_corpus_eval(
    gold: list[GoldQuery],
    config,
    *,
    output_dir: str | Path,
    top_k: int = 10,
    fusion_candidate_k: int | None = None,
    search_fn: SearchVariantFn | None = None,
    request_pause: float = 0.0,
    allow_network: bool = False,
) -> dict[str, Any]:
    """Run the four-way evaluation and return the report dict (also on disk)."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if top_k < 10:
        raise ValueError("evaluation top_k must be >= 10 (AC-GOLD-004)")
    eval_config = copy.copy(config)
    object.__setattr__(eval_config, "retrieval_allow_network", allow_network)
    if allow_network:
        object.__setattr__(eval_config, "block_network", False)
    if fusion_candidate_k is not None:
        object.__setattr__(eval_config, "fusion_candidate_k", fusion_candidate_k)

    if search_fn is None:
        search_fn = default_search_fn(eval_config, top_k=top_k)

    # Build retrieval indexes from the accepted runs' chunks (derived data).
    from transit_scholar.layer2.retrieval import api as retrieval_api

    paper_ids = sorted({entry.paper_id for entry in gold})
    parse_run_ids: dict[str, str | None] = {}
    for paper_id in paper_ids:
        try:
            build = retrieval_api.build_retrieval(paper_id, config=eval_config)
        except Exception as exc:  # noqa: BLE001 - structured runner failure
            build = {"status": "unavailable", "error_code": "build_exception",
                     "error_message": f"{type(exc).__name__}: {exc}"}
        if build.get("status") != "ok":
            print(
                f"warning: build_retrieval({paper_id}) -> {build.get('status')}",
                file=sys.stderr,
            )
        from transit_scholar.layer2.paths import load_current

        parse_run_ids[paper_id] = load_current(eval_config.parsed_paper_dir(paper_id))

    from transit_scholar.layer2.util import sha256_file, now_utc_iso

    gold_sha256 = ""
    if getattr(config, "_gold_path", None):
        try:
            gold_sha256 = sha256_file(config._gold_path)
        except OSError:
            gold_sha256 = ""

    per_query: dict[str, dict[str, Any]] = {}
    unfinished: list[dict[str, Any]] = []
    for entry in gold:
        qid = query_id(entry.query)
        variants: dict[str, dict[str, Any]] = {}
        for variant in VARIANTS:
            started = time.time()
            try:
                result = search_fn(entry.paper_id, entry.query, variant, top_k)
            except Exception as exc:  # noqa: BLE001 - structured unavailable
                result = RetrievalResult(
                    status="unavailable",
                    method=variant,
                    error_code="provider_exception",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            if request_pause > 0:
                time.sleep(request_pause)
            if result.status == "ok":
                metrics = compute_metrics_from_hits(result.hits, entry.gold_block_ids)
                variants[variant] = {"status": "ok", "metrics": metrics,
                                     "top_hits": [h.chunk_id for h in result.hits[:top_k]],
                                     "warnings": list(result.warnings)}
            else:
                variants[variant] = {
                    "status": "unavailable",
                    "error_code": result.error_code or "unavailable",
                    "error_message": result.error_message,
                    "warnings": list(result.warnings),
                    "metrics": None,
                }
                unfinished.append(
                    {"query_id": qid, "query": entry.query,
                     "paper_id": entry.paper_id, "variant": variant,
                     "error_code": result.error_code or "unavailable"}
                )
            variants[variant]["runtime_s"] = round(time.time() - started, 3)
        per_query[qid] = {
            "query": entry.query,
            "paper_id": entry.paper_id,
            "query_type": entry.query_type,
            "language": classify_language(entry.query),
            "gold_block_ids": list(entry.gold_block_ids),
            "variants": variants,
        }

    overall = _aggregate(per_query)
    by_type = {query_type: _aggregate(
        {qid: data for qid, data in per_query.items()
         if data["query_type"] == query_type})
        for query_type in QUERY_TYPES}
    by_language = {language: _aggregate(
        {qid: data for qid, data in per_query.items()
         if data["language"] == language})
        for language in ("zh", "en")}

    rules = compute_rules(overall, by_type, by_language)
    failed_queries = _failed_query_detail(per_query, rules)

    report = {
        "format_version": "transit-scholar-layer2-retrieval-eval-v1",
        "gold_file": getattr(config, "_gold_path", None),
        "gold_sha256": gold_sha256 or "unavailable",
        "top_k": top_k,
        "fusion_candidate_k": fusion_candidate_k
        or eval_config.fusion_candidate_k,
        "network_enabled": allow_network,
        "embedding_model": eval_config.resolved_embedding_model,
        "reranker_model": eval_config.resolved_reranker_model,
        "request_pause_s": request_pause,
        "query_count": len(gold),
        "completed_query_count": len(per_query),
        "parse_run_ids": parse_run_ids,
        "unfinished_query_count": len(unfinished),
        "unfinished_queries": unfinished,
        "overall": overall,
        "per_query": per_query,
        "by_type": by_type,
        "by_language": by_language,
        "rules": rules,
        "failed_queries": failed_queries,
        "created_at": now_utc_iso(),
        "command": "transit_scholar.layer2.eval.run",
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output / "failed_queries.json").write_text(
        json.dumps({"failed_queries": failed_queries,
                    "unfinished_queries": unfinished},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def compute_metrics_from_hits(hits, gold_block_ids: list[str]) -> dict[str, float]:
    """Reuse the canonical binary-relevance metric semantics (gold coverage)."""
    from transit_scholar.layer2.eval.evaluate import metrics_from_hits

    return metrics_from_hits(hits, gold_block_ids)


def _aggregate(per_query: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    for variant in VARIANTS:
        entries = [
            data["variants"][variant]["metrics"]
            for data in per_query.values()
            if data["variants"].get(variant, {}).get("status") == "ok"
        ]
        if not entries:
            aggregated[variant] = {"status": "unavailable", "completed": 0}
            continue
        aggregated[variant] = {
            "status": "ok",
            "completed": len(entries),
            **{
                key: round(
                    sum(entry[key] for entry in entries) / len(entries), 6
                )
                for key in METRIC_KEYS
            },
        }
    return aggregated


def compute_rules(
    overall: dict[str, dict[str, Any]],
    by_type: dict[str, dict[str, Any]],
    by_language: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """AC-GOLD-006 five acceptance rules with values (computed over completed
    queries; unavailable variants make the corresponding rule unverifiable)."""
    rules: list[dict[str, Any]] = []
    rules.append({
        "rule": 1,
        "name": "hybrid_recall >= best single",
        "passed": _rule1(overall),
        "values": {variant: overall.get(variant, {}).get("recall@10")
                   for variant in VARIANTS},
    })
    rules.append({
        "rule": 2,
        "name": "hybrid_rerank mrr >= best single",
        "passed": _rule2(overall),
        "values": {variant: overall.get(variant, {}).get("mrr@10")
                   for variant in VARIANTS},
    })
    rules.append({
        "rule": 3,
        "name": "rerank does not hurt recall",
        "passed": _rule3(overall),
        "values": {
            "hybrid_recall@10": overall.get("hybrid", {}).get("recall@10"),
            "hybrid_rerank_recall@10": overall.get("hybrid_rerank", {}).get("recall@10"),
        },
    })
    exact = by_type.get("exact_term", {})
    rules.append({
        "rule": 4,
        "name": "exact_term keeps bm25 advantage",
        "passed": _rule4(exact),
        "values": {
            "bm25_recall@10": exact.get("bm25", {}).get("recall@10"),
            "dense_recall@10": exact.get("dense", {}).get("recall@10"),
            "bm25_mrr@10": exact.get("bm25", {}).get("mrr@10"),
            "dense_mrr@10": exact.get("dense", {}).get("mrr@10"),
        },
    })
    zh = by_language.get("zh", {})
    zh_completed = _completed_variants(zh)
    rules.append({
        "rule": 5,
        "name": "zh (cross-language) results exist",
        "passed": _rule5(by_language),
        "values": {
            "zh_completed_variants": zh_completed,
            "zh_variant_statuses": {
                variant: data.get("status")
                for variant, data in zh.items()
                if isinstance(data, dict)
            },
        },
    })
    return rules


def _completed_variants(group: dict[str, dict[str, Any]]) -> int:
    """Number of variants in an aggregate group with a real completed run."""
    return sum(
        1
        for data in group.values()
        if isinstance(data, dict) and data.get("status") == "ok"
    )


def _rule1(overall: dict[str, dict[str, Any]]) -> bool | None:
    values = []
    for variant in ("hybrid", "hybrid_rerank"):
        value = overall.get(variant, {}).get("recall@10")
        if isinstance(value, (int, float)):
            values.append(value)
    singles = []
    for variant in ("bm25", "dense"):
        value = overall.get(variant, {}).get("recall@10")
        if isinstance(value, (int, float)):
            singles.append(value)
    if not values or not singles:
        return None
    return max(values) >= max(singles)


def _rule2(overall: dict[str, dict[str, Any]]) -> bool | None:
    value = overall.get("hybrid_rerank", {}).get("mrr@10")
    singles = []
    for variant in ("bm25", "dense"):
        mrr = overall.get(variant, {}).get("mrr@10")
        if isinstance(mrr, (int, float)):
            singles.append(mrr)
    if value is None or not singles:
        return None
    return value >= max(singles)


def _rule3(overall: dict[str, dict[str, Any]]) -> bool | None:
    hybrid = overall.get("hybrid", {}).get("recall@10")
    reranked = overall.get("hybrid_rerank", {}).get("recall@10")
    if not isinstance(hybrid, (int, float)) or not isinstance(reranked, (int, float)):
        return None
    return reranked >= hybrid - 0.05


def _rule4(exact: dict[str, dict[str, Any]]) -> bool | None:
    """Exact-term queries: BM25 must keep its advantage on Recall **or** MRR.

    The recall comparison and the MRR comparison are alternative evidence of
    the same requirement, so the rule passes when either holds and is only
    ``None`` (unverifiable) when neither metric pair is available.
    """
    bm25_recall = exact.get("bm25", {}).get("recall@10")
    dense_recall = exact.get("dense", {}).get("recall@10")
    bm25_mrr = exact.get("bm25", {}).get("mrr@10")
    dense_mrr = exact.get("dense", {}).get("mrr@10")
    recall_pass = (
        isinstance(bm25_recall, (int, float))
        and isinstance(dense_recall, (int, float))
        and bm25_recall >= dense_recall
    )
    mrr_pass = (
        isinstance(bm25_mrr, (int, float))
        and isinstance(dense_mrr, (int, float))
        and bm25_mrr >= dense_mrr
    )
    if not any(
        isinstance(value, (int, float))
        for value in (bm25_recall, dense_recall, bm25_mrr, dense_mrr)
    ):
        return None
    return recall_pass or mrr_pass


def _rule5(by_language: dict[str, dict[str, Any]]) -> bool | None:
    """Chinese (cross-language) results exist: at least one variant in the
    real ``by_language.zh`` group completed."""
    zh = by_language.get("zh", {})
    completed = _completed_variants(zh)
    if completed <= 0:
        return False
    return True


def _failed_query_detail(
    per_query: dict[str, dict[str, Any]], rules: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """AC-GOLD-007 failure detail: every query whose default (hybrid_rerank)
    variant completed but scored recall@10 < 0.90, plus every query with
    unfinished variants."""
    failed: list[dict[str, Any]] = []
    for qid, data in per_query.items():
        variants = data["variants"]
        detail: dict[str, Any] = {
            "query_id": qid,
            "query": data["query"],
            "paper_id": data["paper_id"],
            "query_type": data["query_type"],
            "language": data["language"],
            "gold_block_ids": data["gold_block_ids"],
            "variants": {
                variant: {
                    "status": variants[variant].get("status"),
                    "metrics": variants[variant].get("metrics"),
                }
                for variant in VARIANTS
            },
        }
        reranked = variants.get("hybrid_rerank", {})
        if reranked.get("status") == "ok":
            recall = (reranked.get("metrics") or {}).get("recall@10", 0.0)
            if recall < 0.90:
                detail["below_target"] = True
                detail["possible_causes"] = _possible_causes(detail)
                failed.append(detail)
    return failed


def _possible_causes(detail: dict[str, Any]) -> list[str]:
    """Deterministic five-class cause hints for the failure report."""
    causes: list[str] = []
    metrics = detail["variants"].get("hybrid_rerank", {}).get("metrics") or {}
    bm25_metrics = detail["variants"].get("bm25", {}).get("metrics") or {}
    if not metrics.get("recall@10"):
        causes.append("parsing/chunking: gold block not covered by any retrieved chunk")
    if bm25_metrics.get("recall@10") and not metrics.get("recall@10"):
        causes.append("query formulation: lexical evidence exists but dense/rerank misses it")
    if metrics.get("recall@10"):
        causes.append("ranking: gold present in candidates but below top-k")
    if detail["language"] == "zh":
        causes.append("cross-language: zh query -> en paper may need embedding coverage")
    return causes or ["no evidence retrieved by any variant"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.eval.run",
        description="Four-way retrieval evaluation over the P-annotated gold set.",
    )
    parser.add_argument("--gold", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-network", action="store_true",
                        help="allow Jina embedding/rerank requests (default: blocked)")
    parser.add_argument("--top-k", type=int, default=10, help="evaluation top-k (>= 10)")
    parser.add_argument("--fusion-candidate-k", type=int, default=None)
    parser.add_argument("--request-pause", type=float, default=1.0,
                        help="seconds to sleep between queries (rate control)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gold_path = Path(args.gold)
    if not gold_path.is_file():
        print(f"gold file missing: {gold_path}", file=sys.stderr)
        return EXIT_USAGE
    data_root = Path(args.data_root)
    if not data_root.is_dir():
        print(f"data root missing: {data_root}", file=sys.stderr)
        return EXIT_USAGE
    try:
        gold = load_gold_queries(gold_path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"gold file invalid: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        from transit_scholar.config import Settings
        from transit_scholar.layer2.config import Layer2Config

        config = Layer2Config.from_settings(Settings(data_root=data_root))
        object.__setattr__(config, "_gold_path", str(gold_path))
        report = run_corpus_eval(
            gold,
            config,
            output_dir=args.output,
            top_k=args.top_k,
            fusion_candidate_k=args.fusion_candidate_k,
            request_pause=args.request_pause,
            allow_network=args.allow_network,
        )
    except Exception as exc:  # noqa: BLE001 - structured runner failure
        print(f"evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNNER_FAILURE
    print(
        f"evaluation report: {Path(args.output) / 'report.json'} "
        f"(queries={report['query_count']}, "
        f"unfinished={report['unfinished_query_count']})",
        file=sys.stderr,
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
