"""L2S2 Package B deterministic tests: candidate evidence mapping and binding.

Covers requirements.md FR-B-005/006 and acceptance criteria AC-L2S2B-07/08 plus
the FR-003 per-ref provenance rules (AC-T001-F10/F11/F12/F13): stable E1..En
numbering, provenance-only-from-RetrievalHit/SourceRef binding, per-ref quote
``block_text[char_start:char_end]``, per-ref pages/section_path (never
candidate-wide data shared across refs of a multi-ref candidate), unknown
evidence id failure, empty-source-ref binding failure without fabricated
EvidenceRef, and deterministic multi-SourceRef handling.
"""

from __future__ import annotations

import pytest

from transit_scholar.layer2.schema import RetrievalHit, SourceRef
from transit_scholar.layer2.schema_extraction import (
    CandidateEvidence,
    EvidenceRef,
    EvidenceBindingError,
    SourceRefRecord,
    UnknownEvidenceIdError,
    bind_evidence,
    enrich_candidates_with_blocks,
    map_hits_to_candidates,
)


def _source_ref(block_id: str, start: int, end: int) -> SourceRef:
    return SourceRef(block_id=block_id, char_start=start, char_end=end)


def _hit(
    rank: int,
    *,
    chunk_id: str = "chunk-1",
    method: str = "hybrid",
    score: float = 1.0,
    source_refs: list[SourceRef] | None = None,
    pages: list[int] | None = None,
    section_path: list[str] | None = None,
    text: str | None = None,
    paper_id: str = "paper_001",
) -> RetrievalHit:
    return RetrievalHit(
        paper_id=paper_id,
        chunk_id=chunk_id,
        score=score,
        retrieval_method=method,
        section_path=section_path or ["Method"],
        pages=pages or [2],
        source_refs=source_refs or [],
        text=text or f"text-{rank}",
        rank=rank,
    )


# ---------------------------------------------------------------------------
# AC-L2S2B-07 candidate evidence mapping
# ---------------------------------------------------------------------------


def test_map_hits_assigns_e1_e2_e3_in_rank_order():
    hits = [
        _hit(rank=3, chunk_id="c3"),
        _hit(rank=1, chunk_id="c1"),
        _hit(rank=2, chunk_id="c2"),
    ]
    candidates = map_hits_to_candidates(hits)
    assert [c.evidence_id for c in candidates] == ["E1", "E2", "E3"]
    assert [c.chunk_id for c in candidates] == ["c1", "c2", "c3"]
    assert [c.rank for c in candidates] == [1, 2, 3]


def test_map_hits_tie_ranks_keep_input_order():
    hits = [
        _hit(rank=1, chunk_id="first"),
        _hit(rank=1, chunk_id="second"),
    ]
    candidates = map_hits_to_candidates(hits)
    assert [c.evidence_id for c in candidates] == ["E1", "E2"]
    assert candidates[0].chunk_id == "first"
    assert candidates[1].chunk_id == "second"


def test_map_hits_identical_input_identical_mapping():
    hits = [
        _hit(rank=2, chunk_id="c2"),
        _hit(rank=1, chunk_id="c1"),
    ]
    first = map_hits_to_candidates(hits)
    second = map_hits_to_candidates(hits)
    assert first == second
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]


def test_candidate_records_all_fields_from_hit():
    refs = [_source_ref("blk_1", 0, 10)]
    hit = _hit(
        rank=1,
        chunk_id="chunk-9",
        method="bm25",
        score=0.73,
        source_refs=refs,
        pages=[4, 5],
        section_path=["3 Method", "3.2 Details"],
        text="the reward is defined as ...",
    )
    candidate = map_hits_to_candidates([hit])[0]
    assert candidate.evidence_id == "E1"
    assert candidate.rank == 1
    assert candidate.method == "bm25"
    assert candidate.score == 0.73
    assert candidate.chunk_id == "chunk-9"
    assert candidate.pages == [4, 5]
    assert candidate.section_path == ["3 Method", "3.2 Details"]
    assert candidate.text == "the reward is defined as ..."
    assert len(candidate.source_refs) == 1
    assert candidate.source_refs[0].block_id == "blk_1"
    assert candidate.source_refs[0].char_start == 0
    assert candidate.source_refs[0].char_end == 10
    assert candidate.source_refs[0].text is None
    assert candidate.source_refs[0].pages is None
    assert candidate.source_refs[0].section_path is None


