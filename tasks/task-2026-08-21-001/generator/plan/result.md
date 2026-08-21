STATUS: completed
NEXT: evaluator
BLOCKING: false

## Implementation Plan

### 1. Files and responsibilities

- `src/transit_scholar/layer2/schema_extraction/llm.py`
  - Extend `LLMConfig` with `structured_output_mode: auto | json_schema | json_object`, defaulting to `auto`.
  - Read the mode from a `TRANSIT_SCHOLAR_LLM_*` environment variable through the existing configuration boundary.
  - Reject invalid modes before constructing or contacting a provider.
  - Generate provider payloads from the supplied Pydantic `output_schema`.
  - Implement strict-schema, JSON-object, capability fallback, shared validation, bounded correction, retry classification, redaction, and request accounting.
- `src/transit_scholar/layer2/schema_extraction/errors.py`
  - Add an explicit capability-incompatibility error/result classification.
  - Preserve separate error identity for transport failures, unavailable configuration, invalid structured output, and exhausted format correction.
  - Carry only bounded, sanitized diagnostic data.
- `src/transit_scholar/layer2/schema_extraction/engine.py`
  - Preserve existing Extractor business validation and evidence-binding retry behavior.
  - Stop retrying format-only failures after the client has already performed its single correction request.
  - Keep business retries for field-type, status/evidence, unknown-evidence, and binding errors.
  - Add a narrow in-memory field-selection path for smoke execution so the existing schema remains unchanged and only the requested field is processed.
- `src/transit_scholar/layer2/schema_extraction/semantic.py`
  - Continue using the injected shared `StructuredLLMClient`.
  - Rely on the client’s common Pydantic validation/correction boundary for `SemanticVerdict`.
  - Preserve the existing five-state verdict mapping, evidence immutability, and `verifier_unavailable` failure semantics.
- `src/transit_scholar/layer2/schema_extraction/api.py`
  - Preserve injection precedence and the composition-root lifecycle.
  - Ensure Extractor, `StructuredSemanticVerifier`, and runtime Recheck receive the same resolved client instance.
  - Do not add a second resolver, dotenv load, provider, parser, or retry implementation.
- `src/transit_scholar/layer2/schema_extraction/__init__.py`
  - Export the new configuration mode and any stable capability/correction error types needed by tests or callers.
- `scripts/l2s2_runtime_smoke.py`
  - Require exactly one paper and exactly one field.
  - Reject zero or multiple fields before runtime execution.
  - Use the existing schema definition and real retrieval; do not synthesize a reduced schema or alter Gold.
  - Run only the selected field’s extraction, then real semantic verification using the same runtime-resolved client.
  - Keep safe evidence output limited to provider, permitted model name, client class, network flags, field id, extraction status, semantic decision, and final success.
  - Preserve explicit non-zero outcomes for blocked network, unavailable configuration, transport errors, invalid output, and unsuccessful verification.
- `tests/test_l2s2_llm_client.py`
  - Cover mode parsing/defaults/invalid values and pre-network rejection.
  - Verify the shared Pydantic schema is the sole source for validation and JSON-object guidance.
  - Add redaction assertions for schema, prompts, correction diagnostics, errors, traces, and call records.
- `tests/test_l2s2_llm_real_provider.py`
  - Use only `httpx.MockTransport` and patched timing.
  - Assert strict payload shape, JSON-object payload shape, capability fallback, request counts, transport retry bounds, one correction request, and final validation.
  - Cover 401/403/429/timeouts/connection failures/ordinary 4xx/5xx as non-capability failures.
  - Verify `json_schema` mode never falls back.
- `tests/test_l2s2_extraction_engine.py`
  - Prove client format repair and Extractor business retry are distinct and never cause a second format-repair cycle.
  - Preserve existing one-business-retry behavior and placeholder/error semantics.
  - Test one-field extraction without invoking other schema fields.
- `tests/test_l2s2_runtime_wiring.py`
  - Assert one client object is shared by Extractor, Verifier, and Recheck.
  - Preserve custom/fake injection precedence and blocked-network behavior.
- `tests/test_l2s2_validation_semantic.py`
  - Add real structured-verifier tests using a scripted client for successful correction, exhausted correction, unchanged evidence, and `verifier_unavailable`.
- Add or extend a focused smoke test module under `tests/test_l2s2_*.py`
  - Exercise argument validation and deterministic one-field wiring without network.
  - Assert that the smoke path does not traverse the complete 39-field extraction route.
