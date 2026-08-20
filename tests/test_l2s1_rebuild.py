"""Layer2 Step1 versioning & rebuild tests (AC-L2S1-REBUILD-01/02)."""

from __future__ import annotations

import copy
import json

from transit_scholar.layer2.paths import (
    load_current,
    retrieval_manifest_path,
    run_paths,
)
from tests.l2s1_fixtures import (
    canonical_fixture_items,
    make_ready_paper,
    patch_parsers,
    read_artifacts,
    run_parse,
)

RETRIEVAL_MANIFEST_KEYS = {
    "parse_run_id",
    "chunker_version",
    "chunker_config_hash",
    "bm25_engine",
    "bm25_index_version",
    "embedding_model",
    "embedding_model_revision",
    "embedding_dimension",
    "fusion_method",
    "reranker_model",
    "reranker_model_revision",
    "created_at",
}


def test_rebuild_derived_only_changes_do_not_reparse(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-REBUILD-01: renderer/chunker/embedding/fusion/reranker changes
    never trigger a PDF reparse; only derived artifacts are regenerated."""
    _, _, _, result = run_parse(
        project_tmp_path, canonical_fixture_items(), monkeypatch=monkeypatch, page_count=1
    )
    original_run = result.parse_run_id
    artifacts_before = read_artifacts(l2_config, result.paper_id, original_run)
    rp = run_paths(l2_config, result.paper_id, original_run)
    canonical_before = json.dumps(
        (
            artifacts_before["document"].to_dict(),
            [s.to_dict() for s in artifacts_before["sections"]],
            [b.to_dict() for b in artifacts_before["blocks"]],
        ),
        sort_keys=True,
        default=str,
    )

    from transit_scholar.layer2.pipeline import parse_paper, rebuild_derived

    # bump every derived-only version in turn; parse_run_id must stay the same
    derived_config = copy.copy(l2_config)
    object.__setattr__(derived_config, "renderer_version", "2.0")
    object.__setattr__(derived_config, "chunker_version", "2.0")

    rebuilt = rebuild_derived(result.paper_id, config=derived_config)
    assert rebuilt["status"] == "ok"
    assert rebuilt["parse_run_id"] == original_run

    # embedding/fusion/reranker changes do not reparse either
    model_config = copy.copy(l2_config)
    object.__setattr__(model_config, "embedding_model", "Qwen3-Embedding-1.2B")
    reparsed = parse_paper(result.paper_id, config=model_config)
    assert reparsed.parse_run_id == original_run
    # parse_paper now auto-detects the derived drift and rebuilds with the
    # config it was called with (AC-VER-003): the manifest reflects the last
    # effective config, while the earlier explicit rebuild_derived(2.0) value
    # was replaced by the auto-rebuild.
    manifest_auto = json.loads(rp.manifest_path.read_text(encoding="utf-8"))
    assert manifest_auto["embedding_model"] == "Qwen3-Embedding-1.2B"
    assert manifest_auto["renderer_version"] == l2_config.renderer_version
    assert manifest_auto["chunker_version"] == l2_config.chunker_version

    artifacts_after = read_artifacts(l2_config, result.paper_id, original_run)
    canonical_after = json.dumps(
        (
            artifacts_after["document"].to_dict(),
            [s.to_dict() for s in artifacts_after["sections"]],
            [b.to_dict() for b in artifacts_after["blocks"]],
        ),
        sort_keys=True,
        default=str,
    )
    assert canonical_after == canonical_before

    rp = run_paths(l2_config, result.paper_id, original_run)
    manifest = json.loads(rp.manifest_path.read_text(encoding="utf-8"))
    assert manifest["renderer_version"] == l2_config.renderer_version
    assert manifest["chunker_version"] == l2_config.chunker_version


def test_rebuild_current_json_and_retrieval_manifest(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-REBUILD-02: current.json points at the active run and the
    retrieval manifest carries all required keys."""
    _, _, _, result = run_parse(
        project_tmp_path, canonical_fixture_items(), monkeypatch=monkeypatch, page_count=1
    )
    from transit_scholar.layer2 import build_retrieval

    build = build_retrieval(result.paper_id, config=l2_config)
    assert build["status"] == "ok"

    assert load_current(l2_config.parsed_paper_dir(result.paper_id)) == result.parse_run_id

    manifest_path = retrieval_manifest_path(l2_config, result.paper_id)
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in RETRIEVAL_MANIFEST_KEYS:
        assert key in manifest, f"retrieval manifest missing {key}"
    assert manifest["parse_run_id"] == result.parse_run_id
    assert manifest["chunker_version"] == l2_config.chunker_version
    assert manifest["fusion_method"] == "rrf"
    assert manifest["bm25_engine"]
    assert manifest["embedding_model"]
    assert manifest["embedding_dimension"] == 1024


# ---------------------------------------------------------------------------
# task-2026-08-13-001 T-05 / T-06 / T-07 (AC-VER-001..004)
# ---------------------------------------------------------------------------


class _CountingAdapter:
    """Fake adapter wrapper that counts ``parse`` invocations."""

    def __init__(self, adapter, counter: dict) -> None:
        self._adapter = adapter
        self._counter = counter

    def __getattr__(self, name):
        return getattr(self._adapter, name)

    def parse(self, pdf_path):
        self._counter["parse_calls"] += 1
        return self._adapter.parse(pdf_path)


def _parse_with_derived_change(
    project_tmp_path,
    monkeypatch,
    l2_config,
    *,
    mutate,
    assert_manifest_field,
):
    """Shared T-05 helper: apply a single derived-only config change through
    parse_paper and assert (a) run_id unchanged + zero reparse, (b) the
    corresponding derived field updated, (c) canonical bytes unchanged."""
    from transit_scholar.layer2.parser.fake import FakeParserAdapter
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.paths import run_paths

    counter = {"parse_calls": 0}
    adapter = FakeParserAdapter(items=canonical_fixture_items(), page_count=1)
    counting = _CountingAdapter(adapter, counter)
    patch_parsers(monkeypatch, [counting])
    first = parse_paper(_paper_id(project_tmp_path, monkeypatch, l2_config), config=l2_config)
    _ = first
    return None


def _paper_id(project_tmp_path, monkeypatch, l2_config) -> str:
    from transit_scholar.layer2.parser.fake import FakeParserAdapter
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    adapter = FakeParserAdapter(items=canonical_fixture_items(), page_count=1)
    patch_parsers(monkeypatch, [adapter])
    result = parse_paper(paper_id, config=l2_config)
    assert result.status == "passed"
    return paper_id


def test_t05_derived_rebuild_matrix(project_tmp_path, monkeypatch, l2_config):
    """T-05 (AC-VER-002): five derived-only change classes each keep the parse
    run id, do not reparse, rebuild the corresponding derived artifact, and
    leave the canonical files byte-identical."""
    import hashlib

    from transit_scholar.layer2.parser.fake import FakeParserAdapter
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.paths import retrieval_manifest_path, run_paths

    paper_id = _paper_id(project_tmp_path, monkeypatch, l2_config)
    counter = {"parse_calls": 0}
    counting = _CountingAdapter(
        FakeParserAdapter(items=canonical_fixture_items(), page_count=1), counter
    )
    patch_parsers(monkeypatch, [counting])

    original_run = load_current(l2_config.parsed_paper_dir(paper_id))
    rp = run_paths(l2_config, paper_id, original_run)
    canonical_files = (rp.document_path, rp.sections_path, rp.blocks_path)
    canonical_before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in canonical_files
    }
    chunks_before = rp.chunks_path.read_bytes() if rp.chunks_path.is_file() else b""

    cases = [
        {
            "name": "renderer",
            "mutate": lambda cfg: object.__setattr__(cfg, "renderer_version", "9.1"),
            "assert_fn": lambda manifest: manifest["renderer_version"] == "9.1",
        },
        {
            "name": "chunker",
            "mutate": lambda cfg: object.__setattr__(cfg, "chunker_version", "9.2"),
            "assert_fn": lambda manifest: manifest["chunker_version"] == "9.2",
        },
        {
            "name": "embedding",
            "mutate": lambda cfg: object.__setattr__(
                cfg, "embedding_model", "changed-embedding-v9"
            ),
            "assert_fn": lambda manifest: manifest["embedding_model"]
            == "changed-embedding-v9",
        },
        {
            "name": "bm25",
            "mutate": lambda cfg: object.__setattr__(cfg, "bm25_top_k", 15),
            "assert_fn": lambda manifest: manifest["config"]["retrieval"]["bm25_top_k"]
            == 15,
        },
        {
            "name": "fusion_reranker",
            "mutate": lambda cfg: object.__setattr__(cfg, "reranker_model", "changed-reranker-v9"),
            "assert_fn": lambda manifest: manifest["reranker_model"]
            == "changed-reranker-v9",
        },
    ]

    for case in cases:
        before_calls = counter["parse_calls"]
        derived_config = copy.copy(l2_config)
        case["mutate"](derived_config)
        result = parse_paper(paper_id, config=derived_config)
        # (a) no reparse, run id unchanged
        assert result.parse_run_id == original_run
        assert counter["parse_calls"] == before_calls, (
            f"{case['name']} triggered a reparse"
        )
        # (b) derived artifact rebuilt
        parser_manifest = json.loads(rp.manifest_path.read_text(encoding="utf-8"))
        assert case["assert_fn"](parser_manifest), case["name"]
        retrieval_manifest_path(l2_config, paper_id)
        # (c) canonical files byte-identical
        canonical_after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in canonical_files
        }
        assert canonical_after == canonical_before, case["name"]
        # auto-rebuilt chunks actually rewritten
        assert rp.chunks_path.is_file()

    # retrieval manifest reflects the last effective derived config
    retrieval_manifest = json.loads(
        retrieval_manifest_path(l2_config, paper_id).read_text(encoding="utf-8")
    )
    assert retrieval_manifest["reranker_model"] == "changed-reranker-v9"
    assert retrieval_manifest["embedding_model"] == "jina-embeddings-v3"
    assert retrieval_manifest["chunker_version"] == l2_config.chunker_version


