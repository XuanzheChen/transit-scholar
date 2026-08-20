"""L2S2 Package B deterministic tests: extraction engine end-to-end.

Covers requirements.md FR-B-003/004/007/008/009/010/011 and acceptance
criteria AC-L2S2B-05..13 plus all Required Tests in acceptance.md. All runs
are fully offline: fake retrieval + fake LLM.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from transit_scholar.layer2.schema import RetrievalHit, RetrievalResult, SourceRef
from transit_scholar.layer2.schema_extraction import (
    SchemaDefinition,
    SectionDefinition,
    FieldDefinition,
    FakeLLMProvider,
    FakeRetrieval,
    ExtractionEngine,
    ExtractionRun,
    build_field_query,
    extract_schema_instance_in_memory,
    map_hits_to_candidates,
)
import transit_scholar.layer2.schema_extraction as schema_pkg

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_SUBPROCESS_MODULES = (
    "transit_scholar.config",
    "transit_scholar.db",
    "transit_scholar.layer2.schema",
    "transit_scholar.layer2.retrieval",
    "transit_scholar.layer2.parser",
    "transit_scholar.layer2.chunker",
    "transit_scholar.layer2.pipeline",
    "transit_scholar.layer2.normalizer",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _field(field_id: str, ftype: str = "string", **kwargs) -> FieldDefinition:
    values = dict(
        label=f"Label {field_id}",
        question=f"What is {field_id}?",
        description=f"Description of {field_id}.",
        type=ftype,
    )
    values.update(kwargs)
    return FieldDefinition(id=field_id, **values)


def _definition(**kwargs) -> SchemaDefinition:
    values = dict(
        schema_id="test_schema",
        version="1.0",
        sections=[
            SectionDefinition(
                id="s1",
                label="Section One",
                fields=[_field("f1"), _field("f2", "number")],
            ),
        ],
    )
    values.update(kwargs)
    return SchemaDefinition(**values)


def _hit(
    rank: int,
    *,
    chunk_id: str | None = "chunk-1",
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
        text=text or f"evidence text for rank {rank}",
        rank=rank,
    )


def _unavailable_result(method: str = "hybrid", error_code: str = "index_not_built") -> RetrievalResult:
    return RetrievalResult(
        status="unavailable",
        method=method,
        hits=[],
        error_code=error_code,
        error_message="retrieval index not built",
    )


def _ok_result(*hits: RetrievalHit, method: str = "hybrid") -> RetrievalResult:
    return RetrievalResult(status="ok", method=method, hits=list(hits))


def _hit_refs(*refs: SourceRef) -> list[SourceRef]:
    return list(refs)


def _ref(block_id: str, start: int, end: int) -> SourceRef:
    return SourceRef(block_id=block_id, char_start=start, char_end=end)


def _build_engine(
    definition: SchemaDefinition,
    paper_id: str = "paper_001",
    *,
    llm_responses: dict | None = None,
    llm_default: dict | None = None,
    retrieval: FakeRetrieval | None = None,
) -> tuple[ExtractionEngine, FakeLLMProvider]:
    llm = FakeLLMProvider(
        responses=llm_responses or {},
        default_response=llm_default,
    )
    if retrieval is None:
        retrieval = FakeRetrieval()
    engine = ExtractionEngine(llm_client=llm, retrieval=retrieval, top_k=4)
    return engine, llm


def _query_for(definition: SchemaDefinition, field_id: str) -> str:
    for section in definition.sections:
        for field in section.fields:
            if field.id == field_id:
                return build_field_query(field, section, definition).query
    raise AssertionError(f"field {field_id!r} not in definition")


# ---------------------------------------------------------------------------
# AC-L2S2B-05 field query construction
# ---------------------------------------------------------------------------


def test_build_field_query_non_empty_and_includes_four_elements():
    definition = _definition()
    section = definition.sections[0]
    field = section.fields[0]
    fq = build_field_query(field, section, definition)
    assert fq.query
    for expected in (section.label, field.label, field.question, field.description):
        assert expected in fq.query
    assert fq.metadata["field_id"] == "f1"
    assert fq.metadata["section_id"] == "s1"
    assert fq.metadata["schema_id"] == "test_schema"
    assert fq.metadata["schema_version"] == "1.0"


def test_build_field_query_deterministic():
    definition = _definition()
    section = definition.sections[0]
    field = section.fields[0]
    first = build_field_query(field, section, definition)
    second = build_field_query(field, section, definition)
    assert first.query == second.query
    assert first.metadata == second.metadata


# ---------------------------------------------------------------------------
# AC-L2S2B-06 / Required test 5+6 retrieval boundary behavior
# ---------------------------------------------------------------------------


def test_retrieval_unavailable_is_system_failure():
    definition = _definition()
    retrieval = FakeRetrieval(
        responses={
            ("paper_001", _query_for(definition, "f1")): _unavailable_result(
                error_code="index_not_built"
            )
        }
    )
    engine, llm = _build_engine(definition, llm_default={"value": "v", "status": "explicit"}, retrieval=retrieval)
    run = engine.run("paper_001", definition=definition)
    assert run.instance is not None
    # AC-T001-F01/F05: the field is never dropped; it carries an unclear
    # placeholder and the manifest error fields are retained.
    assert "f1" in run.instance.fields
    assert run.instance.fields["f1"].status == "unclear"
    assert run.instance.fields["f1"].value is None
    assert run.instance.fields["f1"].evidence == []
    assert "retrieval_unavailable" in run.instance.fields["f1"].notes
    entry = run.manifest.fields[0]
    assert entry.field_id == "f1"
    assert entry.error_code == "retrieval_unavailable"
    assert entry.retrieval_status == "unavailable"
    assert entry.retrieval_error_code == "index_not_built"
    assert entry.field_result_status == "unclear"
    assert llm.calls == []  # LLM never called on system failure


def test_retrieval_ok_no_hits_produces_not_found():
    definition = _definition()
    engine, llm = _build_engine(definition, llm_default={"value": "v", "status": "explicit"})
    run = engine.run("paper_001", definition=definition)
    assert run.instance is not None
    assert run.instance.fields["f1"].status == "not_found"
    assert run.instance.fields["f2"].status == "not_found"
    entry = run.manifest.fields[0]
    assert entry.error_code is None
    assert entry.field_result_status == "not_found"
    assert entry.retrieval_status == "ok"
    assert entry.candidate_ids == []
    assert llm.calls == []  # no LLM call when there are no candidates


def test_retrieval_boundary_exception_is_system_failure():
    class BrokenRetrieval:
        def retrieve(self, paper_id, query, top_k):
            raise RuntimeError("index exploded")

    engine = ExtractionEngine(
        llm_client=FakeLLMProvider(),
        retrieval=BrokenRetrieval(),
        top_k=4,
    )
    run = engine.run("paper_001", definition=_definition())
    entry = run.manifest.fields[0]
    assert entry.error_code == "retrieval_unavailable"
    assert entry.retrieval_error_code == "boundary_exception"
    assert "f1" in run.instance.fields
    assert run.instance.fields["f1"].status == "unclear"


# ---------------------------------------------------------------------------
# AC-L2S2B-09 / Required tests 1-4 structured extraction
# ---------------------------------------------------------------------------


def test_valid_llm_output_produces_field_result_with_bound_evidence():
    definition = _definition()
    hit = _hit(
        rank=1,
        chunk_id="chunk-9",
        source_refs=_hit_refs(_ref("blk_12", 0, 27)),
        pages=[3],
        section_path=["Method"],
        text="control strategy is holding",
    )
    engine, llm = _build_engine(
        definition,
        llm_responses={
            "f1": {
                "value": "holding",
                "status": "explicit",
                "evidence_ids": ["E1"],
                "confidence": 0.9,
                "notes": "clear statement",
            }
        },
        retrieval=FakeRetrieval(
            responses={
                ("paper_001", _query_for(definition, "f1")): _ok_result(hit)
            }
        ),
    )
    run = engine.run("paper_001", definition=definition)
    result = run.instance.fields["f1"]
    assert result.value == "holding"
    assert result.status == "explicit"
    assert result.confidence == 0.9
    assert result.notes == "clear statement"
    assert len(result.evidence) == 1
    ref = result.evidence[0]
    assert ref.block_id == "blk_12"
    assert ref.char_start == 0
    assert ref.char_end == 27
    assert ref.pages == [3]
    assert ref.section_path == ["Method"]
    assert ref.quote == "control strategy is holding"


def test_evidence_provenance_comes_from_hit_not_llm():
    """Required test 4: provenance fields (pages/section_path/quote/block) are
    backfilled by program logic from RetrievalHit/SourceRef; the LLM payload
    cannot carry them. The quote is the deterministic substring at the ref's
    char range."""
    definition = _definition()
    text = "real paper text"
    hit = _hit(
        rank=1,
        source_refs=_hit_refs(_ref("blk_7", 0, len(text))),
        pages=[7],
        section_path=["4 Results"],
        text=text,
    )
    engine, _ = _build_engine(
        definition,
        llm_responses={
            "f1": {"value": "x", "status": "explicit", "evidence_ids": ["E1"]}
        },
        retrieval=FakeRetrieval(
            responses={
                ("paper_001", _query_for(definition, "f1")): _ok_result(hit)
            }
        ),
    )
    run = engine.run("paper_001", definition=definition)
    ref = run.instance.fields["f1"].evidence[0]
    assert ref.block_id == "blk_7"
    assert ref.char_start == 0
    assert ref.char_end == len(text)
    assert ref.pages == [7]
    assert ref.section_path == ["4 Results"]
    assert ref.quote == text
    assert run.manifest.fields[0].llm_output["value"] == "x"


@pytest.mark.parametrize(
    "bad_preset",
    [
        {"value": "x", "status": "made_up_status"},
        {"value": "x", "status": "explicit", "confidence": 1.5},
        {"value": "x", "status": "explicit", "confidence": -0.2},
        {"value": "x", "status": "explicit", "block_id": "blk_1"},
        {"value": "x", "status": "explicit", "pages": [3]},
        {"value": "x", "status": "explicit", "quote": "nope"},
        {"value": "x", "status": "explicit", "section_path": ["nope"]},
    ],
)
def test_invalid_llm_output_explicit_failure_never_not_found(bad_preset):
    """Required test 2: invalid structured output -> explicit field failure,
    never a fake not_found. The field is kept as an unclear placeholder after
    the one bounded retry."""
    definition = _definition()
    hit = _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
    engine, _ = _build_engine(
        definition,
        llm_responses={"f1": bad_preset},
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
    )
    run = engine.run("paper_001", definition=definition)
    assert "f1" in run.instance.fields
    assert run.instance.fields["f1"].status == "unclear"
    assert run.instance.fields["f1"].value is None
    entry = run.manifest.fields[0]
    assert entry.error_code == "llm_invalid_output"
    assert entry.error_message and "f1" in entry.error_message
    assert entry.field_result_status == "unclear"


def test_unknown_evidence_id_field_failure():
    """Required test 3: unknown evidence id -> explicit failure with field id;
    after the bounded retry the field is an unclear placeholder."""
    definition = _definition()
    hit = _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
    engine, _ = _build_engine(
        definition,
        llm_responses={
            "f1": {
                "value": "x",
                "status": "explicit",
                "evidence_ids": ["E9"],
            }
        },
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
    )
    run = engine.run("paper_001", definition=definition)
    assert "f1" in run.instance.fields
    assert run.instance.fields["f1"].status == "unclear"
    entry = run.manifest.fields[0]
    assert entry.error_code == "unknown_evidence_id"
    assert "f1" in entry.error_message
    assert "E9" in entry.error_message


def test_evidence_binding_failure_when_candidate_has_no_source_refs():
    """Required test 10: selected candidate without source refs -> binding
    failure, no fabricated EvidenceRef; after the bounded retry the field is
    an unclear placeholder."""
    definition = _definition()
    hit = _hit(rank=1, source_refs=[])
    engine, _ = _build_engine(
        definition,
        llm_responses={
            "f1": {"value": "x", "status": "explicit", "evidence_ids": ["E1"]}
        },
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
    )
    run = engine.run("paper_001", definition=definition)
    assert "f1" in run.instance.fields
    assert run.instance.fields["f1"].status == "unclear"
    entry = run.manifest.fields[0]
    assert entry.error_code == "evidence_binding_failed"
    assert "f1" in entry.error_message


def test_engine_skips_empty_text_figure_ref_binds_surviving_refs_with_warning():
    """AC-T001-F11 skip-with-warning at engine level: a canonical reader that
    resolves a figure block with empty text (and a caption block with real
    text) lets the field succeed with only the bindable ref, and records the
    skipped figure ref in the manifest trace."""

    def _reader(paper_id, block_ids):
        block_map = {
            "blk_fig": {
                "block_id": "blk_fig",
                "paper_id": paper_id,
                "text": "",
                "pages": [9],
                "section_path": ["5 Results"],
                "provenance": [],
            },
            "blk_caption": {
                "block_id": "blk_caption",
                "paper_id": paper_id,
                "text": "Fig. 3: performance under headway-based holding",
                "pages": [9],
                "section_path": ["5 Results"],
                "provenance": [],
            },
        }
        return {bid: block_map[bid] for bid in block_ids if bid in block_map}

    definition = _definition()
    hit = _hit(
        rank=1,
        source_refs=_hit_refs(
            _ref("blk_fig", 0, 0),
            _ref("blk_caption", 0, 45),
        ),
        pages=[9],
        section_path=["5 Results"],
        text="Fig. 3: performance under headway-based holding",
    )
    llm = FakeLLMProvider(
        responses={"f1": {"value": "holding", "status": "explicit", "evidence_ids": ["E1"]}}
    )
    engine = ExtractionEngine(
        llm_client=llm,
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
        canonical_reader=_reader,
        top_k=4,
    )
    run = engine.run("paper_001", definition=definition)
    field = run.instance.fields["f1"]
    assert field.status == "explicit"
    assert field.value == "holding"
    assert [e.block_id for e in field.evidence] == ["blk_caption"]
    assert field.evidence[0].quote == "Fig. 3: performance under headway-based holding"[:45]
    entry = run.manifest.fields[0]
    assert entry.error_code is None
    assert entry.evidence_warnings is not None
    assert any("blk_fig" in w for w in entry.evidence_warnings)


def test_engine_all_selected_refs_unbound_still_unclear_after_retry():
    """AC-T001-F11: when every ref of every selected candidate is
    unbound-able even after the targeted retry, the field stays an unclear
    placeholder (never a fabricated not_found, never assertive-no-evidence)."""
    definition = _definition()
    hit = _hit(
        rank=1,
        source_refs=_hit_refs(_ref("blk_fig", 0, 0)),
        pages=[9],
        section_path=["5 Results"],
        text="figure-only chunk",
    )
    # canonical_reader returns the block but its text is empty -> the only ref
    # is unbound-able each attempt
    llm = FakeLLMProvider(
        default_response={
            "value": "holding",
            "status": "explicit",
            "evidence_ids": ["E1"],
        }
    )
    engine = ExtractionEngine(
        llm_client=llm,
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
        canonical_reader=lambda paper_id, bids: {
            bid: {"block_id": bid, "text": "", "pages": [9], "section_path": ["5 Results"]}
            for bid in bids
        },
        top_k=4,
    )
    run = engine.run("paper_001", definition=definition)
    field = run.instance.fields["f1"]
    assert field.status == "unclear"
    assert field.value is None
    assert field.evidence == []
    entry = run.manifest.fields[0]
    assert entry.error_code == "evidence_binding_failed"
    assert entry.retry_count == 1


def test_llm_not_found_status_without_evidence_is_legit_not_found():
    definition = _definition()
    hit = _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
    engine, _ = _build_engine(
        definition,
        llm_responses={"f1": {"value": None, "status": "not_found"}},
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
    )
    run = engine.run("paper_001", definition=definition)
    assert run.instance.fields["f1"].status == "not_found"
    entry = run.manifest.fields[0]
    assert entry.error_code is None
    assert entry.field_result_status == "not_found"


def test_llm_not_found_with_evidence_ids_rejected():
    definition = _definition()
    hit = _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
    engine, _ = _build_engine(
        definition,
        llm_responses={
            "f1": {"value": None, "status": "not_found", "evidence_ids": ["E1"]}
        },
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
    )
    run = engine.run("paper_001", definition=definition)
    # after the bounded retry the field is an unclear placeholder: an absent
    # status with evidence is never silently rewritten to a plain not_found
    assert "f1" in run.instance.fields
    assert run.instance.fields["f1"].status == "unclear"
    assert run.manifest.fields[0].error_code == "llm_invalid_output"


def test_absent_status_with_assertive_value_retries_then_clean_not_found():
    """Absent status must not carry a non-null value: first attempt returns
    not_found with a placeholder string, the corrective retry returns a legal
    not_found with value None, so the field is a clean not_found (2 LLM calls,
    retry feedback mentions null value)."""
    definition = _definition()
    hit = _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
    engine, llm = _build_engine(
        definition,
        llm_responses={
            "f1": [
                {"value": "unspecified", "status": "not_found"},
                {"value": None, "status": "not_found"},
            ]
        },
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
    )
    run = engine.run("paper_001", definition=definition)
    result = run.instance.fields["f1"]
    assert result.status == "not_found"
    assert result.value is None
    entry = run.manifest.fields[0]
    assert entry.error_code is None
    assert entry.retry_count == 1
    assert "non-null value" in entry.retry_feedback
    assert len(llm.calls) == 2


def test_absent_status_with_assertive_value_exhausted_keeps_placeholder():
    """After two not_found-with-value attempts the field is an unclear
    placeholder (value None) — never saved with a contradictory value."""
    definition = _definition()
    hit = _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
    engine, llm = _build_engine(
        definition,
        llm_responses={
            "f1": [
                {"value": "unspecified", "status": "not_found"},
                {"value": "not stated", "status": "not_found"},
            ]
        },
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
    )
    run = engine.run("paper_001", definition=definition)
    result = run.instance.fields["f1"]
    assert result.status == "unclear"
    assert result.value is None
    assert result.evidence == []
    entry = run.manifest.fields[0]
    assert entry.error_code == "llm_invalid_output"
    assert entry.retry_count == 1
    assert len(llm.calls) == 2


def test_llm_unavailable_client_failure_is_system_failure():
    class UnavailableClient:
        is_fake = False
        provider_name = "real"
        model_name = "x"

        def generate_structured(self, messages, output_schema, metadata=None):
            from transit_scholar.layer2.schema_extraction import LLMUnavailableError

            raise LLMUnavailableError("provider offline")

    definition = _definition()
    hit = _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
    retrieval = FakeRetrieval(
        responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
    )
    engine = ExtractionEngine(llm_client=UnavailableClient(), retrieval=retrieval, top_k=4)
    run = engine.run("paper_001", definition=definition)
    assert "f1" in run.instance.fields
    assert run.instance.fields["f1"].status == "unclear"
    entry = run.manifest.fields[0]
    assert entry.error_code == "llm_unavailable"
    assert "f1" in entry.error_message


# ---------------------------------------------------------------------------
# AC-L2S2B-10 / Required tests 7-9 SchemaInstance assembly + determinism
# ---------------------------------------------------------------------------


def test_e2e_two_fields_produce_schema_instance_and_trace():
    """Required test 7: fake end-to-end with two fields."""
    definition = _definition()
    f1_query = _query_for(definition, "f1")
    f2_query = _query_for(definition, "f2")
    retrieval = FakeRetrieval(
        responses={
            ("paper_001", f1_query): _ok_result(
                _hit(rank=1, chunk_id="c1", source_refs=_hit_refs(_ref("blk_a", 0, 10)))
            ),
            ("paper_001", f2_query): _ok_result(
                _hit(rank=1, chunk_id="c2", source_refs=_hit_refs(_ref("blk_b", 3, 9)))
            ),
        }
    )
    llm = FakeLLMProvider(
        responses={
            "f1": {"value": "holding", "status": "explicit", "evidence_ids": ["E1"], "confidence": 0.8},
            "f2": {"value": 0.6, "status": "inferred", "evidence_ids": ["E1"], "confidence": 0.6},
        }
    )
    engine = ExtractionEngine(llm_client=llm, retrieval=retrieval, top_k=4)
    run = engine.run("paper_001", definition=definition)
    assert isinstance(run, ExtractionRun)
    instance = run.instance
    assert instance.paper_id == "paper_001"
    assert instance.schema_id == "test_schema"
    assert instance.schema_version == "1.0"
    assert set(instance.fields) == {"f1", "f2"}
    assert instance.fields["f1"].status == "explicit"
    assert instance.fields["f2"].value == 0.6
    assert instance.fields["f1"].evidence[0].block_id == "blk_a"
    assert instance.fields["f2"].evidence[0].block_id == "blk_b"
    manifest = run.manifest
    assert manifest.run_id
    assert manifest.paper_id == "paper_001"
    assert manifest.schema_id == "test_schema"
    assert manifest.schema_version == "1.0"
    assert manifest.schema_hash
    assert manifest.llm_provider == "fake"
    assert manifest.llm_fake is True
    assert len(manifest.fields) == 2
    assert [e.field_id for e in manifest.fields] == ["f1", "f2"]


def test_trace_records_query_candidates_selected_statuses_and_errors():
    """Required test 8 + AC-L2S2B-11: trace content and JSON serializability."""
    definition = _definition()
    hit = _hit(rank=1, chunk_id="chunk-42", source_refs=_hit_refs(_ref("blk_1", 0, 5)))
    engine, _ = _build_engine(
        definition,
        llm_responses={
            "f1": {"value": "x", "status": "explicit", "evidence_ids": ["E1"]}
        },
        retrieval=FakeRetrieval(
            responses={("paper_001", _query_for(definition, "f1")): _ok_result(hit)}
        ),
    )
    run = engine.run("paper_001", definition=definition)
    entry = run.manifest.fields[0]
    assert entry.query
    assert entry.query_metadata["field_id"] == "f1"
    assert entry.retrieval_status == "ok"
    assert entry.retrieval_method == "hybrid"
    assert entry.hit_chunk_ids == ["chunk-42"]
    assert entry.candidate_ids == ["E1"]
    assert entry.llm_output is not None
    assert entry.llm_output["status"] == "explicit"
    assert entry.llm_output["evidence_ids"] == ["E1"]
    assert entry.selected_evidence_ids == ["E1"]
    assert entry.field_result_status == "explicit"
    assert entry.error_code is None
    assert entry.error_message is None
    payload = json.loads(run.model_dump_json())
    assert payload["manifest"]["run_id"] == run.manifest.run_id
    assert payload["instance"]["fields"]["f1"]["status"] == "explicit"


def test_engine_determinism_same_inputs_same_outputs():
    """Required test 9: same fake inputs -> same instance and trace except
    run_id/created_at; run_id stays unique."""
    definition = _definition()
    f1_query = _query_for(definition, "f1")
    f2_query = _query_for(definition, "f2")

    def _one_run(run_id):
        retrieval = FakeRetrieval(
            responses={
                ("paper_001", f1_query): _ok_result(
                    _hit(rank=1, chunk_id="c1", source_refs=_hit_refs(_ref("blk_a", 0, 10)))
                ),
                ("paper_001", f2_query): _ok_result(
                    _hit(rank=1, chunk_id="c2", source_refs=_hit_refs(_ref("blk_b", 3, 9)))
                ),
            }
        )
        llm = FakeLLMProvider(
            responses={
                "f1": {"value": "holding", "status": "explicit", "evidence_ids": ["E1"]},
                "f2": {"value": 0.6, "status": "inferred", "evidence_ids": ["E1"]},
            }
        )
        engine = ExtractionEngine(llm_client=llm, retrieval=retrieval, top_k=4)
        return engine.run("paper_001", definition=definition, run_id=run_id)

    first = _one_run("run-1")
    second = _one_run("run-2")
    assert first.manifest.run_id == "run-1"
    assert second.manifest.run_id == "run-2"
    assert first.manifest.run_id != second.manifest.run_id
    assert first.instance.model_dump() == second.instance.model_dump()
    first_trace = first.manifest.model_dump()
    second_trace = second.manifest.model_dump()
    first_trace.pop("run_id")
    second_trace.pop("run_id")
    first_trace.pop("created_at")
    second_trace.pop("created_at")
    assert first_trace == second_trace


def test_engine_run_id_auto_unique():
    definition = _definition()
    engine, _ = _build_engine(definition)
    first = engine.run("paper_001", definition=definition)
    second = engine.run("paper_001", definition=definition)
    assert first.manifest.run_id != second.manifest.run_id


# ---------------------------------------------------------------------------
# AC-L2S2B-12 / Required test 12 error model
# ---------------------------------------------------------------------------


def test_failed_fields_not_in_instance_but_recorded_in_trace():
    definition = _definition()
    f1_query = _query_for(definition, "f1")
    retrieval = FakeRetrieval(
        responses={
            ("paper_001", f1_query): _ok_result(
                _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
            )
        }
    )
    llm = FakeLLMProvider(
        responses={
            "f1": {"value": "x", "status": "bad_status"},  # fails
            "f2": {"value": 1.0, "status": "explicit"},  # no hits -> not_found
        },
        default_response={"value": "d", "status": "explicit"},
    )
    engine = ExtractionEngine(llm_client=llm, retrieval=retrieval, top_k=4)
    run = engine.run("paper_001", definition=definition)
    # both definition fields are always present (AC-T001-F01); f1 is an
    # unclear placeholder, f2 a real not_found
    assert set(run.instance.fields) == {"f1", "f2"}
    assert run.instance.fields["f1"].status == "unclear"
    assert run.instance.fields["f2"].status == "not_found"
    assert len(run.manifest.fields) == 2
    assert run.manifest.fields[0].error_code == "llm_invalid_output"
    assert run.manifest.fields[1].error_code is None


def test_schema_load_failure_run_level_error():
    """Required test 12: schema definition/load failure -> explicit run-level
    error distinct from not_found."""
    run = extract_schema_instance_in_memory("paper_001", "no_such_schema")
    assert isinstance(run, ExtractionRun)
    assert run.instance is None
    assert run.manifest.run_error_code == "schema_load_failed"
    assert "no_such_schema" in run.manifest.run_error_message
    assert run.manifest.schema_hash is None
    assert run.manifest.fields == []


def test_schema_load_failure_never_looks_like_not_found():
    run = extract_schema_instance_in_memory("paper_001", "no_such_schema")
    assert run.instance is None
    assert run.manifest.run_error_code == "schema_load_failed"
    assert not any(
        e.field_result_status == "not_found" for e in run.manifest.fields
    )


def test_all_error_codes_are_distinct_and_machine_readable():
    from transit_scholar.layer2.schema_extraction.engine import (
        ERROR_EVIDENCE_BINDING_FAILED,
        ERROR_LLM_INVALID_OUTPUT,
        ERROR_LLM_UNAVAILABLE,
        ERROR_RETRIEVAL_UNAVAILABLE,
        ERROR_SCHEMA_LOAD_FAILED,
        ERROR_UNKNOWN_EVIDENCE_ID,
    )

    codes = {
        ERROR_RETRIEVAL_UNAVAILABLE,
        ERROR_LLM_UNAVAILABLE,
        ERROR_LLM_INVALID_OUTPUT,
        ERROR_UNKNOWN_EVIDENCE_ID,
        ERROR_EVIDENCE_BINDING_FAILED,
        ERROR_SCHEMA_LOAD_FAILED,
    }
    assert len(codes) == 6


# ---------------------------------------------------------------------------
# AC-L2S2B-13 / Required test 11 public export + import isolation
# ---------------------------------------------------------------------------


def test_package_d_public_names_exported():
    """AC-D-16: Package D adds and exports these five public APIs."""
    package_d_names = {"extract_schema", "get_schema", "get_field", "validate_schema", "recheck_fields"}
    namespace = set(dir(schema_pkg))
    assert package_d_names <= namespace
    assert package_d_names <= set(schema_pkg.__all__)


def test_package_b_objects_exported():
    for name in (
        "LLMConfig",
        "FakeLLMProvider",
        "RealLLMClientStub",
        "resolve_llm_client",
        "FieldQuery",
        "build_field_query",
        "RetrievalBoundary",
        "FakeRetrieval",
        "HybridRetrievalWrapper",
        "CandidateEvidence",
        "map_hits_to_candidates",
        "bind_evidence",
        "FieldTraceEntry",
        "ExtractionManifest",
        "FieldExtractionLLMOutput",
        "ExtractionEngine",
        "ExtractionRun",
        "extract_schema_instance_in_memory",
        "SchemaLoadError",
        "LLMUnavailableError",
        "LLMInvalidOutputError",
        "UnknownEvidenceIdError",
        "EvidenceBindingError",
        "RetrievalUnavailableError",
    ):
        assert hasattr(schema_pkg, name), name


def test_package_b_import_isolation_in_fresh_subprocess():
    """Required test 11: importing Package B modules in a fresh interpreter
    must not import L2S1 retrieval/parser/chunker/normalizer/pipeline, db, or
    config. ``transit_scholar.layer2.schema`` is NOT allowed either (it is
    only referenced lazily / under TYPE_CHECKING)."""
    code = (
        "import sys; "
        "from transit_scholar.layer2.schema_extraction import ("
        "build_field_query, FieldQuery, FakeLLMProvider, FakeRetrieval, "
        "ExtractionEngine, extract_schema_instance_in_memory, "
        "CandidateEvidence, map_hits_to_candidates, bind_evidence, "
        "ExtractionManifest, FieldTraceEntry, LLMConfig, resolve_llm_client, "
        "FieldExtractionLLMOutput, build_extraction_messages); "
        "from transit_scholar.layer2.schema_extraction import ("
        "SchemaLoadError, RetrievalUnavailableError, LLMUnavailableError, "
        "LLMInvalidOutputError, UnknownEvidenceIdError, EvidenceBindingError); "
        "forbidden = ['transit_scholar.config', 'transit_scholar.db', "
        "'transit_scholar.layer2.schema', 'transit_scholar.layer2.retrieval', "
        "'transit_scholar.layer2.parser', 'transit_scholar.layer2.chunker', "
        "'transit_scholar.layer2.pipeline', 'transit_scholar.layer2.normalizer']; "
        "bad = [n for n in forbidden if n in sys.modules]; "
        "assert not bad, bad; print('package-b-isolated-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "package-b-isolated-ok" in result.stdout


def test_hybrid_retrieval_wrapper_imports_retrieval_only_lazily():
    """AC-L2S2B-06: the production wrapper exists but is never exercised by
    deterministic tests, and its import of the retrieval stack is lazy."""
    code = (
        "import sys; "
        "from transit_scholar.layer2.schema_extraction.retrieval import "
        "HybridRetrievalWrapper; "
        "assert 'transit_scholar.layer2.retrieval' not in sys.modules; "
        "wrapper = HybridRetrievalWrapper(top_k=3, rerank=False); "
        "assert wrapper.top_k == 3 and wrapper.rerank is False; "
        "assert 'transit_scholar.layer2.retrieval' not in sys.modules; "
        "print('lazy-import-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "lazy-import-ok" in result.stdout


# ---------------------------------------------------------------------------
# FR-B-011 entry function through the plugin loader
# ---------------------------------------------------------------------------


def test_entry_function_end_to_end_with_plugin_loader(monkeypatch, tmp_path):
    """extract_schema_instance_in_memory resolves the definition through the
    plugin loader and runs the full pipeline with injected fakes."""
    plugin_dir = tmp_path / "b_test_schema"
    plugin_dir.mkdir()
    (plugin_dir / "schema.yaml").write_text(
        "schema_id: b_test_schema\n"
        'version: "1.0"\n'
        "sections:\n"
        "  - id: overview\n"
        "    label: Overview\n"
        "    fields:\n"
        "      - id: headline\n"
        "        label: Headline\n"
        "        question: What is the headline?\n"
        "        type: string\n"
        "      - id: page_count\n"
        "        label: Page Count\n"
        "        question: How many pages?\n"
        "        type: number\n",
        encoding="utf-8",
    )
    from transit_scholar.layer2.schema_extraction import loader

    monkeypatch.setattr(loader, "plugins_root", lambda: tmp_path)
    definition = loader.get_schema_definition("b_test_schema")
    q_headline = _query_for(definition, "headline")
    q_page_count = _query_for(definition, "page_count")
    retrieval = FakeRetrieval(
        responses={
            ("paper_777", q_headline): _ok_result(
                _hit(rank=1, chunk_id="c1", source_refs=_hit_refs(_ref("blk_h", 0, 8)))
            ),
            ("paper_777", q_page_count): _ok_result(
                _hit(rank=1, chunk_id="c2", source_refs=_hit_refs(_ref("blk_p", 1, 4)))
            ),
        }
    )
    llm = FakeLLMProvider(
        responses={
            "headline": {"value": "On Bus Control", "status": "explicit", "evidence_ids": ["E1"]},
            "page_count": {"value": 12, "status": "explicit", "evidence_ids": ["E1"]},
        }
    )
    run = extract_schema_instance_in_memory(
        "paper_777", "b_test_schema", llm_client=llm, retrieval=retrieval, top_k=4
    )
    assert run.instance.paper_id == "paper_777"
    assert run.instance.schema_id == "b_test_schema"
    assert run.instance.schema_version == "1.0"
    assert set(run.instance.fields) == {"headline", "page_count"}
    assert run.instance.fields["headline"].value == "On Bus Control"
    assert run.instance.fields["page_count"].value == 12
    assert run.manifest.schema_hash == schema_pkg.compute_schema_hash(definition)
    assert [e.field_id for e in run.manifest.fields] == ["headline", "page_count"]


def test_entry_function_end_to_end_accepts_injected_canonical_reader(monkeypatch, tmp_path):
    """The entry function propagates an injected canonical_reader into the
    engine's evidence binding (AC-T001-F13)."""
    plugin_dir = tmp_path / "c2_test_schema"
    plugin_dir.mkdir()
    (plugin_dir / "schema.yaml").write_text(
        "schema_id: c2_test_schema\n"
        'version: "1.0"\n'
        "sections:\n"
        "  - id: overview\n"
        "    label: Overview\n"
        "    fields:\n"
        "      - id: headline\n"
        "        label: Headline\n"
        "        question: What is the headline?\n"
        "        type: string\n",
        encoding="utf-8",
    )
    from transit_scholar.layer2.schema_extraction import loader

    monkeypatch.setattr(loader, "plugins_root", lambda: tmp_path)
    definition = loader.get_schema_definition("c2_test_schema")
    text = "the bus control problem"
    block_map = {
        "blk_h": {
            "block_id": "blk_h",
            "paper_id": "paper_777",
            "text": text,
            "pages": [2],
            "section_path": ["Method"],
        }
    }
    retrieval = FakeRetrieval(
        responses={
            ("paper_777", _query_for(definition, "headline")): _ok_result(
                _hit(rank=1, chunk_id="c1", source_refs=_hit_refs(_ref("blk_h", 4, 9)))
            )
        }
    )
    llm = FakeLLMProvider(
        responses={
            "headline": {"value": "On Bus Control", "status": "explicit", "evidence_ids": ["E1"]}
        }
    )
    reader = lambda pid, bids: dict(block_map)  # noqa: E731
    run = extract_schema_instance_in_memory(
        "paper_777",
        "c2_test_schema",
        llm_client=llm,
        retrieval=retrieval,
        top_k=4,
        canonical_reader=reader,
    )
    result = run.instance.fields["headline"]
    assert result.value == "On Bus Control"
    assert result.evidence[0].block_id == "blk_h"
    # per-ref canonical provenance -> quote is the canonical substring
    assert result.evidence[0].quote == text[4:9]
    assert result.evidence[0].pages == [2]
    assert result.evidence[0].section_path == ["Method"]


