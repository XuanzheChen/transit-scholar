# API Layer v2 freeze validation — public error and Schema read closure

## Scope and provenance

Base commit: `0679538b4014ddf3c20046f420b9502124b3a9aa`.
A fresh fetch confirmed HEAD and origin/master match before edits.
This report describes that base plus the accompanying working-tree changes.
It supersedes the previous taxonomy-round evidence; it does not claim that the
base commit itself contains these fixes or that GitHub CI ran.

The adjacent gate JSON files are derived from actual pytest JUnit output and
record commands, timestamps, durations and individual testcase outcomes.
`summary.json` identifies the base and the tested LF-normalized Python source
and test tree digest. No commit or push was performed during this validation.

## A. Blocker closure

- Closed: known Workspace/Schema/Wiki 4xx errors never expose exception text.
  Public messages are selected from a code allowlist, including SCHEMA_MISSING.
- Closed: no-schema Paper Schema GET checks Product membership before entering
  bound-only Schema readiness: member => 200 disabled, nonmember/missing => 404.
- Existing status taxonomy and machine codes are retained. Status-returning
  read DTOs are not converted into exceptions.
- No Schema Core, RoleRuntime, MainRuntime, ExecutionManager or PSC changes.

## B. Production changes

| File | Change | Why |
|---|---|---|
| `src/transit_scholar/api/errors.py` | Code-to-public-message allowlist; fixed messages for typed Product exception handlers | A known 4xx status is not evidence that exception text is safe |
| `src/transit_scholar/api/routers/workspaces.py` | Use public messages; check no-schema membership first | Prevent storage diagnostic leakage and make disabled read behavior reachable |
| `src/transit_scholar/api/routers/wiki.py` | Project Workspace/Schema errors and WikiNotFoundError safely | Missing current.json and wrapped storage exceptions must remain internal |
| `src/transit_scholar/api/routers/schemas.py` | Fixed catalog 404/409/422 messages | Apply the same rule to neighboring catalog exceptions |
| `src/transit_scholar/api/routers/conversations.py` | Fixed exception-backed public messages | Do not leak arbitrary typed Product diagnostics |
| `src/transit_scholar/api/routers/runs.py` | Fixed Run conflict message | Keep exception text out of 409 while retaining structured Run identity |
| `src/transit_scholar/api/routers/papers.py` | Fixed PaperInUse message | Retain paper/workspace IDs in details without exposing exception text |

Lower-layer diagnostics are unchanged; original exceptions remain chained.
Existing safe result-code projections and response shapes are retained.

## C. Tests added/updated

`tests/api/test_schema_read_boundaries.py` adds four real API regressions:

1. No-schema Workspace + imported member Paper => exact 200 disabled DTO.
2. No-schema Workspace + imported nonmember Paper => 404 NOT_FOUND.
3. No-schema Workspace + missing Paper => 404 NOT_FOUND.
4. Create schema catalog entry and bound Workspace, import/add Paper, skip
   materialization, then build Wiki => 409 SCHEMA_MISSING with fixed message.
   The test first verifies the real lower-layer exception contains current.json
   and SchemaCurrentNotFoundError, then proves the endpoint exposes neither,
   including neither native nor slash-normalized project temp paths.

`tests/api/test_workspace_wiki_error_taxonomy.py` now injects secrets/private
paths for ALL statuses, not only 500. It additionally tests Schema errors
propagating through Wiki build/read and catalog/storage 404/409/422 errors.
Calls, exact envelopes, statuses and public messages are asserted.

`tests/api/test_error_mapping.py` checks typed Product errors with secret text
across 404/409/422/413/503. `test_conversations_api.py` migrates an old assertion
that required raw validation text to the fixed public message, with a call
counter and secret injection.

Initial targeted boundary selection: 49 passed, 0 failed/errors/skipped. This
preceded the final broader exception-text cleanup; all affected tests were then
included in the final three gates. An intermediate Gate 1 failure was a stale
raw-validation-message assertion; it was migrated before final gate evidence.

## D. Gate results

Final results and full commands appear in gate-1.json, gate-2.json and gate-3.json.
All use `.venv/Scripts/python.exe`, `-q`,
`--basetemp=.pytest-tmp-api-freeze`, and a JUnit output under ignored `temp/`.
These are the requested selections, not a claim that every repository-root test
was executed. Existing Pydantic `schema` field-shadowing warning remains.

| Gate | passed | failed | errors | skipped |
|---|---:|---:|---:|---:|
| 1 | 148 | 0 | 0 | 0 |
| 2 | 155 | 0 | 0 | 0 |
| 3 | 159 | 0 | 0 | 0 |


## E. Real HTTP smoke

**29 real HTTP calls passed**; shutdown took **0.219 seconds**.

Real Uvicorn on an ephemeral 127.0.0.1 port, with an HTTP client, actual SQLite,
Product scopes, ingestion and LocalExecutionManager; controlled fake runtime
for deterministic pause/resume. The local temporary harness is
`temp/api_freeze_smoke.py`; its hash is in summary.json.

- Startup, health and capabilities succeeded.
- PDF import => 201; Paper list/detail => 200.
- Workspace create => 201; add Paper => 200; Conversation create => 201.
- No-schema Wiki build and Schema materialize => 409.
- NEW: no-schema member Schema GET => 200 disabled.
- NEW: no-schema nonmember and missing-Paper Schema GET => 404.
- NEW: create bound Workspace, add Paper without Schema materialization,
  Wiki build => 409 SCHEMA_MISSING, fixed public message, no current.json,
  SchemaCurrentNotFoundError or temporary-root diagnostic.
- Prompt => 202; Run/Timeline => 200; pause => 200 then paused.
- Unavailable runtime Prompt/resume => 503 PROVIDER_UNAVAILABLE.
- Available resume => 202 then same Run completed; Turn/Conversation reads => 200.
- Paper, Workspace and Conversation rows verified in the same owned database.
- Responses checked for local path/internal diagnostic leakage.
- Shutdown completed with manager closed=true and busy=false.

## F. Static risk audit / remaining P2

Final API search has no `str(exc)` or `__dict__` passthrough. This is not a claim
that all repository occurrences must be removed: lower layers keep diagnostics,
and expected Paper business-result messages retain their existing projection.

Default-profile Role continuation, accounting, resume metadata, single pause
trace, runtime-unavailable admission and manager lifecycle remain unchanged and
covered by the green gates. No new global SessionLocal/data-root fallback was
introduced; existing composition/ownership tests remain in the gate selections.

Remaining P2 debt is unchanged: DOI provider/network global settings ownership;
CWD-relative PDF staging; broad nested Paper authors/duplicate_relations DTOs.
Startup Alembic/global-root alignment is not validated for concurrent multi-app
initialization. Smoke does not call a real external model/provider.

`git diff --check` passed. Test DBs, generated PDFs/runtime state and temporary
scripts remain ignored, and `.pytest-tmp-*/` coverage is unchanged. The intended
change set consists of API source, regressions and these validation records.
GitHub check-run status is separate from this local evidence and is not claimed.

## G. Final status

**API Layer v2 freeze candidate ready** for final review, based on the tested
working tree, all three green gates and real HTTP smoke. Remaining items are
the explicitly listed P2 debt; no remote CI success is claimed.