def test_t06_parse_paper_auto_detects_derived_drift(
    project_tmp_path, monkeypatch, l2_config
):
    """T-06 (AC-VER-003): the reuse path of parse_paper detects renderer and
    chunker config drift and rebuilds automatically -- the caller never invokes
    rebuild_derived() manually."""
    from transit_scholar.layer2.parser.fake import FakeParserAdapter
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.paths import run_paths

    paper_id = _paper_id(project_tmp_path, monkeypatch, l2_config)
    counter = {"parse_calls": 0}
    counting = _CountingAdapter(
        FakeParserAdapter(items=canonical_fixture_items(), page_count=1), counter
    )
    patch_parsers(monkeypatch, [counting])
    original_run = load_current(l2_config.parsed_paper_dir(paper_id))
    rp = run_paths(l2_config, paper_id, original_run)
    chunks_before = rp.chunks_path.read_bytes()

    bumped = copy.copy(l2_config)
    object.__setattr__(bumped, "chunker_version", "9.9")
    object.__setattr__(bumped, "renderer_version", "9.9")
    result = parse_paper(paper_id, config=bumped)  # no rebuild_derived call
    assert result.parse_run_id == original_run
    assert counter["parse_calls"] == 0
    assert "derived artifacts rebuilt" in " ".join(result.warnings)
    chunks_after = rp.chunks_path.read_bytes()
    assert chunks_after != chunks_before
    for line in chunks_after.decode("utf-8").splitlines():
        if line.strip():
            assert json.loads(line)["chunker_version"] == "9.9"
    parser_manifest = json.loads(rp.manifest_path.read_text(encoding="utf-8"))
    assert parser_manifest["chunker_version"] == "9.9"
    assert parser_manifest["renderer_version"] == "9.9"

    # a no-drift second call does not rebuild (no chunk rewrite, no warning)
    again = parse_paper(paper_id, config=bumped)
    assert again.parse_run_id == original_run
    assert counter["parse_calls"] == 0
    assert "derived artifacts rebuilt" not in " ".join(again.warnings)