- `doc/20260814-L2S2-Schema提取与验证开发情况说明.md`
  - Record the structured-output root cause and final reliability contract.
  - Explain all 85 warnings as `35 value_mismatch + 35 judgement_conflict + 15 status_mismatch`, including overlap and non-blocking status.
  - Document Package E’s absent direct `strict_traceability_rate` and `not_found_correctness` aggregates, with the canonical audit facts and no invented metrics.
  - Add sanitized real-smoke evidence, one-field scope, endpoint/output outcome, and the resulting Freeze conclusion.

### 2. Configuration and payload shape

`LLMConfig` will expose:

```text
structured_output_mode: "auto" | "json_schema" | "json_object"
```

The environment boundary will expose the corresponding `TRANSIT_SCHOLAR_LLM_*` setting, defaulting to `auto`; existing timeout, transport retry, rate-limit, provider, model, key, base URL, and network controls remain unchanged.

For `json_schema`, the request will contain an OpenAI-compatible structure equivalent to:

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "<stable sanitized schema name>",
    "strict": true,
    "schema": "<output_schema.model_json_schema()>"
  }
}
```

For `json_object`, the request will contain only:

```json
{"type": "json_object"}
```

The messages will include bounded JSON-object guidance derived from the same Pydantic JSON Schema. No hand-maintained provider schema will be added.

`auto` first sends strict JSON Schema. It falls back exactly once to JSON-object mode only after an explicit provider capability rejection for `response_format`/`json_schema`. Generic “schema” or “JSON” wording is insufficient to trigger fallback.

### 3. Validation and correction

Every successful HTTP response follows the same pipeline:

1. Validate the provider response envelope.
2. Extract message content and remove the existing compatible JSON fence.
3. Parse JSON.
4. Require a JSON object.
5. Run `output_schema.model_validate()`.

The strict schema, JSON-object guidance, correction instructions, and final validation all derive from the supplied Pydantic model.

A parse/object/schema failure after a successful HTTP response triggers at most one correction request using the same client/model. The correction prompt includes only:

- bounded, sanitized invalid output;
- bounded, sanitized validation details;
- a direct instruction to return one corrected JSON object.

The corrected response goes through the complete validation pipeline again. A second failure raises `LLMInvalidOutputError`; it never fabricates `unclear`, fills missing fields, or converts to `not_found`.

The client records capability fallback, transport retry, and correction retry independently. Once client-level format repair is exhausted, the Extractor may perform only its existing business correction for business-level field/evidence errors.

### 4. Shared runtime behavior

The composition root continues to resolve one client per operation. That exact instance is passed to:

- `ExtractionEngine`;
- `StructuredSemanticVerifier`;
- `build_runtime_recheck_callable`.

Verifier and Recheck do not reload `.env`, resolve another provider, or implement their own parser/correction loop. Explicit injected clients, verifiers, recheck callables, and fakes remain higher priority than runtime resolution.

All configuration, transport, timeout, rate-limit, capability, and provider failures remain explicit system failures and cannot become `not_found`.

### 5. One-field smoke

The smoke command will require one `--paper` and one `--field` value and reject missing or multiple fields before any provider call. It will use the existing `bus_control_rl` schema definition, select the requested field from that definition, run real retrieval and extraction only for that field, and invoke `StructuredSemanticVerifier` for that same field with the same client.

It will not modify the schema tree, Gold data, persisted schema definitions, or `.env`. The temporary network override remains process-local. Output remains redacted and excludes API keys, authorization values, and full base URLs.

### 6. Validation and evidence

The focused tests, complete L2S2 deterministic suite, network-blocked suite, and existing redaction/API-key tests will run without real network access. The real smoke will not be run in this planning round; its eventual evidence will be recorded only after an explicitly authorized execution.

This plan does not modify forbidden scope: BusControlRL schema fields, schema plugins, Gold/judgements, L2S1 parsing/retrieval behavior, evidence canonical binding, Package E acceptance/reporting logic, status semantics, Wiki, Layer3, Knowledge Graph, multi-model routing, databases, or user `.env` contents.

The plan satisfies every frozen acceptance criterion by making Pydantic the single contract source, constraining fallback to explicit capability incompatibility, bounding correction to one request, separating client format repair from Extractor business retry, sharing one client across all three runtime roles, proving true one-field smoke wiring, preserving explicit failure semantics, and documenting the root cause, warning facts, Package E metric limitation, smoke evidence, and Freeze state.