# TransitScholar Web UI

The formal local Web UI for TransitScholar: a React + TypeScript + Vite
application that consumes the frozen `/api/v1/*` HTTP API.

The Stage 7 acceptance panel in `src/transit_scholar/web/static/` remains a
separate application. This frontend does not extend it and does not import it.

## Layout

```
ui/
  index.html            Vite entry document (served as the SPA entry)
  vite.config.ts        dev proxy for /api/v1 + production static build
  scripts/
    smoke-backend-states.mjs   renders the built bundle in jsdom to prove the
                               backend-connected and backend-unavailable states
    smoke-workspace-library.mjs drives the built bundle against a running local
                               API for workspace creation and Library membership
    smoke-library-management.mjs renders the built bundle in jsdom against a
                               deterministic API stand-in for extended Library
                               management (metadata correction, duplicates,
                               bibliography, deletion/restore, enrichment)
    smoke-workspace-knowledge.mjs renders the built bundle in jsdom against a
                               deterministic API stand-in for the richer
                               Workspace knowledge UI (Paper Schema readiness and
                               materialization, Wiki page/entry detail, Wiki
                               search modes)
  src/
    api/                the only place that performs HTTP transport
      client.ts         ApiClient, ApiError envelope handling, failure classes
      types.ts          DTO types mirroring the frozen backend contract
      endpoints/        one typed module per product resource
    app/                shell: navigation, routing, backend status, workspace context
    components/         shared primitives (buttons, badges, states, capability panel)
    features/           one directory per product area
    hooks/              small data-loading hook exposing explicit state
    lib/                presentation-only formatting helpers
```

## Commands

Run all commands from `ui/`.

| Command | Purpose |
| --- | --- |
| `npm install` | Install dependencies |
| `npm run build` | Type-check and build the production bundle into `ui/dist` |
| `npm run typecheck` | Type-check only |
| `npm run smoke:states` | Render the built bundle in jsdom and assert both backend connection states |
| `npm run smoke:workspace-library` | Drive the built bundle against a **running** local API: create workspaces, import a PDF, and change workspace membership |
| `npm run smoke:core-flow` | Fresh-session core research flow against a **running** local API: workspace → PDF import → paper membership → Conversation → live run and Timeline → pause/resume → final answer → citation → Wiki |
| `npm run smoke:product-e2e` | Real end-to-end local product smoke (T-010): fresh session with a real paper PDF against a **running** local app — Schema-bound Workspace, real import, real local evidence parse, live AgentRun Timeline, final answer, citation PDF, persisted reload, Wiki |
| `npm run smoke:library-management` | Extended Library management (T-006) in jsdom against a deterministic API stand-in: metadata correction, duplicate adjudication, bibliography, confirmed Library deletion/restore, enrichment |
| `npm run smoke:workspace-knowledge` | Richer Workspace knowledge (T-007) in jsdom against a deterministic API stand-in: per-Paper Schema readiness, Schema materialization (including the workspace-busy conflict), Wiki page and Agent Learned entry detail, and every reported Wiki search mode |
| `npm run smoke:ux` | UX polish (T-009) in jsdom against a deterministic API stand-in: shortcut reference and focus restoration, `g` + section chords, `/` focus, Ctrl+Enter submit, citation arrow/previous/next navigation, and the page-specific PDF hint |
| `npm run verify` | `npm run build` followed by the offline `smoke:states`, `smoke:research`, `smoke:ux`, `smoke:library-management`, and `smoke:workspace-knowledge` harnesses |
| `npm run dev` | Vite dev server on port 5173 with `/api/v1` proxied to FastAPI |
| `npm run preview` | Serve the production build with the same `/api/v1` proxy |

## Two supported ways to run

### 1. Production (single origin, one server)

```powershell
cd ui
npm run build
cd ..
.venv\Scripts\python.exe -m uvicorn transit_scholar.api.app:create_app --factory --port 8000
```

`transit_scholar.api.app` serves `ui/dist` as static assets from the same origin
that answers `/api/v1/health`:

* `/` and every client-side route return `ui/dist/index.html` (SPA fallback)
* `/assets/*` serves the hashed build assets with correct web content types
* `/api/v1/*` is untouched, and unknown API paths still return JSON errors

If the build is missing, `/` returns HTTP 503 with the machine-readable code
`FRONTEND_BUILD_MISSING` instead of failing at startup. The build directory can
be overridden with `TRANSIT_SCHOLAR_UI_DIST`.

### 2. Local development (Vite dev server)

```powershell
# terminal 1 — the API
.venv\Scripts\python.exe -m uvicorn transit_scholar.api.app:create_app --factory --port 8000

# terminal 2 — the UI
cd ui
npm run dev
```

