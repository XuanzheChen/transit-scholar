You are the external Generator (G) for task `task-2026-08-21-001`.

This is a targeted continuation of the already user-approved implementation, before the first formal Evaluator review. Read and obey:

1. `AGENTS.md`
2. `.agents/skills/multi-agent-sdlc/SKILL.md`
3. `tasks/task-2026-08-21-001/requirements.md`
4. `tasks/task-2026-08-21-001/acceptance.md`
5. `tasks/task-2026-08-21-001/generator/plan.md`
6. `tasks/task-2026-08-21-001/execution-contract.yaml`

The structured-output reliability implementation is already present. Deterministic validation already passed 218 focused tests, 629 complete L2S2 tests, and 629 network-blocked L2S2 tests. Do not redesign or broaden it.

The remaining real-smoke failure is narrowly diagnosed:

- `scripts/l2s2_runtime_smoke.py` constructs the real retrieval wrapper and calls `extract_field_instance_in_memory(...)`.
- The helper accepts `canonical_reader`.
- The smoke currently omits it.
- Real retrieval returns source refs, the LLM extraction succeeds, and canonical evidence binding then fails with `evidence_binding_failed`.
- The existing public canonical reader is `transit_scholar.layer2.retrieval.api.read_blocks` (also exported by the retrieval package).

Implement only this repair:

1. In `scripts/l2s2_runtime_smoke.py`, import the existing L2S1 public `read_blocks` function and pass that exact callable to `extract_field_instance_in_memory(..., canonical_reader=read_blocks)`.
2. In `tests/test_l2s2_runtime_smoke.py`, update deterministic coverage so the CLI success test proves the canonical reader supplied to extraction is the existing imported public reader. Keep the test fully offline and preserve API-key/base-URL leakage assertions.
3. Do not modify L2S1 retrieval/read_blocks, evidence binding, schema, gold, Package E, `.env`, documentation, or any other file.
4. Do not run a real network smoke. Run deterministic tests only.

Validation requested:

- `python -m pytest tests/test_l2s2_runtime_smoke.py -q`
- `python -m pytest tests/test_l2s2_*.py -q`
- with `TRANSIT_SCHOLAR_BLOCK_NETWORK=1`, `python -m pytest tests/test_l2s2_*.py -q`

Self-repair any failures caused by these two-file changes. If an unapproved file is required, stop before editing and report `CONTRACT_UPDATE_REQUIRED`.

Your final response must begin exactly with:

STATUS: completed
NEXT: evaluator
BLOCKING: false

Then summarize changed files and deterministic test results. If blocked, keep the required header values parseable and explain the blocker clearly without editing outside the contract.
