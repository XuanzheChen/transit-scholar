"""Deterministic coverage for the one-paper × one-field runtime smoke."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import scripts.l2s2_runtime_smoke as runtime_smoke
from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    FieldResult,
    SemanticVerdict,
    extract_field_instance_in_memory,
    get_schema_definition,
)


class _OneHitRetrieval:
    def __init__(self):
        self.calls = []

    def retrieve(self, paper_id, query, top_k):
        self.calls.append((paper_id, query, top_k))
        reference = SimpleNamespace(
            block_id="blk-1",
            char_start=0,
            char_end=17,
        )
        hit = SimpleNamespace(
            rank=1,
            retrieval_method="fake",
            score=1.0,
            chunk_id="chunk-1",
            paper_id=paper_id,
            source_refs=[reference],
            pages=[1],
            section_path=["Methods"],
            text="fixed-time control",
        )
        return SimpleNamespace(status="ok", method="fake", hits=[hit])


def test_one_field_engine_path_touches_only_selected_existing_field():
    definition = get_schema_definition("bus_control_rl")
    field_id = "research_problem.control_type"
    field = next(
        field
        for section in definition.sections
        for field in section.fields
        if field.id == field_id
    )
    value = field.options[0] if field.options else "fixed-time control"
    client = FakeLLMProvider(
        responses={
            field_id: {
                "value": value,
                "status": "explicit",
                "evidence_ids": ["E1"],
            }
        }
    )
    retrieval = _OneHitRetrieval()

    run = extract_field_instance_in_memory(
        "transit-001",
        "bus_control_rl",
        field_id,
        llm_client=client,
        retrieval=retrieval,
    )

    assert run.instance is not None
    assert set(run.instance.fields) == {field_id}
    assert [entry.field_id for entry in run.manifest.fields] == [field_id]
    assert len(retrieval.calls) == 1
    assert [call.prompt_key for call in client.calls] == [field_id]


def test_unknown_field_fails_before_retrieval_or_llm():
    client = FakeLLMProvider(default_response={"status": "not_found"})
    retrieval = _OneHitRetrieval()
    with pytest.raises(ValueError, match="not part of schema"):
        extract_field_instance_in_memory(
            "transit-001",
            "bus_control_rl",
            "missing.field",
            llm_client=client,
            retrieval=retrieval,
        )
    assert retrieval.calls == []
    assert client.calls == []


def test_cli_requires_exactly_one_field_argument():
    with pytest.raises(SystemExit):
        runtime_smoke._parse_args(["--paper", "transit-001"])
    with pytest.raises(SystemExit):
        runtime_smoke._parse_args(
            ["--paper", "transit-001", "--field", "f1", "f2"]
        )


def test_main_shares_client_and_reports_only_sanitized_success(
    monkeypatch, capsys
):
    import transit_scholar.config as config_module
    import transit_scholar.layer2.retrieval.api as retrieval_api
    import transit_scholar.layer2.schema_extraction as schema_extraction
    import transit_scholar.layer2.schema_extraction.retrieval as retrieval_module

    sentinel_key = "sk-runtime-smoke-secret"
    sentinel_url = "https://secret-provider.invalid/v1"
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_API_KEY", sentinel_key)
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_BASE_URL", sentinel_url)
    monkeypatch.setattr(config_module, "ensure_project_dotenv", lambda: None)

    field = SimpleNamespace(id="research_problem.control_type")
    definition = SimpleNamespace(sections=[SimpleNamespace(fields=[field])])
    class FakeRealClient:
        is_fake = False
        provider_name = "openai_compatible"
        model_name = "public-model"
        config = SimpleNamespace(allow_network=True)

    client = FakeRealClient()
    result = FieldResult(value="fixed-time", status="explicit")
    seen = {}

    monkeypatch.setattr(
        schema_extraction, "get_schema_definition", lambda schema_id: definition
    )
    monkeypatch.setattr(
        schema_extraction, "resolve_runtime_llm_client", lambda: client
    )
    monkeypatch.setattr(
        schema_extraction, "OpenAICompatibleLLMClient", FakeRealClient
    )
    monkeypatch.setattr(retrieval_module, "HybridRetrievalWrapper", lambda **kw: seen.setdefault("retrieval", object()))

    def fake_extract(paper_id, schema_id, field_id, **kwargs):
        seen["extract_client"] = kwargs["llm_client"]
        seen["canonical_reader"] = kwargs["canonical_reader"]
        seen["field_id"] = field_id
        return SimpleNamespace(
            instance=SimpleNamespace(fields={field_id: result}),
            manifest=SimpleNamespace(
                fields=[SimpleNamespace(error_code=None)]
            ),
        )

    class FakeStructuredVerifier:
        def __init__(self, injected_client):
            seen["verifier_client"] = injected_client

        def __call__(self, selected_field, selected_result):
            seen["verified_field"] = selected_field.id
            return SemanticVerdict(decision="supported", confidence=1.0)

    monkeypatch.setattr(
        schema_extraction, "extract_field_instance_in_memory", fake_extract
    )
    monkeypatch.setattr(
        schema_extraction, "StructuredSemanticVerifier", FakeStructuredVerifier
    )

    exit_code = runtime_smoke.main(
        [
            "--paper",
            "transit-001",
            "--field",
            "research_problem.control_type",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert seen["extract_client"] is client
    assert seen["canonical_reader"] is retrieval_api.read_blocks
    assert seen["verifier_client"] is client
    assert seen["field_id"] == "research_problem.control_type"
    assert seen["verified_field"] == "research_problem.control_type"
    assert "extraction_status=explicit" in captured.out
    assert "semantic_decision=supported" in captured.out
    assert "final_success=true" in captured.out
    assert sentinel_key not in captured.out + captured.err
    assert sentinel_url not in captured.out + captured.err


def test_main_rejects_fake_client_before_retrieval(monkeypatch, capsys):
    import transit_scholar.config as config_module
    import transit_scholar.layer2.schema_extraction as schema_extraction

    field = SimpleNamespace(id="research_problem.control_type")
    definition = SimpleNamespace(sections=[SimpleNamespace(fields=[field])])

    class ExpectedRealClient:
        pass

    fake_client = SimpleNamespace(is_fake=True)
    monkeypatch.setattr(config_module, "ensure_project_dotenv", lambda: None)
    monkeypatch.setattr(
        schema_extraction, "get_schema_definition", lambda schema_id: definition
    )
    monkeypatch.setattr(
        schema_extraction, "resolve_runtime_llm_client", lambda: fake_client
    )
    monkeypatch.setattr(
        schema_extraction, "OpenAICompatibleLLMClient", ExpectedRealClient
    )

    exit_code = runtime_smoke.main(
        [
            "--paper",
            "transit-001",
            "--field",
            "research_problem.control_type",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 3
    assert "real OpenAI-compatible client required" in captured.err
