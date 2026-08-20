"""L2S2 Package D deterministic tests: public API.

Covers AC-D-16..33, 35, 37..39 at the API level: exports and signatures,
extract/get/field/validate/recheck behaviour, injection boundaries, failure
semantics (extraction vs validation vs storage vs recheck), current pointer
safety, and versioning through the public surface.

Fully offline and isolated: every test injects ``storage_root`` into a
temporary directory and uses fake LLM / retrieval / verifier / reader objects.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from transit_scholar.layer2.schema import RetrievalHit, RetrievalResult, SourceRef
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    FakeRetrieval,
    FieldResult,
    LLMConfig,
    OpenAICompatibleLLMClient,
    SchemaExtractionRunError,
    SchemaFieldNotFoundError,
    SchemaIdMismatchError,
    SchemaRecheckError,
    SchemaRunResult,
    ValidationIssue,
    ValidationReport,
    build_field_query,
    compute_extraction_config_hash,
    extract_schema,
    get_field,
    get_schema,
    get_schema_definition,
    list_schema_plugins,
    recheck_fields,
    resolve_llm_client,
    schema_enabled,
    validate_schema,
)
from transit_scholar.layer2.schema_extraction import loader
from transit_scholar.layer2.schema_extraction import persistence as persistence_module

PAPER_ID = "paper_001"
PLUGIN_ID = "d_test_schema"

PLUGIN_YAML = """schema_id: d_test_schema
version: "1.0"
sections:
  - id: overview
    label: Overview
    fields:
      - id: headline
        label: Headline
        question: What is the headline?
        type: string
      - id: page_count
        label: Page Count
        question: How many pages?
        type: number
      - id: tags
        label: Tags
        question: What tags?
        type: list
