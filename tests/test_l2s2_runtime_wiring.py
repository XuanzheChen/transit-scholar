"""L2S2 runtime LLM wiring deterministic tests (task-2026-08-20-001).

Covers the frozen acceptance criteria AC-RW-01..17 for the real L2S2 runtime:

- the single dotenv bootstrap boundary (``ensure_project_dotenv``) feeds
  ``LLMConfig.from_env()`` (AC-RW-01/02/03);
- the frozen resolver truth table: explicit fake only, unconfigured fails,
  complete real config resolves ``OpenAICompatibleLLMClient`` offline
  (AC-RW-05);
- the default ``extract_schema`` path resolves the runtime client, and
  injection precedence beats any real ``.env`` (AC-RW-04/06);
- failure semantics: unconfigured / network-blocked runtimes raise an
  explicit ``LLMUnavailableError`` and write nothing (AC-RW-08/10);
- the real ``StructuredSemanticVerifier`` sends one structured call carrying
  field/question/value/status + the complete Evidence Set and maps verdicts to
  the fixed issues; ``EvidenceRef`` objects stay byte-identical (AC-RW-11/12/13);
- the default Targeted Recheck reuses the very same client object, at most
  once per field (AC-RW-14);
- network-block (AC-RW-16) and API-key / Authorization-header / URL redaction
  (AC-RW-17);
- explicit fake never networks even under a full real ``.env`` (FR-005).

Everything is offline/deterministic: no real HTTP, no real key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transit_scholar.layer2.schema import RetrievalHit, RetrievalResult, SourceRef
from transit_scholar.layer2.schema_extraction import (
    EvidenceRef,
    FakeLLMProvider,
    FakeRetrieval,
    FakeSemanticVerifier,
    FieldDefinition,
    FieldResult,
    LLMConfig,
    LLMInvalidOutputError,
    LLMUnavailableError,
    OpenAICompatibleLLMClient,
    RealLLMClientStub,
    StructuredSemanticVerifier,
    build_semantic_verifier_messages,
    extract_schema,
    recheck_fields,
    resolve_llm_client,
    resolve_runtime_llm_client,
    verify_field_semantics,
)
from transit_scholar.layer2.schema_extraction import loader as loader_module
from transit_scholar.layer2.schema_extraction.engine import FieldExtractionLLMOutput

PAPER_ID = "runtime_paper_001"
PLUGIN_ID = "rt_test_schema"

PLUGIN_YAML = """schema_id: rt_test_schema
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
"""

LLM_ENV_VARS = (
    "TRANSIT_SCHOLAR_LLM_PROVIDER",
    "TRANSIT_SCHOLAR_LLM_MODEL",
    "TRANSIT_SCHOLAR_LLM_API_KEY",
    "TRANSIT_SCHOLAR_LLM_BASE_URL",
    "TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK",
    "TRANSIT_SCHOLAR_BLOCK_NETWORK",
)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def test_schema(tmp_path, monkeypatch):
    """A test-local schema plugin (mirrors the Package D plugin-loader tests)."""
    plugin_dir = tmp_path / PLUGIN_ID
    plugin_dir.mkdir()
    (plugin_dir / "schema.yaml").write_text(PLUGIN_YAML, encoding="utf-8")
    monkeypatch.setattr(loader_module, "plugins_root", lambda: tmp_path)
    return PLUGIN_ID


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    for name in LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _set_full_real_env(monkeypatch, *, allow_network: bool = True):
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_MODEL", "rt-model")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_API_KEY", "sk-rt-redact-0000000000")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_BASE_URL", "https://rt.provider.invalid")
    monkeypatch.setenv(
        "TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK", "1" if allow_network else "0"
    )
    monkeypatch.delenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", raising=False)


def _definition(schema_id: str = PLUGIN_ID):
    from transit_scholar.layer2.schema_extraction import get_schema_definition

    return get_schema_definition(schema_id)


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


class ScriptedClient:
    """Records every structured call and returns a preset response."""

    is_fake = False
    provider_name = "openai_compatible"
    model_name = "scripted"

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def generate_structured(self, messages, output_schema, metadata=None):
        self.calls.append(
            {
                "messages": list(messages),
                "output_schema": output_schema,
                "metadata": dict(metadata or {}),
            }
        )
        if self.error is not None:
            raise self.error
        return output_schema.model_validate(self.response)


# ---------------------------------------------------------------------------
# AC-RW-01/02/03: single dotenv bootstrap boundary
# ---------------------------------------------------------------------------


def test_project_dotenv_path_is_derived_not_hardcoded():
    import transit_scholar.config as config_module

    p = config_module.project_dotenv_path()
    assert p.name == ".env"
    assert p.is_absolute()
    assert p.parent == Path(__file__).resolve().parents[1]


def test_ensure_project_dotenv_feeds_llm_config_from_temp_env(
    tmp_path, monkeypatch
):
    """AC-RW-02/03: after the bootstrap loads a temp ``.env`` (override=False,
    without touching the real one), ``LLMConfig.from_env()`` reads it."""
    import transit_scholar.config as config_module

    env_file = tmp_path / "my.env"
    env_file.write_text(
        "TRANSIT_SCHOLAR_LLM_PROVIDER=temp_provider\n"
        "TRANSIT_SCHOLAR_LLM_MODEL=temp-model\n"
        "TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK=0\n",
        encoding="utf-8",
    )
    for key in LLM_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_module, "_env_loaded", False)
    monkeypatch.setattr(config_module, "project_dotenv_path", lambda: env_file)
    config_module.ensure_project_dotenv()
    config = LLMConfig.from_env()
    assert config.provider == "temp_provider"
    assert config.model == "temp-model"
    assert config.allow_network is False


def test_ensure_project_dotenv_is_process_once_and_never_repollutes(
    tmp_path, monkeypatch
):
    """The one-shot guard means a later call after ``delenv`` does not re-load."""

    import transit_scholar.config as config_module

    env_file = tmp_path / "once.env"
    env_file.write_text(
        "TRANSIT_SCHOLAR_LLM_PROVIDER=once_provider\n", encoding="utf-8"
    )
    for key in LLM_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_module, "_env_loaded", False)
    monkeypatch.setattr(config_module, "project_dotenv_path", lambda: env_file)
    config_module.ensure_project_dotenv()
    assert LLMConfig.from_env().provider == "once_provider"
    # delete the loaded value, then call again: the flag must prevent a reload
    monkeypatch.delenv("TRANSIT_SCHOLAR_LLM_PROVIDER", raising=False)
    config_module.ensure_project_dotenv()
    assert LLMConfig.from_env().provider is None


# ---------------------------------------------------------------------------
# AC-RW-05: frozen resolver truth table
# ---------------------------------------------------------------------------


def test_resolve_explicit_fake_only_path(monkeypatch):
    # Even with a full real env present, explicit provider=fake is the fake.
    _set_full_real_env(monkeypatch)
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_PROVIDER", "fake")
    client = resolve_llm_client()
    assert isinstance(client, FakeLLMProvider)
    assert client.is_fake is True


def test_resolve_unconfigured_raises_never_fake():
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_llm_client()
    assert excinfo.value.error_code == "llm_unavailable"
    assert "not_found" not in str(excinfo.value)


def test_resolve_complete_real_returns_real_client_without_network():
    config = LLMConfig(
        provider="openai_compatible",
        model="rt-model",
        api_key="sk-rt-redact-0000000000",
        base_url="https://rt.provider.invalid",
        allow_network=True,
    )
    client = resolve_llm_client(config)
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.is_fake is False
    assert client.provider_name == "openai_compatible"
    assert client.model_name == "rt-model"


def test_resolve_real_provider_missing_field_names_env_var():
    config = LLMConfig(
        provider="openai_compatible",
        model=None,
        api_key="sk-rt-redact-0000000000",
        base_url="https://rt.provider.invalid",
        allow_network=True,
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_llm_client(config)
    assert "TRANSIT_SCHOLAR_LLM_MODEL" in str(excinfo.value)


def test_resolve_other_provider_returns_stub_whose_call_fails():
    config = LLMConfig(
        provider="anthropic",
        model="m",
        api_key="k",
        base_url="https://x.invalid",
        allow_network=True,
    )
    client = resolve_llm_client(config)
    assert isinstance(client, RealLLMClientStub)
    assert client.is_fake is False
    with pytest.raises(LLMUnavailableError):
        client.generate_structured([{"role": "user", "content": "u"}], FieldExtractionLLMOutput)


# ---------------------------------------------------------------------------
# AC-RW-04: default path resolves the runtime client
# ---------------------------------------------------------------------------


def test_default_extract_resolves_runtime_real_client_no_network(
    tmp_path, test_schema, monkeypatch
):
    """Full real env + cleared block gate -> extract_schema() with no injected
    client resolves the real client; FakeRetrieval has no hits so nothing is
    sent over the network and the manifest records the real provider."""
    _set_full_real_env(monkeypatch)
    result = extract_schema(PAPER_ID, test_schema, storage_root=tmp_path)
    assert result.manifest.llm_fake is False
    assert result.manifest.llm_provider == "openai_compatible"
    assert result.run_manifest.llm_provider == "openai_compatible"
    # FakeRetrieval default -> no hits -> clean not_found for every field
    assert {e.field_result_status for e in result.manifest.fields} == {"not_found"}
    assert all(f.status == "not_found" for f in result.instance.fields.values())


# ---------------------------------------------------------------------------
# AC-RW-06: injected client precedence / resolver not called
# ---------------------------------------------------------------------------


def test_injected_fake_beats_real_env(monkeypatch, tmp_path, test_schema):
    import transit_scholar.layer2.schema_extraction.api as api_module

    _set_full_real_env(monkeypatch)
    calls: list[str] = []
    original = api_module.resolve_runtime_llm_client

    def spy(config=None):
        calls.append("resolve")
        return original(config)

    monkeypatch.setattr(api_module, "resolve_runtime_llm_client", spy)
    result = extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        llm_client=FakeLLMProvider(),
    )
    assert result.manifest.llm_fake is True
    assert result.manifest.llm_provider == "fake"
    assert calls == []  # resolver never invoked when a client is injected


# ---------------------------------------------------------------------------
# AC-RW-08/10: failure semantics
# ---------------------------------------------------------------------------


def test_unconfigured_default_extract_raises_and_writes_nothing(tmp_path):
    storage_root = tmp_path / "storage"
    with pytest.raises(LLMUnavailableError) as excinfo:
        extract_schema(PAPER_ID, PLUGIN_ID, storage_root=storage_root)
    assert excinfo.value.error_code == "llm_unavailable"
    assert not storage_root.exists()
    assert not list(tmp_path.iterdir())


class UnavailableClient:
    is_fake = False
    provider_name = "openai_compatible"
    model_name = "x"

    def generate_structured(self, messages, output_schema, metadata=None):
        raise LLMUnavailableError("provider offline (injected)")


def test_injected_client_failure_is_explicit_field_level_not_not_found(
    tmp_path, test_schema
):
    from transit_scholar.layer2.schema_extraction import build_field_query

    definition = _definition(test_schema)
    q = build_field_query(
        definition.sections[0].fields[0], definition.sections[0], definition
    ).query
    retrieval = FakeRetrieval(
        responses={(PAPER_ID, q): _ok_result(_hit(text="has text", block_id="blk_x"))}
    )
    result = extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        llm_client=UnavailableClient(),
        retrieval=retrieval,
    )
    by_field = {e.field_id: e for e in result.manifest.fields}
    assert by_field["headline"].error_code == "llm_unavailable"
    assert "llm_unavailable" in result.instance.fields["headline"].notes
    assert result.instance.fields["headline"].status == "unclear"
    # never a fake success, never a fabricated not_found
    assert result.instance.fields["headline"].value is None


# ---------------------------------------------------------------------------
# AC-RW-11/12/13: real StructuredSemanticVerifier
# ---------------------------------------------------------------------------


def _field() -> FieldDefinition:
    return FieldDefinition(
        id="headline",
        label="Headline",
        question="What is the headline?",
        description="The paper headline.",
        type="string",
    )


def _evidenced_result() -> FieldResult:
    return FieldResult(
        value="Bus Control RL",
        status="explicit",
        confidence=0.9,
        notes="stated in the abstract",
        evidence=[
            EvidenceRef(
                block_id="blk_a",
                char_start=0,
                char_end=14,
                pages=[2],
                section_path=["Abstract"],
                quote="the bus control problem",
            ),
            EvidenceRef(
                block_id="blk_b",
                char_start=3,
                char_end=10,
                pages=[5],
                section_path=["Method"],
                quote="control",
            ),
        ],
    )


def test_verifier_prompt_carries_field_value_status_and_full_evidence():
    field = _field()
    result = _evidenced_result()
    messages = build_semantic_verifier_messages(field, result)
    payload = json.loads(messages[1]["content"])
    text = json.dumps(payload, ensure_ascii=False)
    assert field.question in text
    assert field.label in text
    assert '"value": "Bus Control RL"' in text
    assert '"status": "explicit"' in text
    assert "blk_a" in text and "blk_b" in text
    assert "the bus control problem" in text
    assert "output_schema" not in payload
    # complete evidence set: every EvidenceRef is inside the single request
    assert text.count('"block_id"') == len(result.evidence)


def test_verifier_single_structured_call_with_full_evidence():
    client = ScriptedClient(
        {"decision": "unsupported", "confidence": 0.1, "notes": "weak evidence"}
    )
    verifier = StructuredSemanticVerifier(client)
    field = _field()
    result = _evidenced_result()
    verdict = verifier(field, result, [ref.quote for ref in result.evidence if ref.quote])
    assert verdict.decision == "unsupported"
    assert len(client.calls) == 1  # exactly one structured call
    payload = json.dumps(client.calls[0]["messages"], ensure_ascii=False)
    assert "blk_a" in payload and "blk_b" in payload
    assert "Bus Control RL" in payload
    assert client.calls[0]["metadata"]["field_id"] == "headline"


def test_verifier_does_not_modify_evidence_or_result():
    client = ScriptedClient(
        {"decision": "supported", "confidence": 0.9, "notes": ""}
    )
    verifier = StructuredSemanticVerifier(client)
    result = _evidenced_result()
    before = result.model_dump()
    verifier(_field(), result, [])
    assert result.model_dump() == before


@pytest.mark.parametrize(
    "decision, expected_type, expected_action",
    [
        ("supported", None, None),
        ("partially_supported", "semantic_partially_supported", None),
        ("unsupported", "semantic_unsupported", "recheck"),
        ("conflicting", "semantic_conflicting", "recheck"),
        ("unclear", "semantic_unclear", None),
    ],
)
def test_verifier_verdict_maps_to_fixed_issue(
    decision, expected_type, expected_action
):
    client = ScriptedClient(
        {"decision": decision, "confidence": 0.5, "notes": "scripted"}
    )
    verifier = StructuredSemanticVerifier(client)
    issues = verify_field_semantics(_field(), _evidenced_result(), verifier)
    if expected_type is None:
        assert issues == []
    else:
        assert issues[0].type == expected_type
        assert issues[0].severity == "warning"
        assert issues[0].action == expected_action


def test_verifier_client_failure_becomes_verifier_unavailable():
    client = ScriptedClient(error=LLMInvalidOutputError("invalid verdict output"))
    verifier = StructuredSemanticVerifier(client)
    issues = verify_field_semantics(_field(), _evidenced_result(), verifier)
    assert issues[0].type == "verifier_unavailable"
    assert issues[0].severity == "error"


# ---------------------------------------------------------------------------
# AC-RW-14: default Targeted Recheck reuses the same client, at most once
# ---------------------------------------------------------------------------


def test_recheck_reuses_same_client_no_resolve(tmp_path, test_schema, monkeypatch):
    import transit_scholar.layer2.schema_extraction.api as api_module

    # seed a current run with the offline fake
    extract_schema(
        PAPER_ID, test_schema, storage_root=tmp_path, llm_client=FakeLLMProvider()
    )
    from transit_scholar.layer2.schema_extraction import build_field_query

    definition = _definition(test_schema)
    q = build_field_query(
        definition.sections[0].fields[0], definition.sections[0], definition
    ).query
    retrieval = FakeRetrieval(
        responses={
            (PAPER_ID, q): _ok_result(_hit(text="recheck target text", block_id="blk_r"))
        }
    )
    counting = ScriptedClient(
        {"value": "Rechecked Headline", "status": "explicit", "evidence_ids": ["E1"]}
    )

    calls: list[str] = []
    original = api_module.resolve_runtime_llm_client

    def spy(config=None):
        calls.append("resolve")
        return original(config)

    monkeypatch.setattr(api_module, "resolve_runtime_llm_client", spy)

    result = recheck_fields(
        PAPER_ID,
        test_schema,
        ["headline"],
        storage_root=tmp_path,
        llm_client=counting,
        retrieval=retrieval,
        verifier=FakeSemanticVerifier(
            default_response={"decision": "supported", "confidence": None, "notes": ""}
        ),
    )
    # the target field was re-extracted through the very same counting client
    assert [c["metadata"].get("field_id") for c in counting.calls] == ["headline"]
    assert result.instance.fields["headline"].value == "Rechecked Headline"
    assert result.run_manifest.run_reason == "recheck"
    assert calls == []  # no re-resolve, no .env reload
    assert all(
        entry.updated == (entry.field_id == "headline")
        for entry in result.report.recheck_trace.entries
    )


# ---------------------------------------------------------------------------
# AC-RW-16: network block
# ---------------------------------------------------------------------------


def test_network_block_raises_blocked_before_any_client(monkeypatch):
    _set_full_real_env(monkeypatch)
    monkeypatch.setenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", "1")
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_runtime_llm_client()
    assert "TRANSIT_SCHOLAR_BLOCK_NETWORK" in str(excinfo.value)


def test_network_block_blocks_default_extract(monkeypatch, tmp_path, test_schema):
    _set_full_real_env(monkeypatch)
    monkeypatch.setenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", "1")
    storage_root = tmp_path / "storage"
    with pytest.raises(LLMUnavailableError) as excinfo:
        extract_schema(PAPER_ID, test_schema, storage_root=storage_root)
    assert "TRANSIT_SCHOLAR_BLOCK_NETWORK" in str(excinfo.value)
    assert not storage_root.exists()


# ---------------------------------------------------------------------------
# AC-RW-17: redaction
# ---------------------------------------------------------------------------


def test_redaction_sentinel_key_and_auth_never_in_outputs(
    tmp_path, test_schema, monkeypatch
):
    sentinel = "sk-live-redact-abcdef0123456789"
    base_url = "https://llm.provider.invalid/secret-v1"
    real_client = OpenAICompatibleLLMClient(
        LLMConfig(
            provider="openai_compatible",
            model="rt-model",
            api_key=sentinel,
            base_url=base_url,
            allow_network=True,
        ),
        transport=None,  # never reached: FakeRetrieval has no hits
    )
    result = extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        llm_client=real_client,
        retrieval=FakeRetrieval(),
    )
    assert result.manifest.llm_provider == "openai_compatible"
    for path in (tmp_path / PAPER_ID).rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert sentinel not in text
        assert "Authorization" not in text
        assert "Bearer" not in text
        assert base_url not in text

    # a resolution failure with the sentinel key configured must not leak it
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_API_KEY", sentinel)
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK", "1")
    monkeypatch.delenv("TRANSIT_SCHOLAR_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TRANSIT_SCHOLAR_LLM_MODEL", raising=False)
    monkeypatch.delenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", raising=False)
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_runtime_llm_client()
    assert sentinel not in str(excinfo.value)


# ---------------------------------------------------------------------------
# FR-005: explicit fake never networks under a full real .env
# ---------------------------------------------------------------------------


def test_explicit_fake_never_networks_under_real_env(tmp_path, test_schema, monkeypatch):
    import socket

    _set_full_real_env(monkeypatch)
    real_connect = socket.socket.connect
    attempts: list = []

    def spy_connect(self, address, *args, **kwargs):
        attempts.append(address)
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", spy_connect)
    result = extract_schema(
        PAPER_ID,
        test_schema,
        storage_root=tmp_path,
        llm_client=FakeLLMProvider(),
    )
    assert result.manifest.llm_fake is True
    assert result.manifest.llm_provider == "fake"
    assert attempts == []  # no network attempt of any kind
