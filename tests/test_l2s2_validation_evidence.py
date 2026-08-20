"""L2S2 Package C deterministic tests: evidence integrity validation
(FR-C-003 / AC-C-03).

All canonical data are fake dicts in the ``CanonicalBlock.to_dict()`` shape;
no real PDF, parse run, or retrieval index is involved. Deterministic and
offline.
"""

from __future__ import annotations

import pytest

from transit_scholar.layer2.schema import RetrievalHit, SourceRef
from transit_scholar.layer2.schema_extraction import (
    EvidenceRef,
    FieldResult,
    SchemaInstance,
    bind_evidence,
    map_hits_to_candidates,
    validate_evidence_integrity,
)

PAPER_ID = "paper_001"
TEXT = "The bus holding control problem is studied with RL methods."


def _block(
    block_id: str = "blk_1",
    *,
    text: str = TEXT,
    paper_id: str = PAPER_ID,
    section_id: str | None = "Method",
    pages: list[int] | None = None,
    provenance: list[dict] | None = None,
) -> dict:
    return {
        "block_id": block_id,
        "paper_id": paper_id,
        "block_type": "body",
        "section_id": section_id,
        "order": 0,
        "text": text,
        "pages": list(pages if pages is not None else [2]),
        "provenance": list(provenance or []),
        "source_items": [],
        "relations": {},
        "content": {},
    }


def _instance(
    refs: list[EvidenceRef],
    *,
    paper_id: str = PAPER_ID,
    field_id: str = "f",
) -> SchemaInstance:
    return SchemaInstance(
        paper_id=paper_id,
        schema_id="test_schema",
        schema_version="1.0",
        fields={
            field_id: FieldResult(
                value="v",
                status="explicit",
                evidence=refs,
            ),
        },
    )


def _reader(block_map: dict):
    def reader(paper_id, block_ids):
        return {bid: block_map[bid] for bid in block_ids if bid in block_map}

    return reader


def _list_reader(block_map: dict):
    def reader(paper_id, block_ids):
        return [block_map[bid] for bid in block_ids if bid in block_map]

    return reader


def _types(instance, reader, **kwargs) -> list[str]:
    return [
        issue.type
        for issue in validate_evidence_integrity(instance, reader, **kwargs)
    ]


# ---------------------------------------------------------------------------
# valid evidence produces no issues
# ---------------------------------------------------------------------------


def test_valid_evidence_no_issues():
    block = _block()
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=4,
        char_end=23,
        pages=[2],
        section_path=["Method"],
        quote="bus holding control",
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


def test_l2s1_read_blocks_list_shape_is_supported():
    block = _block()
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=4,
        char_end=23,
        pages=[2],
        section_path=["Method"],
        quote="bus holding control",
    )
    assert validate_evidence_integrity(
        _instance([ref]), _list_reader({"blk_1": block})
    ) == []


def test_package_b_bound_full_hit_quote_containing_span_is_valid():
    block = _block(section_id="sec_001")
    hit = RetrievalHit(
        paper_id=PAPER_ID,
        chunk_id="chunk_1",
        score=1.0,
        retrieval_method="hybrid",
        section_path=["Method"],
        pages=[2],
        source_refs=[SourceRef(block_id="blk_1", char_start=4, char_end=23)],
        text=TEXT,
        rank=1,
    )
    refs = bind_evidence(["E1"], map_hits_to_candidates([hit]), field_id="f")
    assert validate_evidence_integrity(
        _instance(refs), _reader({"blk_1": block})
    ) == []


# ---------------------------------------------------------------------------
# block existence / paper identity
# ---------------------------------------------------------------------------


def test_block_missing_is_error():
    ref = EvidenceRef(block_id="ghost", char_start=0, char_end=4)
    issues = validate_evidence_integrity(_instance([ref]), _reader({}))
    match = [i for i in issues if i.type == "evidence_block_missing"]
    assert len(match) == 1
    assert match[0].severity == "error"
    assert match[0].fields == ["f"]