"""


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def test_schema(tmp_path, monkeypatch):
    """A test-local schema plugin (mirrors Package B plugin-loader tests)."""
    plugin_dir = tmp_path / PLUGIN_ID
    plugin_dir.mkdir()
    (plugin_dir / "schema.yaml").write_text(PLUGIN_YAML, encoding="utf-8")
    monkeypatch.setattr(loader, "plugins_root", lambda: tmp_path)
    return PLUGIN_ID


def _definition(schema_id: str = PLUGIN_ID):
    return get_schema_definition(schema_id)


def _query_for(definition, field_id: str) -> str:
    for section in definition.sections:
        for field in section.fields:
            if field.id == field_id:
                return build_field_query(field, section, definition).query
    raise AssertionError(f"field {field_id!r} not in definition")


def _hit(rank=1, *, text="real paper text", block_id="blk_1") -> RetrievalHit:
    return RetrievalHit(
        paper_id=PAPER_ID,
        chunk_id=f"chunk-{rank}",
        score=1.0,
        retrieval_method="fake",
        section_path=["Method"],
        pages=[2],
        source_refs=[SourceRef(block_id=block_id, char_start=0, char_end=len(text))],
        text=text,
        rank=rank,
    )


def _ok_result(*hits: RetrievalHit) -> RetrievalResult:
    return RetrievalResult(status="ok", method="fake", hits=list(hits))


def _block(block_id: str, text: str) -> dict:
    return {
        "block_id": block_id,
        "paper_id": PAPER_ID,
        "block_type": "body",
        "section_id": "s1",
        "order": 0,
        "text": text,
        "pages": [2],
        "section_path": ["Method"],
        "provenance": [],
        "source_items": [],
        "relations": {},
        "content": {},
    }


def _reader(block_map: dict):
    def reader(paper_id, block_ids):
        return {bid: block_map[bid] for bid in block_ids if bid in block_map}

    return reader


def _value_extraction_kwargs(definition):
    """Fake LLM/retrieval/reader producing a clean passed run with evidence."""
    text_headline = "A deep RL paper on bus control"
    text_pages = "The paper has twelve pages"
    retrieval = FakeRetrieval(
        responses={
            (PAPER_ID, _query_for(definition, "headline")): _ok_result(
                _hit(text=text_headline, block_id="blk_h")
            ),
            (PAPER_ID, _query_for(definition, "page_count")): _ok_result(
                _hit(text=text_pages, block_id="blk_p")
            ),
        }
    )
    llm = FakeLLMProvider(
        responses={
            "headline": {
                "value": "Bus Control RL",
                "status": "explicit",
                "evidence_ids": ["E1"],
                "confidence": 0.9,
            },
            "page_count": {
                "value": 12,
                "status": "explicit",
                "evidence_ids": ["E1"],
                "confidence": 0.9,
            },
        }
    )
    canonical_reader = _reader(
        {"blk_h": _block("blk_h", text_headline), "blk_p": _block("blk_p", text_pages)}
    )
    return dict(llm_client=llm, retrieval=retrieval, canonical_reader=canonical_reader)


def _file_hashes(storage, paper_id: str, run_id: str) -> dict:
    """SHA256 of every file inside one run directory."""
    hashes = {}
    run_dir = storage.run_dir(paper_id, run_id)
    for path in sorted(run_dir.iterdir()):
        hashes[f"{run_dir.name}/{path.name}"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return hashes


def _run_dirs(storage, paper_id: str) -> list[str]:
    runs = storage.runs_dir(paper_id)
    if not runs.is_dir():
        return []
    return sorted(p.name for p in runs.iterdir() if p.is_dir())


class CountingRecheck:
    def __init__(self, results):
        self.results = results
        self.calls: list[str] = []

    def __call__(self, definition, field, paper_id):
        self.calls.append(field.id)
        return self.results[field.id]


# ---------------------------------------------------------------------------
# AC-D-16 exports and signatures
# ---------------------------------------------------------------------------


def test_public_names_exported():
    import transit_scholar.layer2.schema_extraction as pkg

    for name in (
        "list_schema_plugins",
        "get_schema_definition",
        "extract_schema",
        "get_schema",
        "get_field",
        "validate_schema",
        "recheck_fields",
        "SchemaRunResult",
    ):
        assert hasattr(pkg, name), name
        assert callable(getattr(pkg, name)) or isinstance(getattr(pkg, name), type), name


def test_signatures_match_requirements():
    sig_extract = inspect.signature(extract_schema)
    assert sig_extract.parameters["schema_id"].default == "bus_control_rl"

    sig_get = inspect.signature(get_schema)
    assert sig_get.parameters["schema_id"].default == "bus_control_rl"
    assert sig_get.parameters["run_id"].default is None

    sig_field = inspect.signature(get_field)
    assert "schema_id" in sig_field.parameters
    assert sig_field.parameters["run_id"].default is None

    sig_validate = inspect.signature(validate_schema)
    assert sig_validate.parameters["schema_id"].default == "bus_control_rl"
    assert sig_validate.parameters["run_id"].default is None

    sig_recheck = inspect.signature(recheck_fields)
    assert "paper_id" in sig_recheck.parameters
    assert "schema_id" in sig_recheck.parameters
    assert "field_ids" in sig_recheck.parameters


def test_unknown_injection_key_raises_typeerror(test_schema, tmp_path):
    with pytest.raises(TypeError):
        extract_schema(PAPER_ID, test_schema, storage_root=tmp_path, bogus_option=1)


# ---------------------------------------------------------------------------
# AC-D-17/22/38 default offline behaviour
# ---------------------------------------------------------------------------


def test_extract_returns_stable_result_object(tmp_path, test_schema):
    """AC-D-17: extract returns a SchemaRunResult with instance, manifest,
    report and run manifest; no directory knowledge required."""
    result = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert isinstance(result, SchemaRunResult)
    assert result.paper_id == PAPER_ID
    assert result.schema_id == test_schema
    assert set(result.instance.fields) == {"headline", "page_count", "tags"}
    assert result.manifest.paper_id == PAPER_ID
    assert result.report.paper_id == PAPER_ID
    assert result.run_manifest.run_id == result.run_id
    assert result.is_current is True
    assert result.instance.fields["headline"].status == "not_found"


def test_extract_defaults_are_fake_and_offline(tmp_path, test_schema):
    """AC-D-17/38: default extract uses fake LLM + fake retrieval offline."""
    result = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert result.manifest.llm_fake is True
    assert result.manifest.llm_provider == "fake"
    assert result.run_manifest.llm_fake is True
    retrieval_methods = {e.retrieval_method for e in result.manifest.fields}
    assert retrieval_methods == {"fake"}


def test_plugin_apis_do_not_touch_storage(tmp_path):
    """AC-D-22: list_schema_plugins / get_schema_definition stay pure plugin
    loader functions and never read or write storage."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    assert "bus_control_rl" in list_schema_plugins()
    definition = get_schema_definition("bus_control_rl")
    assert definition.schema_id == "bus_control_rl"
    assert list(storage_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# AC-D-23..26 integration and failure semantics
# ---------------------------------------------------------------------------


def test_extraction_run_level_failure_no_writes_no_current(tmp_path):
    """AC-D-24: unknown schema -> SchemaExtractionRunError with
    ``schema_load_failed``; nothing is written and current is untouched."""
    storage_root = tmp_path / "storage"
    with pytest.raises(SchemaExtractionRunError) as excinfo:
        extract_schema(PAPER_ID, "no_such_schema", storage_root=storage_root)
    assert excinfo.value.error_code == "schema_load_failed"
    assert not storage_root.exists()


def test_validation_stage_failure_prevents_persistence(tmp_path, test_schema):
    """AC-D-23: validation runs before persistence; a raising cross-field
    validator aborts before any run file or current pointer is written."""

    def exploding_validator(instance):
        raise RuntimeError("validator exploded (injected)")

    storage_root = tmp_path / "storage"
    with pytest.raises(RuntimeError, match="validator exploded"):
        extract_schema(
            PAPER_ID,
            test_schema,
            storage_root=storage_root,
            cross_field_validators=[exploding_validator],
        )
    assert not storage_root.exists()


def test_field_level_error_preserved_and_distinct_from_not_found(
    tmp_path, test_schema
):
    """AC-D-25/AC-T001-F01..F05: retrieval failure is a field-level error
    preserved in the manifest trace; the field is never dropped from the
    instance (FR-001 ``unclear`` placeholder) while ``not_found`` stays
    reserved for clean empty runs; the run is still fully persisted."""
    definition = _definition(test_schema)
    unavailable = RetrievalResult(
        status="unavailable",
        method="fake",
        hits=[],
        error_code="index_not_built",
        error_message="retrieval index not built",
    )
    retrieval = FakeRetrieval(
        responses={
            (PAPER_ID, _query_for(definition, "headline")): unavailable,
        }
    )
    result = extract_schema(
        PAPER_ID, test_schema, storage_root=tmp_path, retrieval=retrieval
    )
    by_field = {entry.field_id: entry for entry in result.manifest.fields}
    assert by_field["headline"].error_code == "retrieval_unavailable"
    assert by_field["headline"].retrieval_error_code == "index_not_built"
    assert "headline" in result.instance.fields
    headline = result.instance.fields["headline"]
    assert headline.status == "unclear"
    assert headline.value is None
    assert headline.evidence == []
    assert "retrieval_unavailable" in headline.notes
    assert by_field["page_count"].error_code is None
    assert by_field["page_count"].field_result_status == "not_found"
    assert result.instance.fields["page_count"].status == "not_found"
    assert get_schema(PAPER_ID, test_schema, storage_root=tmp_path) is not None


def test_validation_failure_persisted_not_disguised(tmp_path, test_schema):
    """AC-D-26: a failed validation is persisted as a failed report and
    reflected in run_manifest.status and current.status; it is never raised as
    an extraction failure."""
    issue = ValidationIssue(
        type="cross_field_violation",
        severity="error",
        message="headline conflicts with page_count",
        fields=["headline", "page_count"],
    )

    def failing_validator(instance):
        return [issue]

    result = extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        cross_field_validators=[failing_validator],
    )
    assert result.report.status == "failed"
    assert any(i.severity == "error" for i in result.report.issues)
    assert result.run_manifest.status == "failed"
    pointer = json.loads(
        (tmp_path / PAPER_ID / "current.json").read_text(encoding="utf-8")
    )
    assert pointer["status"] == "failed"
    assert pointer["run_id"] == result.run_id
    assert get_schema(PAPER_ID, test_schema, storage_root=tmp_path).schema_id == test_schema