def test_candidate_is_json_serializable():
    candidate = map_hits_to_candidates([_hit(rank=1)])[0]
    import json

    payload = candidate.model_dump_json()
    assert json.loads(payload)["evidence_id"] == "E1"


# ---------------------------------------------------------------------------
# AC-T001-F10: per-ref provenance + deterministic quote slice
# ---------------------------------------------------------------------------


def _one_candidate(**kwargs) -> CandidateEvidence:
    return map_hits_to_candidates([_hit(rank=1, **kwargs)])[0]


def test_bind_evidence_uses_source_ref_for_block_and_chars():
    candidate = _one_candidate(
        source_refs=[_source_ref("blk_7", 12, 34)],
        text="x" * 40,
        pages=[1],
        section_path=["Method"],
    )
    refs = bind_evidence(["E1"], [candidate], field_id="f1")
    assert len(refs) == 1
    ref = refs[0]
    assert isinstance(ref, EvidenceRef)
    assert ref.block_id == "blk_7"
    assert ref.char_start == 12
    assert ref.char_end == 34
    assert ref.quote == "x" * (34 - 12)


def test_bind_evidence_quote_is_deterministic_substring_at_range():
    """AC-T001-F10 / required test 5: quote == text[char_start:char_end]."""
    text = "the quick brown fox jumps over"
    candidate = _one_candidate(
        source_refs=[_source_ref("blk_7", 4, 15)],
        text=text,
        pages=[3],
        section_path=["2 Formulation"],
    )
    ref = bind_evidence(["E1"], [candidate], field_id="f1")[0]
    assert ref.quote == text[4:15]  # "quick brown"
    assert ref.pages == [3]
    assert ref.section_path == ["2 Formulation"]
    assert ref.quote != text  # never the whole hit text


def test_bind_evidence_full_span_quote_equals_text():
    text = "quote from the paper"
    candidate = _one_candidate(
        source_refs=[_source_ref("blk_1", 0, len(text))],
        text=text,
        pages=[3],
        section_path=["2 Formulation"],
    )
    ref = bind_evidence(["E1"], [candidate], field_id="f1")[0]
    assert ref.quote == text


def test_bind_evidence_multi_ref_uses_per_ref_pages_not_shared():
    """AC-T001-F10 / required test 6: a multi-ref candidate never reuses the
    candidate-wide pages; each ref carries its own canonical block pages."""
    candidate = _one_candidate(
        source_refs=[
            SourceRefRecord(block_id="blk_a", char_start=0, char_end=5),
            SourceRefRecord(block_id="blk_b", char_start=10, char_end=20),
            SourceRefRecord(block_id="blk_c", char_start=30, char_end=40),
        ],
        pages=[5],
        section_path=["Results"],
        text="shared quote",
    )
    candidate.source_refs[0].pages = [1]
    candidate.source_refs[1].pages = [2, 3]
    candidate.source_refs[2].pages = [4]
    candidate.source_refs[0].section_path = ["Sec A"]
    candidate.source_refs[1].section_path = ["Sec B"]
    candidate.source_refs[2].section_path = ["Sec C"]
    candidate.source_refs[0].text = "aaaaa bbbbb ccccc ddddd eeeee fffff ggggg hhhh"
    candidate.source_refs[1].text = "aaaaa bbbbb ccccc ddddd eeeee fffff ggggg hhhh"
    candidate.source_refs[2].text = "aaaaa bbbbb ccccc ddddd eeeee fffff ggggg hhhh"
    refs = bind_evidence(["E1"], [candidate], field_id="f1")
    assert [r.block_id for r in refs] == ["blk_a", "blk_b", "blk_c"]
    assert refs[0].pages == [1]
    assert refs[1].pages == [2, 3]
    assert refs[2].pages == [4]
    assert [r.section_path for r in refs] == [["Sec A"], ["Sec B"], ["Sec C"]]
    text = candidate.source_refs[0].text
    assert refs[0].quote == text[0:5]
    assert refs[1].quote == text[10:20]
    assert refs[2].quote == text[30:40]
    assert refs[0].quote != "shared quote"


