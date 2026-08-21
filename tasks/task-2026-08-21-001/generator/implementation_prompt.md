You are the external Generator implementing task-2026-08-21-001 as one complete
package.

Read completely before editing:

- tasks/task-2026-08-21-001/requirements.md
- tasks/task-2026-08-21-001/acceptance.md
- tasks/task-2026-08-21-001/generator/plan.md
- tasks/task-2026-08-21-001/execution-contract.yaml
- AGENTS.md
- every current product/test/document file you intend to edit

The working tree contains approved uncommitted changes from
task-2026-08-20-001. Preserve and build on them. Do not reset, revert, or
rewrite unrelated user work.

Implement the approved Structured Output Reliability Contract and document
closure exactly as frozen. You may edit only `write_paths` in
`execution-contract.yaml`. The task directory, workflow configuration,
requirements, acceptance, state, `.env`, Gold, fixtures, Package E, Schema
plugins, L2S1, and all other forbidden paths are read-only. If a necessary file
is absent from the write contract, stop before editing it and return
`CONTRACT_UPDATE_REQUIRED` with the path and reason.

Key implementation constraints:

- Pydantic `output_schema` is the only structure source.
- Implement auto/json_schema/json_object with strict schema first in auto and
  one narrowly classified capability fallback only.
- Never treat auth, permission, rate limit, timeout, connection, ordinary 4xx,
  or 5xx errors as capability fallback.
- Always JSON/Pydantic validate and permit at most one sanitized, bounded
  structured correction request.
- Do not fabricate SemanticVerdict fields or convert failures to fake,
  `unclear`, or `not_found` business success.
- Keep client format correction separate from Extractor post-validation
  business retry.
- Preserve one shared client across Extractor, Verifier, and Recheck and retain
  dependency-injection precedence.
- Make the runtime smoke truly one paper x one field without modifying or
  synthesizing Schema/Gold.
- Update only the specified development-status document for the 85-warning and
  Package E null-metric explanations.
- Do not read, print, log, or archive actual secrets from `.env`.

Add/update deterministic tests and self-repair until they pass. At minimum run:

```powershell
python -m pytest tests/test_l2s2_llm_client.py tests/test_l2s2_llm_real_provider.py tests/test_l2s2_extraction_engine.py tests/test_l2s2_runtime_wiring.py tests/test_l2s2_validation_semantic.py tests/test_l2s2_validation_pipeline.py tests/test_l2s2_recheck.py tests/test_l2s2_runtime_smoke.py -q
python -m pytest tests/test_l2s2_*.py -q
powershell -NoProfile -Command '$env:TRANSIT_SCHOLAR_BLOCK_NETWORK="1"; python -m pytest tests/test_l2s2_*.py -q'
```

Do not run the real network smoke in this Generator round. It will be executed
later under controlled Runner/Evaluator validation.

Return Markdown with this exact header:

STATUS: completed
NEXT: evaluator
BLOCKING: false

Then report changed files, implementation facts, tests and exact results,
remaining risks, and whether any real-smoke prerequisite remains. If blocked or
the contract must change, set BLOCKING: true and explain precisely; do not edit
outside the contract.