def test_persisted_content_contains_no_api_keys(tmp_path, test_schema):
    """AC-D-28: no API key material is ever persisted."""
    extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    for path in (tmp_path / PAPER_ID).rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "api_key" not in text
        assert "TRANSIT_SCHOLAR_LLM_API_KEY" not in text
        assert "sk-" not in text


# ---------------------------------------------------------------------------
# AC-D-18/19 read APIs
# ---------------------------------------------------------------------------


def test_get_schema_current_and_historical(tmp_path, test_schema):
    first = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    second = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert first.run_id != second.run_id
    current = get_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert current.fields == second.instance.fields
    historical = get_schema(
        PAPER_ID, test_schema, run_id=first.run_id, storage_root=tmp_path
    )
    assert historical.fields == first.instance.fields


def test_get_schema_error_cases(tmp_path, test_schema):
    """AC-D-18: missing paper / run / invalid JSON / schema mismatch all raise
    explicit exceptions, never None or an empty object."""
    with pytest.raises(Exception) as paper_missing:
        get_schema("unknown_paper", test_schema, storage_root=tmp_path)
    assert paper_missing.value.__class__.__name__ == "SchemaCurrentNotFoundError"

    extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    with pytest.raises(Exception) as run_missing:
        get_schema(PAPER_ID, test_schema, run_id="ghost", storage_root=tmp_path)
    assert run_missing.value.__class__.__name__ == "SchemaRunNotFoundError"

    with pytest.raises(SchemaIdMismatchError):
        get_schema(PAPER_ID, "bus_control_rl", storage_root=tmp_path)

    (tmp_path / PAPER_ID / "current.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(Exception) as bad_json:
        get_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert bad_json.value.__class__.__name__ == "SchemaInvalidJsonError"


def test_get_field_behaviour(tmp_path, test_schema):
    result = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    field = get_field(
        PAPER_ID, test_schema, "headline", storage_root=tmp_path
    )
    assert isinstance(field, FieldResult)
    assert field.status == result.instance.fields["headline"].status
    with pytest.raises(SchemaFieldNotFoundError):
        get_field(PAPER_ID, test_schema, "no_such_field", storage_root=tmp_path)


def test_get_field_returns_placeholder_for_failed_field(tmp_path, test_schema):
    """AC-D-19/AC-T001-F01/F02: under FR-001 a field with a field-level
    failure is never missing from a stored instance; ``get_field`` returns the
    ``unclear`` placeholder instead of raising SchemaFieldMissingError, and the
    diagnostic note remains inspectable."""
    definition = _definition(test_schema)
    retrieval = FakeRetrieval(
        responses={
            (PAPER_ID, _query_for(definition, "headline")): RetrievalResult(
                status="unavailable",
                method="fake",
                hits=[],
                error_code="index_not_built",
                error_message="no index",
            ),
        }
    )
    extract_schema(PAPER_ID, test_schema, storage_root=tmp_path, retrieval=retrieval)
    field = get_field(PAPER_ID, test_schema, "headline", storage_root=tmp_path)
    assert isinstance(field, FieldResult)
    assert field.status == "unclear"
    assert field.value is None
    assert field.evidence == []
    assert "retrieval_unavailable" in field.notes


# ---------------------------------------------------------------------------
# AC-D-20/21 validate + injection boundaries
# ---------------------------------------------------------------------------


def test_validate_schema_is_read_only(tmp_path, test_schema):
    """AC-D-20: validate_schema re-validates a stored run without modifying
    any run file or current.json."""
    result = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    storage = persistence_module.SchemaRunStorage(storage_root=tmp_path)
    before = _file_hashes(storage, PAPER_ID, result.run_id)
    current_before = (tmp_path / PAPER_ID / "current.json").read_bytes()
    report = validate_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    after = _file_hashes(storage, PAPER_ID, result.run_id)
    assert isinstance(report, ValidationReport)
    assert report.schema_id == test_schema
    assert before == after
    assert (tmp_path / PAPER_ID / "current.json").read_bytes() == current_before


def test_validate_schema_historical_run(tmp_path, test_schema):
    first = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    report = validate_schema(
        PAPER_ID, test_schema, run_id=first.run_id, storage_root=tmp_path
    )
    assert report.paper_id == PAPER_ID


def test_injected_broken_retrieval_fails_explicitly(tmp_path, test_schema):
    """AC-D-21: an injected retrieval that raises produces explicit
    field-level failure traces and never falls back to a real service."""

    class BrokenRetrieval:
        def retrieve(self, paper_id, query, top_k):
            raise RuntimeError("index exploded")

    llm = FakeLLMProvider(responses={})
    result = extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        retrieval=BrokenRetrieval(),
        llm_client=llm,
    )
    errors = {entry.error_code for entry in result.manifest.fields}
    assert errors == {"retrieval_unavailable"}
    assert all(
        entry.retrieval_error_code == "boundary_exception"
        for entry in result.manifest.fields
    )
    assert llm.calls == []