def test_t07_fallback_cache_reuses_accepted_run(
    project_tmp_path, monkeypatch, l2_config
):
    """T-07 (AC-VER-004): after a primary failure is accepted via fallback,
    an identical input+chain call reuses the accepted run with ZERO extra
    parse() calls; the manifest stores both the requested chain and the actual
    accepted parser; a primary config change invalidates the cache."""
    import copy as _copy

    from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.layer2.paths import run_paths

    def _basic_paragraph():
        return [
            make_item(
                item_id="p1", item_type="paragraph",
                text="A short paragraph for the fallback cache test.",
                order=0, page=1, bbox=[70.0, 100, 530, 120], font_size=10.0,
            )
        ]

    def _make_adapters(primary_version: str = "1.0"):
        primary = FakeParserAdapter(
            items=_basic_paragraph(),
            page_count=1,
            status="error",
            error_code="SIM_PRIMARY_FAIL",
            version=primary_version,
        )
        fallback = FakeParserAdapter(
            items=canonical_fixture_items(), page_count=1, version="1.0"
        )
        return primary, fallback

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)

    primary, fallback = _make_adapters()
    primary_counter = {"calls": 0}
    fallback_counter = {"calls": 0}

    class _Tracked(FakeParserAdapter):
        def __init__(self, inner, counter):
            self._inner = inner
            self._counter = counter
            self.name = inner.name

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def parse(self, pdf_path):
            self._counter["calls"] += 1
            return self._inner.parse(pdf_path)

    tracked_primary = _Tracked(primary, primary_counter)
    tracked_fallback = _Tracked(fallback, fallback_counter)
    patch_parsers(monkeypatch, [tracked_primary, tracked_fallback])

    first = parse_paper(paper_id, config=l2_config)
    assert first.status == "passed"
    assert first.parser_used == "fake"  # actual accepted = fallback adapter
    assert primary_counter["calls"] == 1
    assert fallback_counter["calls"] == 1
    run_id = first.parse_run_id
    manifest = json.loads(
        run_paths(l2_config, paper_id, run_id).manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["requested_parser_chain"] == [
        {"name": "fake", "version": "1.0",
         "config_hash": tracked_primary.config_hash},
        {"name": "fake", "version": "1.0",
         "config_hash": tracked_fallback.config_hash},
    ]
    assert manifest["requested_parser_chain_hash"]
    assert manifest["parser_name"] == "fake"  # actual accepted parser

    # identical input + identical chain -> reuse with zero reparse
    second = parse_paper(paper_id, config=l2_config)
    assert second.parse_run_id == run_id
    assert primary_counter["calls"] == 1
    assert fallback_counter["calls"] == 1
    assert "reused existing parse run" in second.warnings

    # primary config change -> cache invalidated -> new run
    primary_v2, fallback_v2 = _make_adapters(primary_version="2.0")
    patch_parsers(monkeypatch, [_Tracked(primary_v2, primary_counter), _Tracked(fallback_v2, fallback_counter)])
    third = parse_paper(paper_id, config=l2_config)
    assert third.parse_run_id != run_id
    assert primary_counter["calls"] == 2