# ---------------------------------------------------------------------------
# FR-001 / AC-T001-F01..F05: field completeness + placeholder fallback
# ---------------------------------------------------------------------------


def _mixed_definition() -> SchemaDefinition:
    return SchemaDefinition(
        schema_id="test_schema",
        version="1.0",
        sections=[
            SectionDefinition(
                id="s1",
                label="Section One",
                fields=[
                    _field("ok_string", "string"),
                    _field("object_field", "object"),
                    _field("list_field", "list"),
                ],
            ),
        ],
    )


def _all_hits_retrieval(definition) -> FakeRetrieval:
    responses = {}
    for section in definition.sections:
        for field in section.fields:
            responses[("paper_001", _query_for(definition, field.id))] = _ok_result(
                _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
            )
    return FakeRetrieval(responses=responses)


def test_field_coverage_invariant_with_mixed_success_failure():
    """AC-T001-F01/F02: every definition field id is always present after a
    run; failing fields become unclear placeholders with manifest traces."""
    definition = _mixed_definition()
    llm = FakeLLMProvider(
        responses={
            # success
            "ok_string": {"value": "fine", "status": "explicit", "evidence_ids": ["E1"]},
            # object field receives a natural-language string -> never saved
            "object_field": {
                "value": "the reward is a weighted sum of terms",
                "status": "explicit",
                "evidence_ids": ["E1"],
            },
            # list field receives a string -> never saved
            "list_field": {"value": "waiting time, headway", "status": "explicit"},
        }
    )
    engine = ExtractionEngine(
        llm_client=llm, retrieval=_all_hits_retrieval(definition), top_k=4
    )
    run = engine.run("paper_001", definition=definition)
    definition_ids = {
        field.id for section in definition.sections for field in section.fields
    }
    assert set(run.instance.fields) == definition_ids
    assert run.instance.fields["ok_string"].status == "explicit"
    assert run.instance.fields["ok_string"].value == "fine"
    for field_id in ("object_field", "list_field"):
        assert run.instance.fields[field_id].status == "unclear"
        assert run.instance.fields[field_id].value is None
        assert run.instance.fields[field_id].evidence == []
        assert "unclear" in (run.instance.fields[field_id].notes or "")
    manifest_by_id = {entry.field_id: entry for entry in run.manifest.fields}
    assert manifest_by_id["object_field"].error_code == "llm_invalid_output"
    assert "does not match field type" in manifest_by_id["object_field"].error_message
    assert manifest_by_id["object_field"].retry_count >= 1
    assert manifest_by_id["list_field"].error_code == "llm_invalid_output"
    assert manifest_by_id["list_field"].retry_count >= 1