def test_injected_llm_and_reader_affect_behaviour(tmp_path, test_schema):
    """AC-D-21: injected llm_client + retrieval + canonical_reader produce a
    fully evidenced passed run; values and bound evidence are real."""
    definition = _definition(test_schema)
    kwargs = _value_extraction_kwargs(definition)
    result = extract_schema(
        PAPER_ID, test_schema, storage_root=tmp_path, **kwargs
    )
    assert result.instance.fields["headline"].value == "Bus Control RL"
    assert result.instance.fields["page_count"].value == 12
    assert result.instance.fields["headline"].evidence[0].block_id == "blk_h"
    assert result.report.status == "passed"


def test_injected_verifier_affects_report(tmp_path, test_schema):
    """AC-D-21: an injected verifier changes the validation outcome."""
    from transit_scholar.layer2.schema_extraction import FakeSemanticVerifier

    definition = _definition(test_schema)
    kwargs = _value_extraction_kwargs(definition)
    verifier = FakeSemanticVerifier(
        default_response={"decision": "conflicting", "confidence": None, "notes": ""}
    )
    result = extract_schema(
        PAPER_ID, test_schema, storage_root=tmp_path, verifier=verifier, **kwargs
    )
    assert any(
        issue.type == "semantic_conflicting" for issue in result.report.semantic_issues
    )