def test_bind_evidence_multi_ref_without_per_ref_data_fails_no_fabrication():
    """AC-T001-F11: multi-ref candidate with no per-ref provenance raises
    EvidenceBindingError instead of sharing candidate-wide data."""
    candidate = _one_candidate(
        source_refs=[
            _source_ref("blk_a", 0, 5),
            _source_ref("blk_b", 10, 20),
        ],
        pages=[5],
        section_path=["Results"],
        text="shared quote",
    )
    with pytest.raises(EvidenceBindingError) as excinfo:
        bind_evidence(["E1"], [candidate], field_id="f1")
    assert excinfo.value.error_code == "evidence_binding_failed"
    assert "per-ref" in str(excinfo.value)


def test_bind_evidence_multi_ref_without_per_ref_data_ok_when_fallback_disallowed_not_used_as_shared():
    candidate = _one_candidate(
        source_refs=[
            SourceRefRecord(block_id="blk_a", char_start=0, char_end=5),
            SourceRefRecord(block_id="blk_b", char_start=10, char_end=20),
        ],
        pages=[5],
        section_path=["Results"],
        text="candidate-wide should never leak",
    )
    candidate.source_refs[0].pages = [1]
    candidate.source_refs[1].pages = [2]
    candidate.source_refs[0].section_path = ["Sec A"]
    candidate.source_refs[1].section_path = ["Sec B"]
    text_a = "aaaaa"
    text_b = "bbbbbbbbbbbbbbbbbbbb"  # 20 chars
    candidate.source_refs[0].text = text_a
    candidate.source_refs[1].text = text_b
    refs = bind_evidence(["E1"], [candidate], field_id="f1")
    assert refs[0].quote == text_a[0:5]
    assert refs[1].quote == text_b[10:20]


def test_bind_evidence_skips_unbound_ref_binds_valid_ones():
    """AC-T001-F11 skip-with-warning: a multi-ref candidate whose figure block
    has no canonical text still binds the other (caption/paragraph) refs; the
    skipped ref is recorded in ``warnings_out`` and never fabricated."""
    text_a = "canonical paragraph text"
    text_c = "caption text for the figure"
    candidate = _one_candidate(
        source_refs=[
            SourceRefRecord(block_id="blk_figure", char_start=0, char_end=0),
            SourceRefRecord(block_id="blk_caption", char_start=0, char_end=20),
            SourceRefRecord(block_id="blk_para", char_start=3, char_end=12),
        ],
        pages=[9],
        section_path=["Results"],
        text="chunk-level text that must not leak",
    )
    # figure block resolved with no text -> per-ref text stays None and is skipped
    candidate.source_refs[0].pages = [9]
    candidate.source_refs[0].section_path = ["Results"]
    candidate.source_refs[1].text = text_c
    candidate.source_refs[1].pages = [9]
    candidate.source_refs[1].section_path = ["Results"]
    candidate.source_refs[2].text = text_a
    candidate.source_refs[2].pages = [9]
    candidate.source_refs[2].section_path = ["Results"]
    warnings: list[str] = []
    refs = bind_evidence(
        ["E1"], [candidate], field_id="f1",
        allow_candidate_fallback=False, warnings_out=warnings,
    )
    assert [r.block_id for r in refs] == ["blk_caption", "blk_para"]
    assert refs[0].quote == text_c[0:20]
    assert refs[1].quote == text_a[3:12]
    assert refs[0].quote != "chunk-level text that must not leak"
    assert len(warnings) == 1
    assert "blk_figure" in warnings[0]
    assert "unbound-able" in warnings[0]