# ---------------------------------------------------------------------------
# FR-002 / AC-T001-F06..F09: targeted retry + type enforcement
# ---------------------------------------------------------------------------


def test_targeted_retry_success_for_absent_status_with_evidence():
    """AC-T001-F06/F09: first attempt returns not_found with evidence -> one
    corrective retry returns a legal value; exactly 2 LLM calls."""
    definition = _definition()
    engine, llm = _build_engine(
        definition,
        llm_responses={
            "f1": [
                {"value": None, "status": "not_found", "evidence_ids": ["E1"]},
                {"value": None, "status": "not_found"},
            ]
        },
        retrieval=FakeRetrieval(
            responses={
                ("paper_001", _query_for(definition, "f1")): _ok_result(
                    _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
                )
            }
        ),
    )
    run = engine.run("paper_001", definition=definition)
    assert run.instance.fields["f1"].status == "not_found"
    assert run.instance.fields["f1"].value is None
    entry = run.manifest.fields[0]
    assert entry.error_code is None
    assert entry.retry_count == 1
    assert entry.retry_feedback is not None
    assert "must not carry" in entry.retry_feedback
    assert len(llm.calls) == 2


def test_targeted_retry_success_for_invalid_value_type():
    """AC-T001-F06/F07: object field gets a string -> one corrective retry
    returns a dict; exactly 2 LLM calls and the dict is persisted."""
    definition = _mixed_definition()
    object_query = _query_for(definition, "object_field")
    retrieval = FakeRetrieval(
        responses={
            ("paper_001", object_query): _ok_result(
                _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
            )
        }
    )
    llm = FakeLLMProvider(
        responses={
            "object_field": [
                {"value": "natural language paragraph", "status": "explicit", "evidence_ids": ["E1"]},
                {"value": {"w": 0.2, "term": "CV2"}, "status": "explicit", "evidence_ids": ["E1"]},
            ]
        }
    )
    engine = ExtractionEngine(llm_client=llm, retrieval=retrieval, top_k=4)
    run = engine.run("paper_001", definition=definition)
    result = run.instance.fields["object_field"]
    assert result.status == "explicit"
    assert isinstance(result.value, dict)
    assert result.value["w"] == 0.2
    assert result.evidence and result.evidence[0].block_id == "blk_1"
    entry = next(
        e for e in run.manifest.fields if e.field_id == "object_field"
    )
    assert entry.error_code is None
    assert entry.retry_count == 1
    assert len([c for c in llm.calls if c.prompt_key == "object_field"]) == 2