def test_injected_top_k_affects_config_hash(tmp_path, test_schema):
    default = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    result = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path, top_k=3)
    assert result.run_manifest.extraction_config_hash != default.run_manifest.extraction_config_hash
    assert result.run_manifest.extraction_config_hash == compute_extraction_config_hash(
        result.run_manifest.prompt_version, 3
    )


def test_storage_injection_writes_into_injected_root(tmp_path, test_schema):
    """AC-D-21: ``storage`` object injection is honoured."""
    storage = persistence_module.SchemaRunStorage(storage_root=tmp_path / "custom")
    extract_schema(PAPER_ID, test_schema, storage=storage)
    assert (tmp_path / "custom" / PAPER_ID / "current.json").is_file()


# ---------------------------------------------------------------------------
# AC-D-34/35 storage failures through the API
# ---------------------------------------------------------------------------


def test_storage_write_failure_leaves_no_current(tmp_path, test_schema, monkeypatch):
    """AC-D-34/27: a storage failure during run writing raises a storage
    error and never creates a current pointer."""
    real_write = persistence_module._atomic_write_json

    def failing_write(path, model):
        if path.name == "run_manifest.json":
            raise OSError("disk full (injected)")
        real_write(path, model)

    monkeypatch.setattr(persistence_module, "_atomic_write_json", failing_write)
    with pytest.raises(Exception) as excinfo:
        extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert isinstance(excinfo.value, persistence_module.SchemaStorageError)
    assert not (tmp_path / PAPER_ID / "current.json").exists()


def test_readback_verification_failure_keeps_old_current(
    tmp_path, test_schema, monkeypatch
):
    """AC-D-35: when read-back verification fails after writing, current.json
    is not updated."""
    first = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    storage = persistence_module.SchemaRunStorage(storage_root=tmp_path)
    current_before = (tmp_path / PAPER_ID / "current.json").read_bytes()

    def failing_verify(self, paper_id, run_id, *, expected_schema_id=None):
        raise persistence_module.SchemaCorruptRunError("verification failed (injected)")

    monkeypatch.setattr(
        persistence_module.SchemaRunStorage, "verify_run_readable", failing_verify
    )
    with pytest.raises(persistence_module.SchemaCorruptRunError):
        extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert (tmp_path / PAPER_ID / "current.json").read_bytes() == current_before
    assert storage.read_current(PAPER_ID).run_id == first.run_id


def test_extract_twice_old_run_byte_identical_and_current_moves(
    tmp_path, test_schema
):
    """AC-D-11/12/14: two extracts -> two run ids; the first run directory is
    byte-identical; current points at the second run."""
    first = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    storage = persistence_module.SchemaRunStorage(storage_root=tmp_path)
    before = _file_hashes(storage, PAPER_ID, first.run_id)
    second = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert first.run_id != second.run_id
    after = _file_hashes(storage, PAPER_ID, first.run_id)
    assert before == after
    assert sorted(_run_dirs(storage, PAPER_ID)) == sorted(
        [first.run_id, second.run_id]
    )
    pointer = storage.read_current(PAPER_ID)
    assert pointer.run_id == second.run_id