def test_bind_evidence_all_refs_unbound_raises_no_fabrication():
    """AC-T001-F11: when every selected ref is unbound-able, the result is an
    explicit EvidenceBindingError (engine falls back to unclear) — never an
    assertive value with empty evidence."""
    candidate = _one_candidate(
        source_refs=[
            SourceRefRecord(block_id="blk_fig1", char_start=0, char_end=0),
            SourceRefRecord(block_id="blk_fig2", char_start=0, char_end=0),
        ],
        pages=[9],
        section_path=["Results"],
        text="  chunk text  ",
    )
    candidate.source_refs[0].pages = [9]
    candidate.source_refs[0].section_path = ["Results"]
    candidate.source_refs[1].pages = [9]
    candidate.source_refs[1].section_path = ["Results"]
    with pytest.raises(EvidenceBindingError) as excinfo:
        bind_evidence(
            ["E1"], [candidate], field_id="f1",
            allow_candidate_fallback=False,
        )
    assert excinfo.value.error_code == "evidence_binding_failed"
    assert "per-ref" in str(excinfo.value)
    assert "zero evidence" in str(excinfo.value)


def test_bind_evidence_skipped_ref_warning_also_collected_for_invalid_range():
    """Skipped refs due to a char range invalid for the canonical text are
    recorded as warnings, not fabricated, when other refs bind."""
    text = "abcdefghij"
    candidate = _one_candidate(
        source_refs=[
            SourceRefRecord(block_id="blk_bad", char_start=3, char_end=99),
            SourceRefRecord(block_id="blk_ok", char_start=0, char_end=4),
        ],
        pages=[1],
        section_path=["Method"],
        text="candidate chunk text",
    )
    candidate.source_refs[0].text = text
    candidate.source_refs[0].pages = [1]
    candidate.source_refs[0].section_path = ["Method"]
    candidate.source_refs[1].text = text
    candidate.source_refs[1].pages = [1]
    candidate.source_refs[1].section_path = ["Method"]
    warnings: list[str] = []
    refs = bind_evidence(
        ["E1"], [candidate], field_id="f1",
        allow_candidate_fallback=False, warnings_out=warnings,
    )
    assert [r.block_id for r in refs] == ["blk_ok"]
    assert refs[0].quote == "abcd"
    assert len(warnings) == 1
    assert "blk_bad" in warnings[0]
    assert "char range" in warnings[0]


def test_enrich_candidates_with_blocks_fills_per_ref_provenance():
    """AC-T001-F13: canonical block data fills each ref's text/pages and
    section_path; quote becomes the canonical substring."""
    block_text = "canonical block text for evidence span"
    block_map = {
        "blk_a": {
            "block_id": "blk_a",
            "paper_id": "paper_001",
            "text": block_text,
            "pages": [2, 3],
            "section_path": ["3 Method"],
            "provenance": [],
        },
        "blk_b": {
            "block_id": "blk_b",
            "paper_id": "paper_001",
            "text": block_text,
            "pages": [],
            "provenance": [{"page": 4, "char_start": 0, "char_end": 5}],
            "section_path": ["3 Method"],
        },
    }
    candidate = _one_candidate(
        source_refs=[
            _source_ref("blk_a", 0, 8),
            _source_ref("blk_b", 9, 14),
        ],
        pages=[99],
        section_path=["WRONG"],
        text="candidate wide chunk text",
    )
    enrich_candidates_with_blocks([candidate], block_map)
    assert candidate.source_refs[0].text == block_text
    assert candidate.source_refs[0].pages == [2, 3]
    assert candidate.source_refs[0].section_path == ["3 Method"]
    assert candidate.source_refs[1].pages == [4]

    refs = bind_evidence(["E1"], [candidate], field_id="f1")
    assert [r.block_id for r in refs] == ["blk_a", "blk_b"]
    assert refs[0].quote == block_text[0:8]
    assert refs[1].quote == block_text[9:14]
    assert refs[0].pages == [2, 3]
    assert refs[1].pages == [4]
    assert refs[0].section_path == ["3 Method"]
    assert refs[1].section_path == ["3 Method"]
    assert all(r.pages != [99] for r in refs)
    assert all(r.section_path != ["WRONG"] for r in refs)