def test_object_field_string_value_retries_then_unclear_never_wrong_type():
    """Required test 3: an object field receiving a string never stores the
    string; after retry exhaustion it falls back to an unclear placeholder."""
    definition = _mixed_definition()
    llm = FakeLLMProvider(
        responses={
            "object_field": {
                "value": "natural language paragraph for reward",
                "status": "explicit",
                "evidence_ids": ["E1"],
            }
        }
    )
    engine = ExtractionEngine(
        llm_client=llm, retrieval=_all_hits_retrieval(definition), top_k=4
    )
    run = engine.run("paper_001", definition=definition)
    result = run.instance.fields["object_field"]
    assert result.status == "unclear"
    assert result.value is None
    entry = next(
        e for e in run.manifest.fields if e.field_id == "object_field"
    )
    assert entry.error_code == "llm_invalid_output"
    assert entry.retry_count == 1
    object_calls = [c for c in llm.calls if c.prompt_key == "object_field"]
    assert len(object_calls) == 2  # first attempt + one bounded retry


def test_list_field_string_value_retries_then_unclear_never_wrong_type():
    """Required test 4: a list field receiving a string retries then falls back
    rather than persisting the string."""
    definition = _mixed_definition()
    list_query = _query_for(definition, "list_field")
    retrieval = FakeRetrieval(
        responses={
            ("paper_001", list_query): _ok_result(
                _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
            )
        }
    )
    llm = FakeLLMProvider(
        responses={
            "list_field": {
                "value": "waiting time, headway regularity",
                "status": "explicit",
            }
        }
    )
    engine = ExtractionEngine(llm_client=llm, retrieval=retrieval, top_k=4)
    run = engine.run("paper_001", definition=definition)
    result = run.instance.fields["list_field"]
    assert result.status == "unclear"
    assert result.value is None
    entry = next(e for e in run.manifest.fields if e.field_id == "list_field")
    assert entry.error_code == "llm_invalid_output"
    assert entry.retry_count == 1


