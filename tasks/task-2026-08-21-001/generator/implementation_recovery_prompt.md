You are the external Generator performing a targeted implementation recovery
for task-2026-08-21-001.

The prior implementation invocation left substantial path-compliant changes in
the working tree, but the Codex CLI returned no final report because one tool
call attempted multiple patch operations on
`scripts/l2s2_runtime_smoke.py`. Preserve and inspect all current changes; do
not restart or revert approved work.

Read completely:

- tasks/task-2026-08-21-001/requirements.md
- tasks/task-2026-08-21-001/acceptance.md
- tasks/task-2026-08-21-001/generator/plan.md
- tasks/task-2026-08-21-001/execution-contract.yaml
- tasks/task-2026-08-21-001/generator/implementation-retry/stderr.log
- AGENTS.md
- the complete current diff and every changed product/test/document file

Finish the approved package. Review the existing implementation against every
frozen acceptance criterion, fix omissions or defects, and run the required
deterministic tests. Use valid patches; do not place multiple patch operations
for the same file in one patch invocation.

You may edit only the execution contract's `write_paths`. If another file is
required, stop before editing it and return `CONTRACT_UPDATE_REQUIRED`.
Do not modify `.env`, Gold, fixtures, Package E, Schema plugins, L2S1, workflow
artifacts, or any forbidden path. Do not run the real network smoke.

At minimum run and self-repair against:

```powershell
python -m pytest tests/test_l2s2_llm_client.py tests/test_l2s2_llm_real_provider.py tests/test_l2s2_extraction_engine.py tests/test_l2s2_runtime_wiring.py tests/test_l2s2_validation_semantic.py tests/test_l2s2_validation_pipeline.py tests/test_l2s2_recheck.py tests/test_l2s2_runtime_smoke.py -q
python -m pytest tests/test_l2s2_*.py -q
powershell -NoProfile -Command '$env:TRANSIT_SCHOLAR_BLOCK_NETWORK="1"; python -m pytest tests/test_l2s2_*.py -q'
```

Return Markdown with this exact header:

STATUS: completed
NEXT: evaluator
BLOCKING: false

Then report changed files, implementation facts, exact test commands/results,
redaction/network guarantees, documentation updates, and any real-smoke
prerequisite. If blocked, set BLOCKING: true with the exact reason.
