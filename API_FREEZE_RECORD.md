# TransitScholar API Layer v1

status: Frozen

API prefix: /api/v1

tested implementation SHA: 0ff2a6ecd2fe7a442621df10505edf46f4994c7b

starting master SHA: 1fb2effdd60514e54c6d07dbbfe2f328294ddeac

date: 2026-09-10

pytest gate 1: PASS
```text
.venv\Scripts\python.exe -m pytest tests/api tests/product -q --basetemp=.pytest-tmp-api-v2
171 passed
0 failed
0 errors
```

pytest gate 2: PASS
```text
.venv\Scripts\python.exe -m pytest tests/layer3 tests/product tests/api -q --basetemp=.pytest-tmp-api-v2
178 passed
0 failed
0 errors
```

pytest gate 3: PASS
```text
.venv\Scripts\python.exe -m pytest tests/api tests/product tests/layer1 tests/layer2 tests/layer3 -q --basetemp=.pytest-tmp-api-v2
182 passed
0 failed
0 errors
```

Additional L3S7 lifecycle/provenance regressions: 6 passed, 0 failed, 0 errors.
Each required gate reports the existing Pydantic WorkspaceCreateRequest.schema shadowing warning.

real API smoke: PASS
- Real localhost TCP / Uvicorn startup and shutdown; health and capabilities.
- Paper PDF import/list/read; Schema foo/1.0 Workspace; Paper membership; foo/1.1 creation; old Workspace materialization and Wiki build.
- Conversation; Prompt 202; Run and timeline reads; cooperative pause; same-execution resume; completed Turn and canonical answer citations.
- LocalExecutionManager -> worker Product -> RuntimeFactory -> RunRuntime -> MainRuntime -> RoleRuntime.
- Deterministic provider/tool injection; no external provider traffic.
- Active-run external Schema/Wiki mutation rejected.
- Startup repairs prepared-unscheduled admission without executing research.
- Terminal Turn recovery with valid, missing and corrupt checkpoints; repeated startup is idempotent and terminal Run truth is unchanged.
- Real SQL flush failure during Turn sync leaves completed Run intact and recoverable.
- Authoritative timeline artifacts and same-run citations filter foreign/missing identities and raw diagnostics.

freeze invariants:
- Run pause crash-consistency verified
- terminal trace durability verified
- real HTTP pause/resume continuation verified
- same AgentRun / ResearchSession / RoleExecution verified
- committed action exactly-once across resume verified
- exact Workspace Schema version binding verified
- Schema 1.0 + later 1.1 lifecycle verified
- materialization status non-null
- Paper public errors sanitized
- canonical citation_refs normalized to existing public citation_references; same-run admitted Evidence ownership retained; source_refs are not answer citations
- prepared-unscheduled admission converges to failed Run and Turn without automatic execution
- terminal Run / pending Turn startup recovery is idempotent and provider-independent
- Product Turn sync failure cannot rewrite authoritative terminal Run outcome
- external Schema materialization and Base Wiki build blocked during nonterminal Runs; Agent-owned L3S7 evolution preserved
- meaningful Timeline artifacts resolved from authoritative same-run research records, never raw runtime diagnostic text

Evidence tests: tests/api/test_freeze_run_durability.py; tests/api/test_freeze_runtime_integration.py; tests/api/test_freeze_schema_lifecycle.py; tests/api/test_product_core_recovery.py; tests/api/test_research_artifact_timeline.py; tests/api/test_workspace_api.py; tests/product/test_answer_citation_projection.py; tests/product/test_research_command_guards.py.

DTO cleanup: src/transit_scholar/api/schemas.py was removed after zero-caller/import-source verification in the prior closure; api/schemas/ remains the sole DTO source.
Local validation/, doc/, .agentic-sdlc/ and temporary smoke/log artifacts remain untracked; local files are preserved.

Previous freeze attempt / superseded evidence: implementation 6d9970e14a7e84a5e8b0d5ab88511316779058c7 recorded 158/165/169 passed and smoke PASS. Those historical results remain valid for that baseline but are superseded by this closure.

This record is committed as a documentation-only descendant of the tested implementation SHA. No production or test code changed after that implementation was tested. Results are local execution evidence, not remote CI results. Documentation-only descendants do not require rerunning the gates.

Remaining nonblocking debt: DOI global settings; CWD-relative PDF staging.

Freeze definition: existing /api/v1 resource model, lifecycle semantics, error contract, and run-control semantics are frozen. Subsequent UI work permits backward-compatible extensions and explicit bug fixes only; no API redesign.