def test_retry_count_exactly_first_plus_one_for_retriable_failure():
    """AC-T001-F06: exactly 1 (first attempt) + 1 (max one retry) LLM calls for
    a retriable failure, no retry churn."""
    definition = _definition()
    engine, llm = _build_engine(
        definition,
        llm_responses={
            "f1": [
                {"value": "x", "status": "explicit", "evidence_ids": ["E9"]},
                {"value": "x", "status": "explicit", "evidence_ids": ["E1"]},
            ]
        },
        retrieval=FakeRetrieval(
            responses={
                ("paper_001", _query_for(definition, "f1")): _ok_result(
                    _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
                )
            }
        ),
    )
    run = engine.run("paper_001", definition=definition)
    assert run.instance.fields["f1"].status == "explicit"
    assert run.manifest.fields[0].retry_count == 1
    assert len(llm.calls) == 2
    assert all(c.prompt_key == "f1" for c in llm.calls)
    # the retry call carried corrective feedback in the prompt
    assert "unknown evidence id" in json.dumps(llm.calls[1].messages, ensure_ascii=False)


def test_retriable_failure_exhausted_keeps_placeholder_never_fabricated_not_found():
    """AC-T001-F09: after retry exhaustion the field is an unclear placeholder,
    never silently rewritten to a plain not_found."""
    definition = _definition()
    engine, llm = _build_engine(
        definition,
        llm_responses={
            "f1": [
                {"value": None, "status": "not_found", "evidence_ids": ["E1"]},
                {"value": None, "status": "not_found", "evidence_ids": ["E1"]},
            ]
        },
        retrieval=FakeRetrieval(
            responses={
                ("paper_001", _query_for(definition, "f1")): _ok_result(
                    _hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))
                )
            }
        ),
    )
    run = engine.run("paper_001", definition=definition)
    assert run.instance.fields["f1"].status == "unclear"
    assert run.manifest.fields[0].error_code == "llm_invalid_output"
    assert run.manifest.fields[0].retry_count == 1
    assert len(llm.calls) == 2