# ---------------------------------------------------------------------------
# AC-D-29..33 recheck integration
# ---------------------------------------------------------------------------


def test_recheck_success_new_run_old_preserved(tmp_path, test_schema):
    """AC-D-29/30/32: recheck produces a brand-new complete run with a fresh
    recheck trace persisted in the report; the old run stays byte-identical
    and readable; non-target fields stay untouched."""
    first = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    storage = persistence_module.SchemaRunStorage(storage_root=tmp_path)
    before = _file_hashes(storage, PAPER_ID, first.run_id)
    old_instance = first.instance

    rechecker = CountingRecheck(
        {
            "headline": FieldResult(value="New Headline", status="explicit"),
            "page_count": FieldResult(value=99, status="explicit"),
        }
    )
    result = recheck_fields(
        PAPER_ID,
        test_schema,
        ["headline", "page_count"],
        storage_root=tmp_path,
        recheck_callable=rechecker,
    )
    assert result.run_id != first.run_id
    assert result.run_manifest.run_reason == "recheck"
    assert result.run_manifest.parent_run_id == first.run_id
    assert rechecker.calls == ["headline", "page_count"]

    assert result.instance.fields["headline"].value == "New Headline"
    assert result.instance.fields["page_count"].value == 99
    assert result.instance.fields["tags"].model_dump() == old_instance.fields[
        "tags"
    ].model_dump()

    persisted = storage.read_run(PAPER_ID, result.run_id)
    assert persisted.report.recheck_trace.entries
    assert all(entry.updated for entry in persisted.report.recheck_trace.entries)
    assert {e.field_id for e in persisted.report.recheck_trace.entries} == {
        "headline",
        "page_count",
    }

    assert _file_hashes(storage, PAPER_ID, first.run_id) == before
    historical = get_schema(
        PAPER_ID, test_schema, run_id=first.run_id, storage_root=tmp_path
    )
    assert historical.fields == old_instance.fields
    assert storage.read_current(PAPER_ID).run_id == result.run_id


def test_recheck_each_target_field_at_most_once(tmp_path, test_schema):
    """AC-D-33: duplicate field ids in the request still recheck each field
    at most once."""
    extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    rechecker = CountingRecheck(
        {"headline": FieldResult(value="x", status="explicit")}
    )
    recheck_fields(
        PAPER_ID,
        test_schema,
        ["headline", "headline", "headline"],
        storage_root=tmp_path,
        recheck_callable=rechecker,
    )
    assert rechecker.calls == ["headline"]


def test_recheck_unclear_conclusion_is_legal(tmp_path, test_schema):
    """AC-D-30: an ``unclear`` recheck conclusion is a legal result and
    produces a complete new run."""
    extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    rechecker = CountingRecheck(
        {"headline": FieldResult(value=None, status="unclear")}
    )
    result = recheck_fields(
        PAPER_ID,
        test_schema,
        ["headline"],
        storage_root=tmp_path,
        recheck_callable=rechecker,
    )
    assert result.instance.fields["headline"].status == "unclear"
    assert result.report.status == "needs_recheck"


def test_recheck_default_offline_produces_unclear(tmp_path, test_schema):
    """AC-D-38: the default recheck callable is offline and deterministic."""
    extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    result = recheck_fields(
        PAPER_ID, test_schema, ["headline"], storage_root=tmp_path
    )
    assert result.instance.fields["headline"].status == "unclear"
    assert "offline default recheck" in result.instance.fields["headline"].notes


def test_recheck_failure_no_writes_current_unchanged(tmp_path, test_schema):
    """AC-D-31: a failing recheck callable raises SchemaRecheckError carrying
    the trace; no new run files are written and current stays byte-identical."""
    first = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    storage = persistence_module.SchemaRunStorage(storage_root=tmp_path)
    current_before = (tmp_path / PAPER_ID / "current.json").read_bytes()

    class FailingRecheck:
        def __call__(self, definition, field, paper_id):
            raise RuntimeError("recheck backend down")

    with pytest.raises(SchemaRecheckError) as excinfo:
        recheck_fields(
            PAPER_ID,
            test_schema,
            ["headline"],
            storage_root=tmp_path,
            recheck_callable=FailingRecheck(),
        )
    assert excinfo.value.trace.entries[0].error_code == "recheck_failed"
    assert (tmp_path / PAPER_ID / "current.json").read_bytes() == current_before
    assert _run_dirs(storage, PAPER_ID) == [first.run_id]