Vite proxies `/api/v1` to `http://127.0.0.1:8000` (override with
`TRANSIT_SCHOLAR_API_TARGET`), so the browser still talks to a single origin.
No Node server is used in production.

## The API boundary

Every `/api/v1/*` call goes through `src/api`. Views never call `fetch`
directly, never cache backend state, and never re-implement backend rules.

* `ApiError` normalises both API error envelopes (`{error: {code, message,
  details}}`) and transport failures. A transport failure is reported as
  `BACKEND_UNAVAILABLE` with status `0`.
* `classifyApiFailure` maps a failure to `unavailable`,
  `unavailable_capability`, `validation`, `conflict`, `not_found`, or `error`,
  which drives the user-visible state.
* `VITE_API_BASE_URL` overrides the API origin. The default (empty) keeps the
  same-origin production model.

## Backend connection states

`BackendStatusProvider` probes `GET /api/v1/health` (and
`GET /api/v1/capabilities`) on load, on window focus, and every 30 seconds.

* `Backend connected` — health returned successfully
* `Backend unavailable` — the health probe failed for any reason; a banner with
  a Retry action is shown and the capability panel reports unknown capabilities
* `Checking backend` — probe in flight

The capability panel always lists every capability from
`/api/v1/capabilities` and marks unavailable ones explicitly. A capability that
is simply absent (for example Schema Wiki on a workspace without a Schema) is
presented as a normal product state, not an application error.

Because production serves the UI from FastAPI, the interface itself is
unreachable if that server is down. To exercise the unavailable state in a
browser, use the Vite dev server (`npm run dev`) with the API stopped, or point
`VITE_API_BASE_URL` at an origin that is not listening.

## Product surfaces

Contributions in this iteration cover the first research-path steps:

* **Workspaces** (`src/features/workspaces/`) — list, open, and create
  workspaces. Creation supports *without a Schema* or *bound to one existing
  Schema definition/version*; the permanent-binding warning is shown before the
  create action. Workspace settings display the API-reported status and the
  Schema binding as read-only information: no control offers to switch an
  existing workspace to another Schema or version. The settings view also adds
  and removes workspace paper membership.
* **Library** (`src/features/library/`) — the global paper list, PDF import
  through `POST /api/v1/papers/import` (bounded by the
  `/api/v1/capabilities` upload limit), paper detail with identity, abstract,
  readiness/status and the registered local PDF, and add/remove workspace
  membership. Diagnostic record fields stay behind a disclosure.
* **Wiki** (`src/features/wiki/`) — the Workspace knowledge view in two
  clearly separated forms: *Schema Wiki* content read from the bound Schema, and
  *Agent Learned* entries promoted from research runs. Both sources are searchable
  together, every hit is tagged with its source, and each Wiki page, topic, and
  entry has its own detail view fed only by structured API responses.
* **Workspace knowledge readiness** (`src/features/workspaces/`) — Workspace
  settings and the Schema Wiki tab expose each member paper's API-reported Schema
  readiness (`ready` / not materialized / not used) and request Schema
  materialization through
  `POST /api/v1/workspaces/{id}/papers/{paper_id}/schema/materialize` only for a
  paper the API reports as not materialized. A workspace-busy rejection is shown
  with its API code and status rather than being silently retried.

Removing a paper from a workspace and deleting it from the global Library stay
separate actions (CON-009). A workspace created without a Schema has no Schema
Wiki and no paper Schema materialization; both are presented as normal product
states, never as application errors.

### UX polish (T-009)

The polish iteration changes presentation and interaction only; it does not
change backend semantics or add a second source of product state.

* **Responsive layout** — the section navigation collapses into a compact
  horizontal strip and the Research layout stacks on narrow windows; the
  conversation list becomes sticky on wide ones; long dialogs scroll their own
  body; the Library/Wiki detail grids collapse to one column on small screens.
* **Visible states** — failures carry a short "what to do next" line derived
  from the API failure class (unavailable, unavailable capability, validation,
  conflict, not found) while the API code and message stay authoritative.
  Loading, empty, disabled, and busy controls remain explicit; dialogs announce
  state changes with `aria-live` and trap/restore keyboard focus.
* **Citation ergonomics** — arrow keys move between the citation references,
  the detail dialog steps Previous/Next without closing, and the local PDF opens
  in the browser viewer. The plain file-content URL stays the default target; a
  citation with a page locator also offers `#page=N` as a viewer hint.
* **Keyboard shortcuts** — `?` opens the shortcut reference, `/` focuses the
  active view's main input, `Ctrl`/`Cmd` + `Enter` sends the research prompt, and
  `g` followed by `w`/`r`/`l`/`k`/`s` moves between product sections. Shortcuts
  are ignored while typing and never mutate Workspace, Schema, Paper, or
  AgentRun state.

## Verification

```powershell
cd ui
npm run verify          # production build + backend-state DOM smoke
cd ..
.venv\Scripts\python.exe -m pytest -q tests/ui tests/api
```