def test_enum_value_not_in_options_never_persisted():
    definition = SchemaDefinition(
        schema_id="test_schema",
        version="1.0",
        sections=[
            SectionDefinition(
                id="s1",
                label="S1",
                fields=[_field("choice", "enum", options=["a", "b"])],
            ),
        ],
    )
    llm = FakeLLMProvider(responses={"choice": {"value": "zzz", "status": "explicit"}})
    retr = _all_hits_retrieval(definition)
    engine = ExtractionEngine(llm_client=llm, retrieval=retr, top_k=4)
    run = engine.run("paper_001", definition=definition)
    assert run.instance.fields["choice"].status == "unclear"
    entry = run.manifest.fields[0]
    assert entry.error_code == "llm_invalid_output"
    assert "does not match field type 'enum'" in entry.error_message


# ---------------------------------------------------------------------------
# FR-002/FR-004 / AC-T001-F08/F14: prompt guidance + frozen status semantics
# ---------------------------------------------------------------------------


def test_prompt_contains_type_constraints_and_frozen_status_semantics():
    """AC-T001-F08/F14: the constructed prompt embeds per-type structural
    constraints and the frozen status 口径."""
    from transit_scholar.layer2.schema_extraction.engine import (
        FIELD_TYPE_GUIDANCE,
        STATUS_SEMANTICS,
        build_extraction_messages,
    )

    definition = _definition()
    section = definition.sections[0]
    field = section.fields[0]  # string
    candidates = map_hits_to_candidates(
        [_hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))]
    )
    messages = build_extraction_messages(
        field, section, definition, "some query", candidates
    )
    payload_text = json.dumps(messages, ensure_ascii=False)
    assert FIELD_TYPE_GUIDANCE["string"] in payload_text
    for meaning in STATUS_SEMANTICS.values():
        assert meaning in payload_text
    # object/list guidance asserted on an object field
    obj_field = _field("o", "object")
    obj_messages = build_extraction_messages(
        obj_field, section, definition, "q", candidates
    )
    obj_text = json.dumps(obj_messages, ensure_ascii=False)
    assert FIELD_TYPE_GUIDANCE["object"] in obj_text
    assert "natural-language paragraphs" in obj_text


