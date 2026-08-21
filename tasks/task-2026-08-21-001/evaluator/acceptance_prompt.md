You are the external Evaluator for task-2026-08-21-001 in a planning-only,
read-only acceptance-design round.

Read completely:

- tasks/task-2026-08-21-001/requirements.md
- src/transit_scholar/layer2/schema_extraction/llm.py
- src/transit_scholar/layer2/schema_extraction/errors.py
- src/transit_scholar/layer2/schema_extraction/engine.py
- src/transit_scholar/layer2/schema_extraction/semantic.py
- scripts/l2s2_runtime_smoke.py
- relevant L2S2 tests, especially test_l2s2_llm_client.py,
  test_l2s2_llm_real_provider.py, test_l2s2_runtime_wiring.py, and semantic tests
- doc/20260814-L2S2-Schema提取与验证开发情况说明.md
- output/l2s2-gold-acceptance/20260819T150047Z/acceptance_report.json

Draft complete, independently executable acceptance criteria for the approved
requirements. Cover Pydantic-as-single-source, strict JSON Schema request
shape, auto/json_schema/json_object mode semantics, narrowly scoped capability
fallback, Pydantic revalidation, exactly one structured correction attempt,
separation from transport/domain retries, explicit final failures, shared
Extractor/Verifier/Recheck client, dependency injection, single-field real
smoke, network blocking, key redaction, warning documentation facts, Package E
documentation scope, and forbidden scope changes.

Specify focused deterministic tests and broader validation commands. The real
smoke criterion must use one paper and one field only, must not expose secrets,
and must distinguish endpoint capability failures from invalid model output.
Do not invent evidence or numeric metrics absent from repository artifacts.

Do not edit any product code, tests, documents, requirements, workflow state,
or configuration. Return only Markdown with this exact header:

DECISION: accept
BLOCKING_COUNT: 0
REQUIRES_USER_DECISION: false

Then provide the proposed acceptance criteria and verification commands. If
the approved requirements are internally inconsistent, use DECISION: blocked
and explain the exact issue; do not make a new product decision.