def test_paper_mismatch_is_error():
    block = _block(paper_id="paper_999")
    ref = EvidenceRef(block_id="blk_1", char_start=0, char_end=4)
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    match = [i for i in issues if i.type == "evidence_paper_mismatch"]
    assert len(match) == 1
    assert match[0].severity == "error"


def test_paper_id_override_is_used():
    block = _block(paper_id="paper_999")
    ref = EvidenceRef(block_id="blk_1", char_start=0, char_end=4)
    issues = validate_evidence_integrity(
        _instance([ref]),
        _reader({"blk_1": block}),
        paper_id="paper_999",
    )
    assert not [i for i in issues if i.type == "evidence_paper_mismatch"]


# ---------------------------------------------------------------------------
# char ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start,end",
    [
        (len(TEXT) + 1, len(TEXT) + 2),
        (0, len(TEXT) + 5),
    ],
)
def test_char_range_out_of_text_is_error(start, end):
    block = _block()
    ref = EvidenceRef(block_id="blk_1", char_start=start, char_end=end)
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    match = [i for i in issues if i.type == "evidence_char_range_invalid"]
    assert len(match) == 1
    assert match[0].severity == "error"


def test_char_range_end_equal_text_length_is_valid():
    block = _block()
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=len(TEXT),
        quote="",
        pages=[],
        section_path=[],
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


def test_char_range_inverted_via_model_construct_is_error():
    block = _block()
    instance = _instance(
        [EvidenceRef(block_id="blk_1", char_start=0, char_end=4)]
    )
    instance.fields["f"].evidence = [
        EvidenceRef.model_construct(
            block_id="blk_1", char_start=10, char_end=5
        )
    ]
    issues = validate_evidence_integrity(
        instance, _reader({"blk_1": block})
    )
    match = [i for i in issues if i.type == "evidence_char_range_invalid"]
    assert len(match) == 1
    assert match[0].severity == "error"


def test_invalid_range_skips_quote_check():
    block = _block()
    instance = _instance(
        [EvidenceRef(block_id="blk_1", char_start=0, char_end=4)]
    )
    instance.fields["f"].evidence = [
        EvidenceRef.model_construct(
            block_id="blk_1", char_start=10, char_end=5, quote="unrelated text"
        )
    ]
    issues = validate_evidence_integrity(
        instance, _reader({"blk_1": block})
    )
    assert [i.type for i in issues] == ["evidence_char_range_invalid"]


# ---------------------------------------------------------------------------
# quotes
# ---------------------------------------------------------------------------


def test_quote_equal_to_substring_ok():
    block = _block()
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=4,
        char_end=20,
        pages=[],
        section_path=[],
        quote=TEXT[4:20],
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


def test_quote_contained_in_substring_ok():
    block = _block()
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=4,
        char_end=20,
        pages=[],
        section_path=[],
        quote="bus holding",
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


def test_quote_mismatch_is_error():
    block = _block()
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=4,
        char_end=20,
        pages=[],
        section_path=[],
        quote="not in the block text at all",
    )
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    match = [i for i in issues if i.type == "evidence_quote_mismatch"]
    assert len(match) == 1
    assert match[0].severity == "error"


def test_empty_quote_is_skipped():
    block = _block()
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=4,
        char_end=20,
        pages=[],
        section_path=[],
        quote="",
    )
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    assert not [i for i in issues if i.type == "evidence_quote_mismatch"]


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


def test_pages_traceable_from_block_pages_ok():
    block = _block(pages=[2, 3])
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[2],
        section_path=[],
        quote=TEXT[:4],
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


def test_pages_traceable_from_provenance_ok():
    block = _block(pages=[], provenance=[{"page": 5, "char_start": 0, "char_end": 4}])
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[5],
        section_path=[],
        quote=TEXT[:4],
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


def test_pages_not_traceable_is_warning():
    block = _block(pages=[2])
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[9],
        section_path=[],
        quote=TEXT[:4],
    )
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    match = [i for i in issues if i.type == "evidence_pages_not_traceable"]
    assert len(match) == 1
    assert match[0].severity == "warning"
    assert match[0].fields == ["f"]


def test_empty_pages_are_skipped():
    block = _block()
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[],
        section_path=[],
        quote=TEXT[:4],
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