def test_prompt_includes_output_guidance_skeleton():
    """AC-T001-F08: documented complex-object skeleton guidance reaches the
    prompt."""
    from transit_scholar.layer2.schema_extraction.engine import build_extraction_messages

    definition = _definition()
    section = definition.sections[0]
    obj_field = _field(
        "decision_model.reward",
        "object",
        output_guidance={
            "skeleton": {
                "type": "object",
                "suggested_keys": ["shaping_terms", "weights", "goal"],
            }
        },
    )
    candidates = map_hits_to_candidates(
        [_hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))]
    )
    messages = build_extraction_messages(
        obj_field, section, definition, "q", candidates
    )
    text = json.dumps(messages, ensure_ascii=False)
    assert "shaping_terms" in text
    assert "object_skeleton" in text


def test_prompt_includes_value_guidance_for_any_field_type():
    """AC-T001-F08: schema-level ``value_guidance`` reaches the prompt for
    non-object fields too (generic additive guidance, e.g. qualitative main
    results)."""
    from transit_scholar.layer2.schema_extraction.engine import build_extraction_messages

    definition = _definition()
    section = definition.sections[0]
    list_field = _field(
        "results.main_results",
        "list",
        output_guidance={
            "value_guidance": (
                "Qualitative comparative findings are valid main results; "
                "use not_found only when no finding exists at all."
            )
        },
    )
    candidates = map_hits_to_candidates(
        [_hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))]
    )
    messages = build_extraction_messages(
        list_field, section, definition, "q", candidates
    )
    text = json.dumps(messages, ensure_ascii=False)
    assert "value_guidance" in text
    assert "Qualitative comparative findings are valid main results" in text
    assert "use not_found only when no finding exists at all" in text


