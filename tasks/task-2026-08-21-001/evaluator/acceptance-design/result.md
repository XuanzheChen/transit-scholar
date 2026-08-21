DECISION: accept
BLOCKING_COUNT: 0
REQUIRES_USER_DECISION: false

## Proposed Acceptance Criteria

### AC-01 — Pydantic Single Source of Truth

- `generate_structured(messages, output_schema, metadata)` derives the provider schema, JSON-object prompt guidance, correction guidance, and final validation from the supplied `output_schema`.
- The provider schema equals `output_schema.model_json_schema()`.
- Extractor, Verifier, and Recheck do not maintain separate handwritten structural schemas.
- Business-semantic prompt text may remain, but cannot duplicate required fields, types, defaults, or enum constraints independently of Pydantic.
- Every successful provider response is envelope-validated, JSON-parsed, confirmed to be an object, and revalidated using `output_schema.model_validate()`.

### AC-02 — Strict JSON Schema Request Shape

- Strict mode sends:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "<stable-legal-name>",
      "strict": true,
      "schema": "<output_schema.model_json_schema()>"
    }
  }
}
```

- The schema name is deterministic for the same Pydantic model and valid for the OpenAI-compatible request shape.
- The schema, name, messages, errors, logs, traces, and manifests contain no API key, authorization value, or complete base URL.
- A `200` response that violates the Pydantic model is rejected even when the provider claims strict enforcement.

### AC-03 — Structured-Output Modes

- The unified LLM configuration exposes `auto`, `json_schema`, and `json_object`, defaults to `auto`, and reads its setting through one documented `TRANSIT_SCHOLAR_LLM_*` environment variable.
- Invalid or blank-invalid mode values fail before any HTTP request.
- `json_schema` sends strict JSON Schema and never silently changes modes.
- `json_object` sends only `{"type":"json_object"}` and adds concise guidance derived from the same Pydantic JSON Schema.
- `auto` attempts strict JSON Schema first and performs exactly one JSON-object fallback only after an explicit provider response identifying `response_format/json_schema` as unsupported.

### AC-04 — Narrow Capability Fallback

- Deterministic transport fixtures demonstrate that an explicit schema-capability rejection causes exactly two requests in `auto`: one strict request and one JSON-object request.
- The fallback response is fully parsed and Pydantic-validated.
- `401`, `403`, `429`, timeout, connection failure, `5xx`, and ordinary `4xx` responses never trigger capability fallback.
- `json_schema` mode returns an explicit request/capability failure for the same unsupported response.
- Capability classification cannot be triggered merely by a generic error containing words such as “schema” or “JSON.”
- Provider capability failure remains distinguishable from a successful HTTP response containing invalid model output.

### AC-05 — Exactly One Structured Correction

- A successful HTTP response containing malformed JSON, a non-object JSON value, a missing required field, an invalid enum, or another Pydantic validation failure causes exactly one correction request.
- The correction uses the same client and model, includes the previous invalid output, and requests only a corrected JSON object.
- Validation details in the correction prompt are concise, length-bounded, and redact API keys, authorization values, and complete base URLs.
- The correction response passes the complete envelope, JSON, object, and Pydantic validation pipeline.
- If the correction remains invalid, the call raises `LLMInvalidOutputError` with the stable invalid-output semantics.
- No path guesses `decision="unclear"`, fills missing required fields, returns fake success, or converts failure to `not_found`.

### AC-06 — Retry Separation and Bounds

- Tests independently count transport retry, capability fallback, and structured correction requests.
- Transport retries apply only to their existing eligible transport/status failures and remain bounded by configured retry limits.
- Capability fallback occurs at most once per logical structured call.
- Structured correction occurs at most once per logical structured call.
- A capability fallback followed by invalid JSON-object output may produce one correction, but cannot restart strict mode or perform another capability fallback.
- After client-level schema correction is exhausted, Extractor does not perform another equivalent schema-format retry.
- Existing Extractor retries may remain only for post-validation business failures such as evidence identifiers, field business types, or absent-status rules.

### AC-07 — Shared Client and Failure Semantics

- Schema Extractor, `StructuredSemanticVerifier`, and Targeted Recheck receive the identical `StructuredLLMClient` object from the composition root.
- Verifier and Recheck do not reload `.env`, resolve another runtime client, construct another provider, or implement separate parsing/correction logic.
- Explicitly injected clients, verifiers, recheck callables, and fakes retain precedence over runtime resolution.
- A corrected `SemanticVerdict` returns one of the existing five valid decisions without modifying the Evidence Set.
- Exhausted verifier output correction maps to `verifier_unavailable`.
- Transport, configuration, and provider failures remain system failures and never become `not_found`.

### AC-08 — Deterministic Single-Field Smoke Wiring

- A deterministic smoke test selects exactly one paper and one field.
- It asserts that retrieval, extraction, and real `StructuredSemanticVerifier` are invoked only for that field.
- It asserts that no other BusControlRL field is retrieved, extracted, verified, or rechecked.
- It uses the existing schema definition without modifying or synthesizing a reduced schema or Gold dataset.
- Extraction and semantic verification use the same injected client instance.

### AC-09 — Real Smoke

- The real smoke accepts exactly one paper and one field; zero or multiple fields are rejected before execution.
- It uses runtime-resolved `OpenAICompatibleLLMClient`, real retrieval, one-field extraction, and `StructuredSemanticVerifier`.
- A successful run returns exit code `0` and records provider name, publicly allowed model name, client class, effective network permission, field ID, extraction status, semantic decision, and final success status.
- Output and retained evidence contain no API key, `Authorization` header/value, bearer token, or complete base URL.
- The command does not modify `.env`; network blocking is disabled only in the smoke process.
- Capability incompatibility is reported as a provider capability/request failure, while a `200` response failing JSON/Pydantic validation is reported as invalid model output.
- Acceptance requires one successful real run producing a valid `SemanticVerdict`; a skip, fake fallback, full-schema run, or classified failure is not a successful smoke.

### AC-10 — Network Blocking and Redaction

- With `TRANSIT_SCHOLAR_BLOCK_NETWORK=1`, the complete L2S2 deterministic suite performs no real provider connection and passes independently of developer `.env` contents.
- Sentinel API keys, authorization values, and complete base URLs are absent from exceptions, prompts, schemas, correction messages, call records, generated JSON, manifests, traces, smoke output, and documentation.
- Explicit fake and custom-injection paths remain offline even when real provider variables exist.

### AC-11 — Documentation Facts

`doc/20260814-L2S2-Schema提取与验证开发情况说明.md` must:

- Explain the root cause as fixed JSON-object requests, missing Pydantic schema propagation, and absence of unified structured correction—not fake/runtime wiring.
- Document the final strict-schema, narrow fallback, Pydantic revalidation, one-correction, and explicit-final-failure contract.
- Record only the actual redacted single-field smoke command, result, and evidence.
- Close the real structured-output Freeze blocker only after the successful smoke required by AC-09.
- State that the 85 warnings are `35 value_mismatch + 35 judgement_conflict + 15 status_mismatch`.
- State that the first 70 diagnostics represent 35 underlying exact-match cases whose human Gold judgement is `correct`.
- State that all 15 status warnings are Gold `explicit` versus predicted `inferred`.
- State that 10 field instances have both diagnostic classes, yielding 40 distinct paper-field instances rather than 85 independent errors.
- State that these are non-blocking 2026-08-19 Package E diagnostics with `blocking_error_count=0`, unrelated causally to the 2026-08-20 verifier smoke.
- State that Package E currently reports `strict_traceability_rate` and `not_found_correctness` as `null`; this is a reporting-completeness gap, not proof of capability failure.
- Cite only the existing independent canonical-audit facts that quote mismatch and page untraceable counts are zero, without fabricating Package E metric values.

### AC-12 — Scope Protection

- No changes are made to the BusControlRL field tree, schema plugins, Gold data or judgements, L2S1 parsing/retrieval, Jina strategy, evidence canonical binding, Package E code or rules, status semantics, Wiki, Layer3, Knowledge Graph, databases, multi-model routing, verifier-specific models, or multi-agent product behavior.
- The user’s `.env` and real configuration remain unchanged.
- `SemanticVerdict` is not relaxed and receives no defaults for required semantic output.
- The six-paper Package E acceptance is not rerun as part of this task.
- Product changes are limited to the shared structured-output reliability boundary, necessary configuration/export wiring, single-field smoke entry, focused tests, and the specified development document.

## Verification Commands

### Focused Deterministic Tests

```powershell
python -m pytest tests/test_l2s2_llm_client.py tests/test_l2s2_llm_real_provider.py tests/test_l2s2_runtime_wiring.py tests/test_l2s2_validation_semantic.py tests/test_l2s2_validation_pipeline.py tests/test_l2s2_recheck.py -q
```

### Retry and Redaction Tests

```powershell
python -m pytest tests/test_l2s2_llm_client.py tests/test_l2s2_llm_real_provider.py tests/test_l2s2_runtime_wiring.py -q -k "schema or json_object or auto or fallback or retry or correction or invalid_output or redact or api_key or authorization or network_block"
```

### Complete L2S2 Suite

```powershell
python -m pytest tests/test_l2s2_*.py -q
```

### Network-Blocked L2S2 Suite

```powershell
powershell -NoProfile -Command '$env:TRANSIT_SCHOLAR_BLOCK_NETWORK="1"; python -m pytest tests/test_l2s2_*.py -q'
```

### Warning-Fact Verification

```powershell
$r = Get-Content -Raw -Encoding utf8 output/l2s2-gold-acceptance/20260819T150047Z/acceptance_report.json | ConvertFrom-Json
$r.issues | Group-Object type | Sort-Object Name | Select-Object Name,Count
$r.metrics.overall | Select-Object issue_count,strict_traceability_rate,not_found_correctness
```

Expected issue grouping is exactly 35 `judgement_conflict`, 15 `status_mismatch`, and 35 `value_mismatch`; both named Package E metrics remain `null`.

### Real One-Field Smoke

```powershell
powershell -NoProfile -Command '$env:TRANSIT_SCHOLAR_BLOCK_NETWORK="0"; python scripts/l2s2_runtime_smoke.py --paper transit-001 --field research_problem.control_type'
```

The implemented CLI may retain `--fields` instead of introducing `--field` only if it requires exactly one value and rejects zero or multiple values. The retained evidence must include exit code and redacted output.

### Scope Review

```powershell
git diff --name-only
git diff -- src/transit_scholar/layer2/schema_extraction scripts/l2s2_runtime_smoke.py tests doc/20260814-L2S2-Schema提取与验证开发情况说明.md
git diff -- .env tests/fixtures src/transit_scholar/layer2/schema_acceptance src/transit_scholar/layer1
```