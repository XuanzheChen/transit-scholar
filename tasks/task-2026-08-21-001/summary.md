# L2S2 Structured Output Reliability Contract — Final Summary

## Outcome

Task `task-2026-08-21-001` is complete. External Evaluator decision: `accept`, blocking count `0`, no additional user decision required for task acceptance.

The former real Semantic Verifier structured-output blocker is closed. With the user's existing `.env`, the controlled runtime resolved `OpenAICompatibleLLMClient` and completed one real paper × one field through extraction and semantic verification:

```text
provider=openai_compatible
model=deepseek-v4-flash
client_class=OpenAICompatibleLLMClient
extraction_status=explicit
semantic_decision=supported
final_success=true
```

No API key, Authorization header, Bearer value, or full base URL was emitted.

## Implemented Contract

- Pydantic `output_schema` is the single structured-output contract and produces provider JSON Schema.
- Runtime supports `auto`, `json_schema`, and `json_object` modes.
- `auto` uses strict JSON Schema first and falls back only for explicit provider capability rejection.
- JSON parsing, object checking, and Pydantic validation remain mandatory in every mode.
- One bounded, sanitized correction attempt is allowed for invalid structured content.
- Terminal format failures remain explicit `LLMInvalidOutputError`; they never become Fake success, `not_found`, or fabricated `unclear`.
- Structured correction, transport retry, capability fallback, and Extractor business retry have independent bounds.
- Extractor, `StructuredSemanticVerifier`, and Targeted Recheck continue to share the same injected/runtime-resolved LLM client.
- Fake providers/verifiers remain explicit deterministic test substitutes only.
- The one-field real smoke passes the existing public L2S1 `read_blocks` function as canonical reader; L2S1 and evidence-binding implementations were not changed.

## Validation

- Focused L2S2 deterministic suite: `218 passed`.
- Complete L2S2 suite: `629 passed`.
- Complete L2S2 suite with `TRANSIT_SCHOLAR_BLOCK_NETWORK=1`: `629 passed`.
- Real one-field smoke: exit `0`, legal five-state SemanticVerdict (`supported`), `final_success=true`.
- An immediately preceding `retrieval_unavailable` was diagnosed as transient: retrieval diagnostic returned `status=ok`, `method=hybrid`, `error_code=None`, `hit_count=4`; the unchanged smoke retry succeeded.
- Independent external Evaluator: `DECISION=accept`, `BLOCKING_COUNT=0`.

## Documentation Conclusions

- The 85 Package E issues are warning-only Gold/test diagnostics: 35 `value_mismatch` + 35 paired `judgement_conflict` for 35 underlying exact-match cases, plus 15 `status_mismatch`; 40 distinct paper-field instances; `blocking_error_count=0`. Their generation logic and semantics were intentionally not modified.
- Formal Package E `strict_traceability_rate` and `not_found_correctness` remain `null`. The documentation gap is closed by accurately recording those nulls and the independent canonical audit (`evidence_quote_mismatch=0`, `evidence_pages_not_traceable=0`); Package E reporting was not changed and no metric was fabricated.

## Freeze Status

No known L2S2 V1 LLM runtime wiring or structured-output blocker remains. The technical hard gate requested by this task is closed. `declared_frozen=false` remains until the user/Planner explicitly makes the formal Freeze governance declaration; non-blocking warnings and the two documented Package E null fields are not promoted to blockers.