def test_enrich_handles_list_reader_shape():
    block = {
        "block_id": "blk_a",
        "paper_id": "paper_001",
        "text": "abcdefghij",
        "pages": [2],
        "section_path": ["Method"],
    }
    candidate = _one_candidate(source_refs=[_source_ref("blk_a", 0, 4)])
    enrich_candidates_with_blocks([candidate], {"blk_a": block})
    assert candidate.source_refs[0].text == "abcdefghij"
    ref = bind_evidence(["E1"], [candidate], field_id="f1")[0]
    assert ref.quote == "abcd"
    assert ref.pages == [2]


def test_enrich_missing_block_leaves_per_ref_untouched():
    candidate = _one_candidate(
        source_refs=[_source_ref("ghost", 0, 4)],
        text="some text",
        pages=[1],
        section_path=["Method"],
    )
    enrich_candidates_with_blocks([candidate], {})
    assert candidate.source_refs[0].text is None
    # single-ref candidate: legal fallback still works
    ref = bind_evidence(["E1"], [candidate], field_id="f1")[0]
    assert ref.quote == "some text"[0:4]


def test_bind_evidence_invalid_char_range_never_binds():
    candidate = _one_candidate(
        source_refs=[_source_ref("blk_1", 3, 40)],
        text="short text",
        pages=[1],
        section_path=["Method"],
    )
    with pytest.raises(EvidenceBindingError):
        bind_evidence(["E1"], [candidate], field_id="f1")


# ---------------------------------------------------------------------------
# AC-L2S2B-08 evidence binding (unchanged semantics)
# ---------------------------------------------------------------------------


def test_bind_evidence_pages_section_path_from_single_ref_fallback():
    candidate = _one_candidate(
        source_refs=[_source_ref("blk_7", 0, 5)],
        text="quote > shorter",
        pages=[3],
        section_path=["2 Formulation"],
    )
    ref = bind_evidence(["E1"], [candidate], field_id="f1")[0]
    assert ref.pages == [3]
    assert ref.section_path == ["2 Formulation"]
    assert ref.quote == "quote"


def test_bind_evidence_dedupes_duplicate_source_refs():
    candidate = _one_candidate(
        source_refs=[
            SourceRefRecord(block_id="blk_a", char_start=0, char_end=5),
            SourceRefRecord(block_id="blk_a", char_start=0, char_end=5),
            SourceRefRecord(block_id="blk_b", char_start=1, char_end=2),
        ]
    )
    text = "abcdefghij"
    candidate.source_refs[0].text = text
    candidate.source_refs[0].pages = [1]
    candidate.source_refs[0].section_path = ["Method"]
    candidate.source_refs[2].text = text
    candidate.source_refs[2].pages = [1]
    candidate.source_refs[2].section_path = ["Method"]
    refs = bind_evidence(["E1"], [candidate], field_id="f1")
    assert [r.block_id for r in refs] == ["blk_a", "blk_b"]


def test_bind_evidence_multiple_candidates_in_selection_order():
    candidates = map_hits_to_candidates(
        [
            _hit(rank=1, source_refs=[_source_ref("blk_1", 0, 1)]),
            _hit(rank=2, source_refs=[_source_ref("blk_2", 0, 1)]),
        ]
    )
    refs = bind_evidence(["E2", "E1"], candidates, field_id="f1")
    assert [r.block_id for r in refs] == ["blk_2", "blk_1"]