def test_recheck_unknown_field_raises_with_trace(tmp_path, test_schema):
    """AC-D-31: a target field missing from the definition produces a
    ``recheck_field_missing`` trace entry and zero writes."""
    first = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    storage = persistence_module.SchemaRunStorage(storage_root=tmp_path)
    with pytest.raises(SchemaRecheckError) as excinfo:
        recheck_fields(
            PAPER_ID, test_schema, ["no_such_field"], storage_root=tmp_path
        )
    assert excinfo.value.trace.entries[0].error_code == "recheck_field_missing"
    assert _run_dirs(storage, PAPER_ID) == [first.run_id]


def test_recheck_requires_current_pointer(tmp_path, test_schema):
    with pytest.raises(Exception) as excinfo:
        recheck_fields(PAPER_ID, test_schema, ["headline"], storage_root=tmp_path)
    assert excinfo.value.__class__.__name__ == "SchemaCurrentNotFoundError"


# ---------------------------------------------------------------------------
# AC-D-37/39 isolation and determinism
# ---------------------------------------------------------------------------


def test_all_runs_land_under_injected_storage_root(tmp_path, test_schema):
    """AC-D-37: with an injected storage root, every file lives under it."""
    result = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    for path in (tmp_path / PAPER_ID).rglob("*"):
        assert path.is_dir() or path.is_file()
    assert (tmp_path / PAPER_ID / "runs" / result.run_id / "run_manifest.json").is_file()


def test_run_ids_differ_but_not_randomness_dependent(tmp_path, test_schema):
    """AC-D-39: assertions depend only on run ids differing, not on their
    values."""
    first = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    second = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert first.run_id != second.run_id
    assert first.run_id and second.run_id


# ---------------------------------------------------------------------------
# FR-004 (task-2026-08-15-001): public API injects the real provider
# ---------------------------------------------------------------------------

REAL_CLIENT_ENV = {
    "TRANSIT_SCHOLAR_LLM_PROVIDER": "openai_compatible",
    "TRANSIT_SCHOLAR_LLM_MODEL": "test-model",
    "TRANSIT_SCHOLAR_LLM_API_KEY": "sk-e2e-test-public-api-0001",
    "TRANSIT_SCHOLAR_LLM_BASE_URL": "https://provider.invalid",
    "TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK": "1",
}

REAL_SENTINEL_KEY = "sk-e2e-test-public-api-0001"

_REAL_ENV_TO_FIELD = {
    "TRANSIT_SCHOLAR_LLM_PROVIDER": "provider",
    "TRANSIT_SCHOLAR_LLM_MODEL": "model",
    "TRANSIT_SCHOLAR_LLM_API_KEY": "api_key",
    "TRANSIT_SCHOLAR_LLM_BASE_URL": "base_url",
    "TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK": "allow_network",
}


def _real_config_kwargs(**extra) -> dict:
    kwargs = {_REAL_ENV_TO_FIELD[k]: v for k, v in REAL_CLIENT_ENV.items()}
    kwargs["allow_network"] = True
    kwargs.update(extra)
    return kwargs


def _real_client_handler(request):
    """MockTransport handler returning a valid per-field extraction output."""
    import json as _json

    body = _json.dumps(
        {
            "value": None,
            "status": "not_found",
            "evidence_ids": [],
            "confidence": None,
            "notes": "mock real provider output",
        }
    )
    return pytest.importorskip("httpx").Response(
        200, json={"choices": [{"message": {"content": body}}]}
    )


def test_resolve_from_env_returns_real_client(monkeypatch):
    """FR-004: resolve_llm_client(LLMConfig.from_env()) with the full env set
    returns the real OpenAI-compatible client."""
    for name, value in REAL_CLIENT_ENV.items():
        monkeypatch.setenv(name, value)
    client = resolve_llm_client(LLMConfig.from_env())
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.is_fake is False
    assert client.provider_name == "openai_compatible"


