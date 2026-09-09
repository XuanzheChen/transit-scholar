# API Layer v2 freeze validation — 2026-09-09

## Scope and provenance

Base: `1259863a33a01e328d8253ac20f07ba701a82f61` (`fix API Layer`).
`git fetch origin master` confirmed HEAD and origin/master match before edits.
Evidence covers this base plus the accompanying working-tree changes. This is
local execution evidence, **not a claim that GitHub Actions/check runs passed**.
No commit, push, or remote CI dispatch was performed.

`summary.json` records a digest of the tested Python source/test tree. Each
`gate-N.json` is derived from the actual pytest JUnit output and retains the
command, timestamp, duration, individual test names and outcomes. Machine host
names and absolute workspace paths are excluded from those records.

## A. Blocker closure

- Closed: Workspace Schema disabled/missing/binding mismatch => HTTP 409.
- Closed: Wiki unsupported/missing/stale/corrupt/empty membership => HTTP 409.
- Closed: resource absence remains 404; inactive/busy Workspace remains 409.
- Closed: semantic Workspace input => 422; unclassified domain error => sanitized 500.
- Closed: Role resume clears terminal metadata and emits one pause trace event.
- Verified: earlier default-profile Role continuation, Main usage accounting,
  Paper sanitization, resume injection, Conversation sanitization and manager
  lifecycle regressions remain green in the gates.

## B. Production changes

| File | Change | Reason |
|---|---|---|
| `src/transit_scholar/api/errors.py` | Shared Workspace/Schema/Wiki status mapping | Prevent router drift and accidental 400 state conflicts |
| `src/transit_scholar/api/routers/workspaces.py` | Apply mapping; sanitize unknown 500 message | Preserve public error codes/envelope with correct status |
| `src/transit_scholar/api/routers/wiki.py` | Apply mapping; sanitize unknown 500 message | Distinguish existing-but-unusable Wiki state from missing identity |
| `src/transit_scholar/layer3/agent/models.py` | Clear ended_at, termination_reason, failure_message on start | Resumed running checkpoint must not retain paused terminal metadata |
| `src/transit_scholar/layer3/runtime/role_runtime.py` | Keep only boundary-specific pause event; include reason | Avoid duplicate pause events while retaining durable checkpoint and trace context |

No ExecutionManager production changes. Status-returning read DTOs (e.g. an
unsupported Wiki overview) remain structured 200 responses; rejected operations
use the new conflict mapping. Existing router-specific machine error codes are
preserved rather than renamed.

## C. Tests

`tests/api/test_workspace_wiki_error_taxonomy.py` adds 27 cases:

- Schema materialize: three Schema conflicts, immutable binding, inactive state,
  three absent-resource cases, semantic input, and unknown internal failure.
- Wiki build and pages: all five Wiki state conflicts, absent/inactive Workspace,
  and unknown internal failure.
- Actual no-schema Workspace creation followed by Wiki build rejection.

Injected lower-layer failures assert one call, exact HTTP status and complete
error envelope. Internal-error cases inject a secret/password/private path and
assert sanitized output.

`tests/layer3/test_freeze_pause_accounting.py` strengthens all four existing
boundary/direct-vs-Main cases: one pause trace with boundary and reason, clean
persisted running metadata at role.resume, and preserved original started_at.
Existing same-execution, one-provider-call, [A,B], default budget and usage
assertions remain in place.

Targeted command:

```powershell
.venv/Scripts/python.exe -m pytest tests/api/test_workspace_wiki_error_taxonomy.py tests/api/test_workspace_api.py tests/api/test_wiki_api.py tests/layer3/test_freeze_pause_accounting.py tests/test_l3s5_trace_role_runtime.py tests/test_l3s5_recovery_role_runtime.py -q --basetemp=.pytest-tmp-api-freeze
```

Result: **50 passed**, 0 failed/errors/skipped.

## D. Gates