# ---------------------------------------------------------------------------
# section path
# ---------------------------------------------------------------------------


def test_section_path_matches_ok():
    block = _block(section_id="Method")
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[],
        section_path=["Introduction", "Method"],
        quote=TEXT[:4],
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


def test_section_mismatch_is_error():
    block = _block(section_id="Results")
    block["section_path"] = ["Results"]
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[],
        section_path=["Method"],
        quote=TEXT[:4],
    )
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    match = [i for i in issues if i.type == "evidence_section_mismatch"]
    assert len(match) == 1
    assert match[0].severity == "error"


def test_section_id_without_title_path_is_accepted_without_false_mismatch():
    block = _block(section_id="sec_001")
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[],
        section_path=["Method"],
        quote=TEXT[:4],
    )
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    assert issues == []


def test_comparable_section_path_mismatch_is_error():
    block = _block(section_id="sec_001")
    block["section_path"] = ["Results"]
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[],
        section_path=["Method"],
        quote=TEXT[:4],
    )
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    assert [i.type for i in issues] == ["evidence_section_mismatch"]


def test_section_unverifiable_is_warning():
    block = _block(section_id=None)
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[],
        section_path=["Method"],
        quote=TEXT[:4],
    )
    issues = validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    )
    match = [i for i in issues if i.type == "evidence_section_unverifiable"]
    assert len(match) == 1
    assert match[0].severity == "warning"


def test_empty_section_path_is_skipped():
    block = _block(section_id=None)
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=0,
        char_end=4,
        pages=[],
        section_path=[],
        quote=TEXT[:4],
    )
    assert validate_evidence_integrity(
        _instance([ref]), _reader({"blk_1": block})
    ) == []


# ---------------------------------------------------------------------------
# reader failures are explicit system failures, never not_found
# ---------------------------------------------------------------------------


def test_reader_exception_is_canonical_read_failed():
    def bad_reader(paper_id, block_ids):
        raise RuntimeError("boom")

    ref = EvidenceRef(block_id="blk_1", char_start=0, char_end=4)
    issues = validate_evidence_integrity(_instance([ref]), bad_reader)
    assert [i.type for i in issues] == ["canonical_read_failed"]
    assert issues[0].severity == "error"
    assert "RuntimeError" in issues[0].message
    assert "not_found" not in issues[0].type


def test_reader_none_return_is_canonical_read_failed():
    def none_reader(paper_id, block_ids):
        return None

    ref = EvidenceRef(block_id="blk_1", char_start=0, char_end=4)
    issues = validate_evidence_integrity(_instance([ref]), none_reader)
    assert [i.type for i in issues] == ["canonical_read_failed"]
    assert issues[0].severity == "error"


def test_reader_not_provided_is_canonical_read_failed():
    ref = EvidenceRef(block_id="blk_1", char_start=0, char_end=4)
    issues = validate_evidence_integrity(_instance([ref]), None)
    assert [i.type for i in issues] == ["canonical_read_failed"]
    assert issues[0].severity == "error"


def test_reader_malformed_list_return_is_canonical_read_failed():
    def malformed_reader(paper_id, block_ids):
        return [{"paper_id": PAPER_ID}]

    ref = EvidenceRef(block_id="blk_1", char_start=0, char_end=4)
    issues = validate_evidence_integrity(_instance([ref]), malformed_reader)
    assert [i.type for i in issues] == ["canonical_read_failed"]
    assert issues[0].severity == "error"


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_evidence_validation_is_deterministic():
    block = _block(pages=[2])
    block["section_path"] = ["Method"]
    ref = EvidenceRef(
        block_id="blk_1",
        char_start=4,
        char_end=20,
        pages=[9],
        section_path=["Results"],
        quote="not present",
    )
    instance = _instance([ref])
    reader = _reader({"blk_1": block})
    first = validate_evidence_integrity(instance, reader)
    second = validate_evidence_integrity(instance, reader)
    assert [i.model_dump() for i in first] == [i.model_dump() for i in second]
    assert [i.type for i in first] == [
        "evidence_quote_mismatch",
        "evidence_pages_not_traceable",
        "evidence_section_mismatch",
    ]
