# TransitScholar API Layer v1 Freeze Record

status: Frozen

API prefix: `/api/v1`

tested implementation SHA: `3f69b6f2090a0c08ec5ab12044cfe18235ebe406`

tested implementation commit: `fix(api): close final v1 freeze state seams`

starting master SHA: `a08c8e21af128c7fb4d77796892c7742e5a7e944`

pre-closure production implementation baseline SHA: `55d13c1b7e9a89d3c4ed9ad00c68193f506f8165`

date: 2026-09-11

## Required pytest gates

Gate 1: PASS

```text
.venv\Scripts\python.exe -m pytest tests/api tests/product -q --basetemp=.pytest-tmp-api-v2
192 passed
0 failed
0 errors
```

Gate 2: PASS

```text
.venv\Scripts\python.exe -m pytest tests/layer3 tests/product tests/api -q --basetemp=.pytest-tmp-api-v2
199 passed
0 failed
0 errors
```

Gate 3: PASS

```text
.venv\Scripts\python.exe -m pytest tests/api tests/product tests/layer1 tests/layer2 tests/layer3 -q --basetemp=.pytest-tmp-api-v2
203 passed
0 failed
0 errors
```

Each gate emitted only the already-known `WorkspaceCreateRequest.schema` Pydantic
shadowing warning.

Additional focused evidence:

- Final three-seam closure regressions: 9 passed, 0 failed, 0 errors.
- Directly affected RunRuntime, Product, Workspace, Schema, Wiki, L3S6, and L3S7
  regressions: 92 passed, 0 failed, 0 errors.

## Real localhost smoke

Status: PASS

The existing comprehensive smoke was rerun against the tested implementation over
real `127.0.0.1` TCP with Uvicorn, FastAPI, Product, SQLite,
`LocalExecutionManager(max_workers=1)`, `RuntimeFactory`, `RunRuntime`,
`MainRuntime`, and `RoleRuntime`. It verified:

- Uvicorn startup and shutdown; health and capabilities.
- Paper PDF import/list/read and Workspace membership.
- Schema 1.0 binding, later Schema 1.1 creation, materialization against the exact
  original `(id, version, hash)`, and Wiki build.
- Conversation, Prompt 202 admission, cooperative pause/resume, same execution,
  completed Turn, canonical citations, and privacy-filtered Timeline.
- Prepared-but-unscheduled startup recovery; terminal Run to pending Turn recovery
  with valid, missing, and corrupt checkpoints; idempotent restart.
- Turn synchronization failure isolation and terminal checkpoint recovery.

The final closure smoke also passed over real localhost TCP/Uvicorn: 7 server
startup/shutdown cycles and 13 HTTP requests. It verified:

- An injected L3S7 auxiliary failure after exact answer `A` leaves the authoritative
  AgentRun and ConversationTurn completed; `run.completed` remains singular,
  `run.failed` remains absent, and only a sanitized lifecycle warning is public.
- A globally deleted Paper cannot be added to an active Workspace; the existing
  member-first global-delete rejection remains intact.
- Escaping Schema LLM unavailable and request/transport failures return the exact
  sanitized 503 `PROVIDER_UNAVAILABLE` envelope.
- Escaping Wiki LLM and embedding-provider unavailable failures return the exact
  sanitized 503 `PROVIDER_UNAVAILABLE` envelope.
- Each provider failure releases the exclusive mutation reservation immediately;
  the next reservation succeeds.
- Schema current pointer/run inventory and existing current Wiki/provenance remain
  byte-for-byte unchanged after provider failure.

No external provider traffic was required; provider and failure seams were injected
deterministically while the production API composition and persistence paths remained
real.

## Newly frozen invariants

- Durable completed terminal intent cannot be overturned by auxiliary L3S7 failure.
- Globally deleted Paper cannot become a member of an active Workspace.
- Paper delete and Workspace membership mutation preserve the
  lifecycle/membership invariant under deterministic concurrency in both orderings.
- Schema materialization provider unavailable maps to 503
  `PROVIDER_UNAVAILABLE`.
- Wiki build provider unavailable maps to 503 `PROVIDER_UNAVAILABLE`.
- Exclusive mutation reservation is released on provider failure.
- Provider failure cannot publish false Schema completion/current state or replace a
  stable current Wiki/provenance.

## Existing frozen contracts retained

- Terminal checkpoint is durable finalization intent for completed, cancelled, and
  terminated outcomes; recovery consumes it before ordinary checkpoint or research
  execution.
- Prompt and external Schema/Wiki mutations share the single process-local admission
  slot; authoritative Workspace guards remain in place.
- Request-owned SQLAlchemy Sessions and worker-owned independent Sessions remain
  separate.
- Pause/resume continues the same AgentRun, ResearchSession, and RoleExecution, with
  committed AgentAction exactly once.
- Prepared-unscheduled admission and terminal Run to pending Turn startup recovery
  remain idempotent and provider-independent.
- Product Turn synchronization failure cannot rewrite authoritative terminal Run
  truth.
- Exact Workspace Schema identity remains bound by `(id, version, hash)` across the
  1.0 to later 1.1 lifecycle.
- Unsafe Schema identity maps to 422; goal-resolver provider availability maps to
  sanitized 503.
- Paper public errors, Timeline projection/privacy, canonical citation references,
  and same-run Evidence ownership remain unchanged.

## Closure evidence map

- `tests/api/test_final_freeze_seams.py`
  - completed terminal intent plus failing L3S7 lifecycle
  - deleted-Paper sequential rejection and existing reverse rule
  - deterministic delete-first and add-first concurrency
  - Schema LLM unavailable/request failure taxonomy and atomic state preservation
  - Wiki LLM/embedding unavailable taxonomy and atomic state preservation
- `tests/api/test_freeze_run_durability.py`
- `tests/api/test_freeze_runtime_integration.py`
- `tests/api/test_freeze_schema_lifecycle.py`
- `tests/api/test_product_core_recovery.py`
- `tests/api/test_research_artifact_timeline.py`
- `tests/product/test_paper_library_guard.py`
- `tests/product/test_runtime_factory_integration.py`
- `tests/integration/test_l3s7_end_to_end_lifecycle.py`
- `tests/test_l3s1_workspace_service.py`
- `tests/test_l3s1_wiki_workspace.py`
- `tests/test_l2s3_production_providers.py`

## Remaining nonblocking P2/P3 debt

- DOI module-global settings.
- CWD-relative PDF staging.
- Capabilities `supported` versus `available` distinction.
- Timeline DTO `dict[str, Any]` precision.
- Pydantic `WorkspaceCreateRequest.schema` shadowing warning.
- `ThreadPoolExecutor` cannot forcibly terminate a hung provider thread.
- Paper child-resource `200 []` versus `404` style consistency.

## Evidence qualification

This record is committed as a documentation-only descendant of the tested
implementation SHA. No production or test code changed after that implementation was
tested. These are local execution results, not remote GitHub CI evidence.

Superseded record: starting master `a08c8e21af128c7fb4d77796892c7742e5a7e944`
bound the preceding implementation `55d13c1b7e9a89d3c4ed9ad00c68193f506f8165`
to 183/190/194 passing tests and a passing localhost smoke. That evidence remains
historically valid for its baseline and is superseded by this closure.

Freeze definition: the existing `/api/v1` resource model, state semantics, error
contract, run-control semantics, and cross-domain lifecycle invariants are frozen.
Subsequent P3 UI work permits backward-compatible extensions and explicit bug fixes
only; no API redesign.