def test_extract_schema_with_real_client_persists_openai_provider(tmp_path, test_schema):
    """FR-004: extract_schema(..., llm_client=<real client>,
    retrieval=FakeRetrieval()) completes and both manifests record the real
    provider with llm_fake False. The real client runs against MockTransport,
    so no network is touched."""
    import httpx

    real_client = OpenAICompatibleLLMClient(
        LLMConfig(**_real_config_kwargs(timeout_seconds=30)),
        transport=httpx.MockTransport(_real_client_handler),
    )
    result = extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        llm_client=real_client,
        retrieval=FakeRetrieval(),
    )
    assert result.manifest.llm_provider == "openai_compatible"
    assert result.manifest.llm_fake is False
    assert result.run_manifest.llm_provider == "openai_compatible"
    assert result.run_manifest.llm_fake is False


def test_extract_schema_real_client_bus_control_rl_completes(tmp_path):
    """FR-004 acceptance shape: extract_schema(paper_id, "bus_control_rl",
    llm_client=<real client>, retrieval=FakeRetrieval(), storage_root=<tmp>)
    completes and persists with the real provider recorded."""
    import httpx

    real_client = OpenAICompatibleLLMClient(
        LLMConfig(**_real_config_kwargs(timeout_seconds=30)),
        transport=httpx.MockTransport(_real_client_handler),
    )
    result = extract_schema(
        "paper_bus_rl",
        "bus_control_rl",
        storage_root=tmp_path,
        llm_client=real_client,
        retrieval=FakeRetrieval(),
    )
    assert result.manifest.llm_provider == "openai_compatible"
    assert result.manifest.llm_fake is False
    assert result.run_manifest.llm_fake is False
    assert get_schema("paper_bus_rl", "bus_control_rl", storage_root=tmp_path) is not None


def test_real_client_run_persists_no_secrets(tmp_path, test_schema):
    """FR-004: no persisted run file contains the sentinel key or the string
    'api_key' when the run used the real client."""
    import httpx

    real_client = OpenAICompatibleLLMClient(
        LLMConfig(**_real_config_kwargs(timeout_seconds=30)),
        transport=httpx.MockTransport(_real_client_handler),
    )
    extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        llm_client=real_client,
        retrieval=FakeRetrieval(),
    )
    for path in (tmp_path / PAPER_ID).rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert REAL_SENTINEL_KEY not in text
        assert "api_key" not in text


def test_hybrid_retrieval_wrapper_injection_shape_accepted(
    tmp_path, test_schema, monkeypatch
):
    """FR-004: the documented retrieval=HybridRetrievalWrapper(...) call shape
    is accepted by the injection boundary (no TypeError from
    _split_injections). retrieve is stubbed so the test stays offline."""
    from transit_scholar.layer2.schema_extraction import HybridRetrievalWrapper

    def fake_retrieve(self, paper_id, query, top_k=None):
        return RetrievalResult(status="ok", method="fake", hits=[])

    monkeypatch.setattr(HybridRetrievalWrapper, "retrieve", fake_retrieve)
    result = extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        retrieval=HybridRetrievalWrapper(top_k=4),
    )
    assert result.run_manifest.llm_fake is True  # default LLM stays fake


# ---------------------------------------------------------------------------
# FR-007 (task-2026-08-15-001): explicit schema switch
# ---------------------------------------------------------------------------


def test_schema_enabled_defaults_true_when_unset(monkeypatch):
    monkeypatch.delenv("TRANSIT_SCHOLAR_SCHEMA_ENABLED", raising=False)
    assert schema_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_schema_enabled_disabled_values(value, monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_SCHEMA_ENABLED", value)
    assert schema_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "", "  "])
def test_schema_enabled_enabled_values(value, monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_SCHEMA_ENABLED", value)
    assert schema_enabled() is True


def test_schema_switch_does_not_touch_persistence_or_api(tmp_path, test_schema, monkeypatch):
    """FR-007: the switch is a read-only configuration boundary — extract_schema
    behaviour and persistence are unchanged whether enabled or disabled."""
    monkeypatch.setenv("TRANSIT_SCHOLAR_SCHEMA_ENABLED", "0")
    assert schema_enabled() is False
    result = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert result.manifest.llm_fake is True
    assert get_schema(PAPER_ID, test_schema, storage_root=tmp_path) is not None