def test_bind_evidence_duplicate_evidence_ids_deduped():
    candidate = _one_candidate(source_refs=[_source_ref("blk_1", 0, 1)])
    refs = bind_evidence(["E1", "E1"], [candidate], field_id="f1")
    assert len(refs) == 1


def test_bind_evidence_empty_ids_returns_empty_list():
    candidate = _one_candidate(source_refs=[_source_ref("blk_1", 0, 1)])
    assert bind_evidence([], [candidate], field_id="f1") == []


def test_unknown_evidence_id_error_explicit_failure():
    candidates = map_hits_to_candidates(
        [
            _hit(rank=1, source_refs=[_source_ref("blk_1", 0, 1)]),
            _hit(rank=2, source_refs=[_source_ref("blk_2", 0, 1)]),
        ]
    )
    with pytest.raises(UnknownEvidenceIdError) as excinfo:
        bind_evidence(["E1", "E9"], candidates, field_id="f1")
    assert excinfo.value.evidence_id == "E9"
    assert excinfo.value.field_id == "f1"
    assert "f1" in str(excinfo.value)
    assert "E9" in str(excinfo.value)
    assert excinfo.value.error_code == "unknown_evidence_id"


def test_bind_evidence_all_ids_checked_before_binding():
    """Unknown id fails up front: no partial binding happens."""
    candidates = map_hits_to_candidates(
        [
            _hit(rank=1, source_refs=[_source_ref("blk_1", 0, 1)]),
            _hit(rank=2, source_refs=[_source_ref("blk_2", 0, 1)]),
        ]
    )
    with pytest.raises(UnknownEvidenceIdError):
        bind_evidence(["E1", "E9"], candidates, field_id="f1")


def test_bind_evidence_candidate_without_source_refs_fails_no_fabrication():
    """AC-L2S2B-08 / Required test 10: no source refs -> explicit binding
    failure and no fabricated EvidenceRef."""
    candidate = _one_candidate(source_refs=[])
    with pytest.raises(EvidenceBindingError) as excinfo:
        bind_evidence(["E1"], [candidate], field_id="f1")
    assert excinfo.value.error_code == "evidence_binding_failed"
    assert excinfo.value.field_id == "f1"
    assert "E1" in str(excinfo.value)
    assert "fabricate" in str(excinfo.value).lower()


def test_bind_evidence_mixed_valid_and_invalid_candidate_fails():
    """A selected candidate without source refs fails even when another
    selected candidate is fine; no EvidenceRef is produced."""
    hits = [
        _hit(rank=1, source_refs=[_source_ref("blk_1", 0, 1)]),
        _hit(rank=2, source_refs=[]),
    ]
    candidates = map_hits_to_candidates(hits)
    with pytest.raises(EvidenceBindingError):
        bind_evidence(["E1", "E2"], candidates, field_id="f1")


# ---------------------------------------------------------------------------
# AC-T001-F12: determinism
# ---------------------------------------------------------------------------


def test_bind_evidence_deterministic_with_enrichment():
    block_map = {
        "blk_a": {
            "block_id": "blk_a",
            "paper_id": "paper_001",
            "text": "the bus holding control problem is studied with RL",
            "pages": [2],
            "section_path": ["4 Method"],
        },
        "blk_b": {
            "block_id": "blk_b",
            "paper_id": "paper_001",
            "text": "the bus holding control problem is studied with RL",
            "pages": [3],
            "section_path": ["4 Method"],
        },
    }

    def _make():
        candidate = _one_candidate(
            source_refs=[
                _source_ref("blk_a", 4, 9),
                _source_ref("blk_b", 20, 26),
            ]
        )
        enrich_candidates_with_blocks([candidate], block_map)
        return candidate

    first = bind_evidence(["E1"], [_make()], field_id="f1")
    second = bind_evidence(["E1"], [_make()], field_id="f1")
    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]
    assert [r.block_id for r in first] == ["blk_a", "blk_b"]