| Gate | Selection | Passed | Failed | Errors | Skipped |
|---|---|---:|---:|---:|---:|
| 1 | tests/api tests/product | 135 | 0 | 0 | 0 |
| 2 | tests/layer3 tests/product tests/api | 142 | 0 | 0 | 0 |
| 3 | tests/api tests/product tests/layer1 tests/layer2 tests/layer3 | 146 | 0 | 0 | 0 |

All use the repository `.venv/Scripts/python.exe`, `-q`, and
`--basetemp=.pytest-tmp-api-freeze`; commands including JUnit destinations are
in the JSON records. All gates report the existing Pydantic `schema` field
shadowing warning. These are the requested gate selections, not a claim to have
run every test at the repository root.

## E. Real API smoke

See `smoke.json` for all **21** actual calls and status codes. Transport was a
real Uvicorn server on an ephemeral 127.0.0.1 port with an HTTP client, not ASGI
TestClient. SQLite, Product scopes, ingestion and LocalExecutionManager were
real; a controlled fake runtime enabled deterministic pause/resume.

- Startup, health and capabilities succeeded.
- Real generated PDF import => 201; Paper list/detail => 200.
- Workspace create => 201; add Paper => 200; Conversation create => 201.
- No-schema Wiki build => 409 WIKI_UNSUPPORTED.
- No-schema Schema materialize => 409 SCHEMA_DISABLED.
- Prompt => 202; Run/Timeline reads => 200; pause => 200 then paused.
- Unavailable runtime: Prompt and resume => 503 PROVIDER_UNAVAILABLE.
- Available resume => 202, then the same Run completed.
- Turn and Conversation reads => 200.
- Paper, Workspace and Conversation rows were verified in the same owned DB.
- Responses were checked for local temp-root/internal-diagnostic leakage.
- Shutdown completed in approximately 0.171 seconds; closed=true, busy=false.

The temporary harness is `temp/api_freeze_smoke.py` (hash recorded in summary).
It was run after all three gates; it does not contact an external model provider.

## F. Risk audit and remaining P2 work

- Role pause continuation and usage: default max_steps=1/max_llm_calls=1 cases
  remain green, no repeated provider/action or cumulative usage double count.
- Global SessionLocal: formal Paper/Wiki composition regressions remain green;
  legacy fallback declarations remain. No DB composition changes in this patch.
- Global settings: DOI provider/network settings ownership remains P2; current
  capabilities do not promise per-instance DOI network control.
- PDF staging still uses CWD-relative temp/api_uploads: P2, not changed in this
  bounded taxonomy round; an unwritable startup directory remains a limitation.
- Paper detail authors/duplicate_relations retain broad nested dict DTO types:
  P2. Their current lower-layer producer constructs explicit fields; this
  round does not redesign them.
- Raw exceptions: unknown Workspace/Wiki domain errors now return generic 500
  text; existing Paper/Conversation sanitization regressions remain green.
- Resume state: unavailable guard preserves paused state/checkpoint; resumed
  Role terminal metadata is now cleared before persisting role.resume.
- Shutdown ownership: reservation, admission race, uncooperative Future and
  submission failure regressions all remain green; actual smoke shutdown bounded.
- Startup Alembic/global settings alignment remains an existing limitation for
  concurrent multi-app initialization; no such concurrency claim is made here.
- No live external-provider integration or remote CI run is claimed.

Final searches reviewed SessionLocal, settings roots, __dict__, str(exc),
error_message, assistant_response, pause callbacks, current_role_execution_id,
max_steps, usage.llm_calls and usage.tool_calls in formal API/runtime paths.
`git diff --check` passed. Test databases/runtime artifacts remain under ignored
temp paths; `.pytest-tmp-*/` stays ignored. Only intended source/tests and this
validation evidence are included in the working-tree change set.

## G. Final status

**API Layer v2 freeze candidate ready** based on local implementation, all
required gates and real HTTP smoke. Remaining items above are explicit P2 debt.
The evidence must travel with the source changes for repository-side review;
GitHub CI status is a separate, unverified signal.
