# TransitScholar API Layer v1

status: Frozen

API prefix: /api/v1

commit SHA: 6d9970e14a7e84a5e8b0d5ab88511316779058c7

date: 2026-09-10

starting master SHA: 58a3fd49950c407833bffe912f63b429047357fe

pytest gate 1: PASS
```text
.venv\Scripts\python.exe -m pytest tests/api tests/product -q --basetemp=.pytest-tmp-api-v2
158 passed
0 failed
0 errors
```

pytest gate 2: PASS
```text
.venv\Scripts\python.exe -m pytest tests/layer3 tests/product tests/api -q --basetemp=.pytest-tmp-api-v2
165 passed
0 failed
0 errors
```

pytest gate 3: PASS
```text
.venv\Scripts\python.exe -m pytest tests/api tests/product tests/layer1 tests/layer2 tests/layer3 -q --basetemp=.pytest-tmp-api-v2
169 passed
0 failed
0 errors
```

additional directly affected regression suites: 84 passed + 32 passed; 0 failed; 0 errors

real API smoke: PASS
- Real localhost TCP / Uvicorn startup and shutdown.
- Paper PDF import/list/read; Schema foo/1.0 Workspace; Paper membership; foo/1.1 creation; old Workspace materialization and Wiki build.
- Health/capabilities; Conversation; Prompt 202; Run and timeline reads; cooperative pause; same-execution resume; completed Turn and final answer.
- LocalExecutionManager -> worker Product -> RuntimeFactory -> RunRuntime -> MainRuntime -> RoleRuntime.
- Deterministic provider/tool injection; no external provider traffic.

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

DTO cleanup: removed src/transit_scholar/api/schemas.py after caller search and import-source verification; api/schemas/ is the sole DTO source.

Record delivery: generated after the repair commit and retained as a working-tree artifact so commit SHA equals the final HEAD.

Freeze definition: existing /api/v1 resource model, lifecycle semantics, error contract, and run-control semantics are frozen. Subsequent UI work permits backward-compatible extensions and explicit bug fixes only.