`npm run smoke:states` renders the real production bundle twice inside jsdom —
once with a failing `fetch` and once with a healthy API — and asserts that the
shell reports `Backend unavailable` and `Backend connected` respectively.

`npm run smoke:workspace-library` renders the real production bundle once and
drives it against a running local API (default `http://127.0.0.1:8017`, override
with `--base` or `TRANSIT_SCHOLAR_SMOKE_API_BASE`). It creates a no-Schema and a
Schema-bound workspace, checks the permanent-binding warning and the read-only
binding, imports `tests/fixtures/metadata/causal_reinforcement_learning_train_scheduling.pdf`
through the Library, and adds then removes that paper's workspace membership.
Start the API first, then run:

```powershell
.venv\Scripts\python.exe -c "import uvicorn; from transit_scholar.api.app import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=8017)"
cd ui
npm run build
npm run smoke:workspace-library
```

`npm run smoke:core-flow` is the UI-S1 integration gate. It drives the built
bundle in a **fresh** jsdom session (empty browser storage) through the complete
primary research path against a running local API: create a Schema-bound and a
no-Schema workspace, import a PDF, add the paper to the open workspace, create a
Conversation, submit a prompt, watch the Run status and Timeline while the Agent
works, request pause and resume, read the final answer with the Timeline
collapsed, inspect an answer citation and open its local PDF, and open both the
no-Schema and the Schema-bound workspace Wiki. The harness fails if the UI ever
issues a request outside `/api/v1/*`, so the flow is proven to need no direct
Product/Core invocation. When the scripted integration server passes
`--release-file <path>`, the harness writes that file after requesting pause, so
the pause/resume window is deterministic instead of timing-based.

The automated version of the same gate lives in
`tests/ui/test_ui_s1_core_flow_gate.py`: it boots the real API and product
runtime on a loopback origin that also serves `ui/dist`, runs the smoke, and
independently checks that the cited evidence resolves to the imported Paper.

```powershell
.venv\Scripts\python.exe -m pytest -q tests/ui/test_ui_s1_core_flow_gate.py
```

`npm run smoke:product-e2e` is the **real end-to-end local product smoke**
(T-010). It drives the built bundle in a fresh jsdom session with a **real paper
PDF** against a running local application and exercises the real production
paths only: Schema-bound Workspace creation, real PDF import through the
Library and its registered local PDF, Workspace membership, the local Layer2
evidence parse + retrieval index for that Paper, a Conversation with a real
research prompt, the live AgentRun Timeline while the run executes, the final
answer with the Timeline collapsed but reopenable, an answer citation whose
`Open PDF` serves the cited local PDF, a reload of the persisted answer from the
API, and the workspace Wiki (status, Base and Agent Learned sources, search).
Every request must stay on `/api/v1/*`.

Local evidence preparation is a product capability with no API trigger, so the
harness writes a handshake file (`--prepare-request`) once the Paper is a
Workspace member and waits for `--prepare-ready` before asking the research
question. `tests/ui/test_product_e2e_smoke.py` boots the **real production
composition** (`ApiRuntimeContext` → `build_local_product` → `RuntimeFactory`
with no injected coordinator, role policy, retrieval planner or synthesis),
performs that preparation with the offline `pymupdf_native` parser and BM25
index build, runs the smoke, and then verifies in the repository that the
AgentRun completed, that the run scope contains only the production services,
that the cited evidence is persisted against the imported Paper with a page
locator and a real excerpt, and that the displayed final answer is the text the
model itself authored in the persisted final-synthesis Role execution.

The gate runs the **non-fake LLM client** for the configured provider. The
provider configured on this workstation (`https://opencode.ai/zen/go/v1`)
rejects requests that do not carry its `x-opencode-session` routing header, so
the gate points the supported `TRANSIT_SCHOLAR_LLM_BASE_URL` knob at
`scripts/product_smoke_llm_bridge.py`: a transparent loopback bridge that
forwards every request body to the configured provider unchanged, adds only that
routing header, and returns the provider's own status and body. The bridge runs
as a separate process, so it is the only component that leaves loopback, and it
records one JSONL line per forwarded request; the gate asserts that every Role
prompt template the completed AgentRun used was answered `200` by the real
provider. The bridge cannot fabricate, rewrite, or replace a model request or
response, and no deterministic run coordinator, role policy, retrieval planner,
reranker, or final-answer composer takes part in the run.

## Notes

* `--configLoader native` in the npm scripts is required in restricted Windows
  shells: Vite's default config bundling probes network drive mappings by
  spawning `net use`, which such shells block (`spawn EPERM`). The native loader
  loads the identical config through Node's TypeScript support.
* Node.js 22.18+ (or 24+) is required for the native config loader.