def test_bus_control_rl_schema_main_results_guidance_allows_qualitative_findings():
    """FR-002/F-08 (F-02 respected): the frozen bus_control_rl schema
    additively documents that qualitative comparative findings are valid main
    results; field id/question/type stay byte-identical."""
    from transit_scholar.layer2.schema_extraction.loader import get_schema_definition

    definition = get_schema_definition("bus_control_rl")
    fields = {f.id: f for s in definition.sections for f in s.fields}
    mr = fields["results.main_results"]
    assert mr.type == "list"
    assert mr.question == "What are the main quantitative results of the paper?"
    assert mr.evidence_required is True
    guidance = (mr.output_guidance or {}).get("value_guidance", "")
    assert "Qualitative comparative findings are valid main results" in guidance
    assert "not_found" in guidance


def test_system_prompt_clarifies_explicit_status_and_qualitative_findings():
    """AC-T001-F14 verbatim-intent: the system prompt clarifies that direct
    statements (including experimental settings / tables / captions) count as
    ``explicit`` and that qualitative findings are valid values."""
    from transit_scholar.layer2.schema_extraction.engine import build_extraction_messages

    definition = _definition()
    section = definition.sections[0]
    field = section.fields[0]
    candidates = map_hits_to_candidates(
        [_hit(rank=1, source_refs=_hit_refs(_ref("blk_1", 0, 5)))]
    )
    messages = build_extraction_messages(field, section, definition, "q", candidates)
    system = messages[0]["content"]
    assert "explicit" in system
    assert "experimental settings" in system
    assert "Qualitative" in system or "qualitative" in system
    assert "not_found" in system


def test_status_semantics_frozen_vocabulary_in_code():
    """AC-T001-F14(c): the code constant carries the full frozen status
    vocabulary used by the tests."""
    from transit_scholar.layer2.schema_extraction.engine import STATUS_SEMANTICS

    assert set(STATUS_SEMANTICS) == {
        "explicit",
        "inferred",
        "not_found",
        "not_applicable",
        "unclear",
        "conflicting",
    }


def test_provenance_fields_still_forbidden_in_llm_output():
    """AC-T001-F11 / AC-T001-V6: provenance stays LLM-immune via
    extra='forbid'."""
    from pydantic import ValidationError

    from transit_scholar.layer2.schema_extraction.engine import FieldExtractionLLMOutput

    with pytest.raises(ValidationError):
        FieldExtractionLLMOutput.model_validate(
            {
                "value": "x",
                "status": "explicit",
                "block_id": "blk_1",
                "char_start": 0,
                "char_end": 5,
            }
        )
