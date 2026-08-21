You are the external Generator for task-2026-08-21-001 in a planning-only,
read-only round. Do not edit any file.

Read completely:

- tasks/task-2026-08-21-001/requirements.md
- tasks/task-2026-08-21-001/acceptance.md
- the current working-tree versions of:
  - src/transit_scholar/layer2/schema_extraction/llm.py
  - src/transit_scholar/layer2/schema_extraction/errors.py
  - src/transit_scholar/layer2/schema_extraction/engine.py
  - src/transit_scholar/layer2/schema_extraction/semantic.py
  - src/transit_scholar/layer2/schema_extraction/api.py
  - src/transit_scholar/layer2/schema_extraction/__init__.py
  - scripts/l2s2_runtime_smoke.py
  - relevant tests/test_l2s2_*.py
  - doc/20260814-L2S2-Schema提取与验证开发情况说明.md

The worktree contains approved, uncommitted changes from
task-2026-08-20-001. Preserve and build on them; do not propose resetting or
rewriting unrelated work.

Produce a user-facing implementation plan for one complete package. It must
explain:

1. exact intended files and why each is necessary;
2. configuration shape for auto/json_schema/json_object;
3. strict request payload generation from Pydantic;
4. narrowly classified capability fallback that cannot swallow auth,
   timeout, rate-limit, ordinary 4xx, or 5xx failures;
5. shared JSON/Pydantic validation and exactly one sanitized bounded
   correction request;
6. how client-level format repair is separated from Extractor business retry;
7. how the same client continues to serve Extractor, Verifier, and Recheck;
8. how the real smoke becomes truly one field without modifying the schema or
   Gold;
9. focused, complete L2S2, network-blocked, redaction, and real-smoke tests;
10. documentation updates for the root cause, 85 warning facts, Package E null
    metrics, smoke evidence, and Freeze conclusion;
11. risks, compatibility considerations, and why the plan satisfies every
    frozen acceptance criterion.

Do not propose changes to forbidden scope. Do not expose `.env` values or
secrets. Do not run the real smoke in this planning round.

Return only Markdown with this exact header:

STATUS: completed
NEXT: evaluator
BLOCKING: false

Then provide the implementation plan. If implementation cannot be planned
within the approved scope, set BLOCKING: true and identify the exact contract
update required.
