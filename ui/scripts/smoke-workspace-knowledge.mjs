/**
 * Offline DOM smoke for the richer Workspace knowledge UI (T-007).
 *
 * Renders the real production bundle (ui/dist) inside jsdom and drives the same
 * user interactions a browser would against a deterministic in-memory stand-in
 * for the frozen `/api/v1/*` API. It verifies the Workspace knowledge behaviors
 * required for this iteration:
 *
 *   1. Workspace/Paper Schema readiness is inspectable: every member paper's
 *      API-reported state (ready / not materialized / not used) is displayed,
 *      both in Workspace settings and beside the Schema Wiki content it feeds;
 *   2. Schema materialization can be requested where the API reports a paper as
 *      not materialized, the API response is shown, and the readiness is re-read
 *      from the API afterwards; a ready paper is never offered materialization;
 *   3. an API conflict on materialization is surfaced as an explicit
 *      API-derived error state instead of a silent failure (AC-014);
 *   4. a Workspace without a Schema binding explains that paper Schema
 *      materialization does not apply as a normal state, not as an error;
 *   5. Wiki page details and Agent Learned entry details render from structured
 *      API data (including provenance references, source papers, and the
 *      superseded-by relationship);
 *   6. Wiki search is usable in every mode the capabilities API reports, and the
 *      requested mode reaches the search endpoint.
 *
 * The harness never calls the Product/Core Python layers and never talks to a
 * backend: every assertion is made on the rendered DOM and on the HTTP requests
 * the bundled UI itself issued to `/api/v1/*`.
 *
 * Usage:
 *   npm run build
 *   node scripts/smoke-workspace-knowledge.mjs [--dist <dir>]
 */
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { JSDOM } from 'jsdom'

/* ------------------------------------------------------------------ args */

const args = process.argv.slice(2)
function argValue(flag, fallback) {
  const index = args.indexOf(flag)
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback
}

const distDir = path.resolve(argValue('--dist', path.join(process.cwd(), 'dist')))
const origin = 'http://transit-scholar.test'

/** Must match ACTIVE_WORKSPACE_STORAGE_KEY in the app. */
const ACTIVE_WORKSPACE_KEY = 'transit-scholar.ui.active-workspace-id'

const W1 = 'W1' // Schema-bound workspace
const W2 = 'W2' // Workspace created without a Schema
const TS = '2024-05-05T10:00:00'

function fail(message) {
  process.stderr.write(`FAIL: ${message}\n`)
  process.exit(1)
}

function pass(message) {
  process.stdout.write(`PASS: ${message}\n`)
}

function assert(condition, message) {
  if (!condition) {
    fail(message)
  }
}

function bundlePath() {
  const assetsDir = path.join(distDir, 'assets')
  const candidates = readdirSync(assetsDir).filter((name) => name.endsWith('.js'))
  if (candidates.length === 0) {
    fail(`no JavaScript bundle found in ${assetsDir}; run "npm run build" first`)
  }
  return path.join(assetsDir, candidates[0])
}

/* ----------------------------------------------- in-memory API stand-in */

const workspace = (id, name, schemaMode) => ({
  workspace_id: id,
  name,
  status: 'active',
  schema_mode: schemaMode,
  schema_binding:
    schemaMode === 'none'
      ? null
      : {
          schema_id: 'generic_research_paper',
          schema_version: '1.0',
          schema_hash: 'a1b2c3d4e5f6a7b8',
        },
  revision: 2,
  created_at: TS,
  updated_at: TS,
})

const paper = (paperId, title) => ({
  paper_id: paperId,
  title,
  publication_year: 2021,
  venue: null,
  doi: null,
  arxiv_id: null,
  status: 'active',
  primary_file_id: null,
  created_at: TS,
  updated_at: TS,
})

const state = {
  /** Capability flag the UI reads before offering semantic search. */
  semanticAvailable: true,
  workspaces: {
    [W1]: workspace(W1, 'Knowledge workspace', 'bound'),
    [W2]: workspace(W2, 'No schema workspace', 'none'),
  },
  members: { [W1]: ['P1', 'P2', 'P4'], [W2]: ['P3'] },
  papers: [
    paper('P1', 'Paper one'),
    paper('P2', 'Paper two'),
    paper('P3', 'Paper three'),
    paper('P4', 'Paper four'),
  ],
  /** API-reported per-Paper Schema readiness, mutated by materialization. */
  paperSchema: {
    [W1]: {
      P1: { status: 'ready', error_code: null },
      P2: { status: 'missing', error_code: 'schema_missing' },
      P4: { status: 'missing', error_code: 'schema_missing' },
    },
    [W2]: {
      P3: { status: 'disabled', error_code: 'schema_disabled' },
    },
  },
  pages: [
    {
      page_id: 'PG1',
      workspace_id: W1,
      paper_id: 'P1',
      title: 'Paper one — Schema Wiki page',
      summary: 'Findings extracted for the bound Schema.\n\nA second paragraph of stored knowledge.',
      schema_id: 'generic_research_paper',
      schema_version: '1.0',
      build_status: 'complete',
      created_at: TS,
      updated_at: TS,
      build_revision: 2,
    },
  ],
  entities: [
    {
      entity_id: 'EN1',
      workspace_id: W1,
      canonical_name: 'Retrieval augmented generation',
      aliases: ['RAG'],
      description: 'A method that combines retrieval with generation.',
      kind: 'method',
      created_at: TS,
      updated_at: TS,
    },
  ],
  entries: [
    {
      entry_id: 'E1',
      workspace_id: W1,
      title: 'Agent learned: transit priority',
      content: 'Transit signal priority reduces intersection delay.\n\nRecorded from this workspace.',
      source_claim_ids: ['claim-1', 'claim-2'],
      evidence_refs: ['ev-1'],
      provenance_refs: ['prov-1'],
      paper_ids: ['P1'],
      originating_agent_run_id: 'RUN-9',
      status: 'active',
      superseded_by: null,
      created_at: TS,
      updated_at: TS,
    },
    {
      entry_id: 'E2',
      workspace_id: W1,
      title: 'Agent learned: earlier transit note',
      content: 'An earlier note that a later entry replaced.',
      source_claim_ids: [],
      evidence_refs: [],
      provenance_refs: [],
      paper_ids: [],
      originating_agent_run_id: 'RUN-8',
      status: 'superseded',
      superseded_by: 'E1',
      created_at: TS,
      updated_at: TS,
    },
  ],
}

const requests = []

function record(method, url, body) {
  requests.push({
    method,
    path: url.pathname,
    query: Object.fromEntries(url.searchParams.entries()),
    body,
  })
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function errorEnvelope(status, code, message) {
  return json({ error: { code, message, details: {} } }, status)
}

/** Any request the stand-in does not model is a harness defect, not a UI bug. */
function notFound(method, pathname) {
  fail(`the UI issued an unmodelled API request: ${method} ${pathname}`)
}

function wikiOverview(workspaceId) {
  if (workspaceId === W1) {
    return {
      workspace_id: W1,
      base_wiki: {
        workspace_id: W1,
        status: 'ready',
        manifest_status: 'complete',
        fingerprint: 'fingerprint-1',
        recorded_fingerprint: 'fingerprint-1',
        build_revision: 2,
        built_at: TS,
        error_code: null,
      },
      base_wiki_capability: { build_supported: true, read_supported: true, reason: null },
      agentic_wiki_entry_count: 2,
    }
  }
  return {
    workspace_id: W2,
    base_wiki: {
      workspace_id: W2,
      status: 'unsupported',
      manifest_status: null,
      fingerprint: null,
      recorded_fingerprint: null,
      build_revision: null,
      built_at: null,
      error_code: null,
    },
    base_wiki_capability: {
      build_supported: false,
      read_supported: false,
      reason: 'unsupported_schema_mode_or_empty_membership',
    },
    agentic_wiki_entry_count: 0,
  }
}

function searchResult(mode) {
  return {
    status: 'ok',
    hits: [
      {
        type: 'page',
        object_id: 'PG1',
        title: 'Paper one — Schema Wiki page',
        score: 1.5,
        snippet: 'Findings extracted for the bound Schema.',
        retrieval_mode: mode,
        source_kind: 'base_wiki',
        lifecycle_status: null,
        source_score: 1.5,
        local_rank: 1,
        fusion_score: 1.5,
      },
      {
        type: 'entry',
        object_id: 'E1',
        title: 'Agent learned: transit priority',
        score: 1.2,
        snippet: 'Transit signal priority reduces intersection delay.',
        retrieval_mode: mode,
        source_kind: 'agentic_wiki',
        lifecycle_status: 'active',
        source_score: 1.2,
        local_rank: 2,
        fusion_score: 1.2,
      },
    ],
    error_code: null,
    source_status: { base_wiki: 'ok', agentic_wiki: 'ok' },
    source_errors: { base_wiki: null, agentic_wiki: null },
  }
}

function handle(method, url, _body) {
  const pathname = url.pathname

  // ---- system
  if (method === 'GET' && pathname === '/api/v1/health') {
    return json({ status: 'ok' })
  }
  if (method === 'GET' && pathname === '/api/v1/capabilities') {
    return json({
      pause_resume: true,
      user_schema_creation: false,
      base_wiki: true,
      agentic_wiki: true,
      semantic_wiki_search: state.semanticAvailable,
      pdf_upload_max_bytes: 10485760,
    })
  }

  // ---- workspaces
  const workspaceMatch = /^\/api\/v1\/workspaces\/([^/]+)$/.exec(pathname)
  if (method === 'GET' && workspaceMatch) {
    const record_ = state.workspaces[decodeURIComponent(workspaceMatch[1])]
    return record_ ? json(record_) : errorEnvelope(404, 'NOT_FOUND', 'Unknown workspace')
  }
  if (method === 'GET' && pathname === '/api/v1/workspaces') {
    return json({ items: Object.values(state.workspaces) })
  }
  const papersMatch = /^\/api\/v1\/workspaces\/([^/]+)\/papers$/.exec(pathname)
  if (method === 'GET' && papersMatch) {
    const workspaceId = decodeURIComponent(papersMatch[1])
    return json({
      items: (state.members[workspaceId] ?? []).map((paperId) => ({
        workspace_id: workspaceId,
        paper_id: paperId,
        created_at: null,
        already_member: false,
      })),
    })
  }
  const paperSchemaMatch = /^\/api\/v1\/workspaces\/([^/]+)\/papers\/([^/]+)\/schema$/.exec(pathname)
  if (method === 'GET' && paperSchemaMatch) {
    const workspaceId = decodeURIComponent(paperSchemaMatch[1])
    const paperId = decodeURIComponent(paperSchemaMatch[2])
    const readiness = state.paperSchema[workspaceId]?.[paperId]
    if (!readiness) {
      return errorEnvelope(404, 'NOT_FOUND', 'Paper is not a workspace member')
    }
    return json({ workspace_id: workspaceId, paper_id: paperId, ...readiness })
  }
  const materializeMatch =
    /^\/api\/v1\/workspaces\/([^/]+)\/papers\/([^/]+)\/schema\/materialize$/.exec(pathname)
  if (method === 'POST' && materializeMatch) {
    const workspaceId = decodeURIComponent(materializeMatch[1])
    const paperId = decodeURIComponent(materializeMatch[2])
    // Model the real API's exclusive-mutation guard for one paper.
    if (workspaceId === W1 && paperId === 'P4') {
      return errorEnvelope(409, 'WORKSPACE_BUSY', 'Workspace has a non-terminal AgentRun')
    }
    state.paperSchema[workspaceId][paperId] = { status: 'ready', error_code: null }
    return json({
      workspace_id: workspaceId,
      paper_id: paperId,
      run_id: `run-${paperId}`,
      status: 'materialized',
    })
  }

  // ---- papers
  if (method === 'GET' && pathname === '/api/v1/papers') {
    return json({ items: state.papers })
  }
  const paperMatch = /^\/api\/v1\/papers\/([^/]+)$/.exec(pathname)
  if (method === 'GET' && paperMatch) {
    const paperId = decodeURIComponent(paperMatch[1])
    const record_ = state.papers.find((item) => item.paper_id === paperId)
    return record_ ? json(record_) : errorEnvelope(404, 'NOT_FOUND', 'Unknown paper')
  }

  // ---- wiki
  const overviewMatch = /^\/api\/v1\/workspaces\/([^/]+)\/wiki$/.exec(pathname)
  if (method === 'GET' && overviewMatch) {
    return json(wikiOverview(decodeURIComponent(overviewMatch[1])))
  }
  const pagesMatch = /^\/api\/v1\/workspaces\/([^/]+)\/wiki\/pages$/.exec(pathname)
  if (method === 'GET' && pagesMatch) {
    const workspaceId = decodeURIComponent(pagesMatch[1])
    return json({ items: workspaceId === W1 ? state.pages : [] })
  }
  const pageMatch = /^\/api\/v1\/workspaces\/([^/]+)\/wiki\/pages\/([^/]+)$/.exec(pathname)
  if (method === 'GET' && pageMatch) {
    const pageId = decodeURIComponent(pageMatch[2])
    const record_ = state.pages.find((item) => item.page_id === pageId)
    return record_ ? json(record_) : errorEnvelope(404, 'NOT_FOUND', 'Unknown wiki page')
  }
  const entitiesMatch = /^\/api\/v1\/workspaces\/([^/]+)\/wiki\/entities$/.exec(pathname)
  if (method === 'GET' && entitiesMatch) {
    const workspaceId = decodeURIComponent(entitiesMatch[1])
    return json({ items: workspaceId === W1 ? state.entities : [] })
  }
  const entityMatch = /^\/api\/v1\/workspaces\/([^/]+)\/wiki\/entities\/([^/]+)$/.exec(pathname)
  if (method === 'GET' && entityMatch) {
    const entityId = decodeURIComponent(entityMatch[2])
    const record_ = state.entities.find((item) => item.entity_id === entityId)
    return record_ ? json(record_) : errorEnvelope(404, 'NOT_FOUND', 'Unknown wiki topic')
  }
  const entriesMatch = /^\/api\/v1\/workspaces\/([^/]+)\/wiki\/agentic-entries$/.exec(pathname)
  if (method === 'GET' && entriesMatch) {
    const workspaceId = decodeURIComponent(entriesMatch[1])
    return json({ items: workspaceId === W1 ? state.entries : [] })
  }
  const entryMatch = /^\/api\/v1\/workspaces\/([^/]+)\/wiki\/agentic-entries\/([^/]+)$/.exec(pathname)
  if (method === 'GET' && entryMatch) {
    const entryId = decodeURIComponent(entryMatch[2])
    const record_ = state.entries.find((item) => item.entry_id === entryId)
    return record_ ? json(record_) : errorEnvelope(404, 'NOT_FOUND', 'Unknown agent learned entry')
  }
  const searchMatch = /^\/api\/v1\/workspaces\/([^/]+)\/wiki\/search$/.exec(pathname)
  if (method === 'GET' && searchMatch) {
    const mode = url.searchParams.get('mode') ?? 'lexical'
    const query = url.searchParams.get('query') ?? ''
    assert(query.length > 0, 'the UI submitted a Wiki search without a term')
    return json(searchResult(mode))
  }

  return notFound(method, pathname)
}

/* ------------------------------------------------------------ DOM plumbing */

const savedGlobals = new Map()

function applyGlobals(window, fetchImpl) {
  const values = {
    window,
    document: window.document,
    localStorage: window.localStorage,
    location: window.location,
    history: window.history,
    FormData: window.FormData,
    HTMLElement: window.HTMLElement,
    HTMLAnchorElement: window.HTMLAnchorElement,
    HTMLButtonElement: window.HTMLButtonElement,
    HTMLInputElement: window.HTMLInputElement,
    HTMLSelectElement: window.HTMLSelectElement,
    Element: window.Element,
    Node: window.Node,
    DocumentFragment: window.DocumentFragment,
    SVGElement: window.SVGElement,
    Event: window.Event,
    CustomEvent: window.CustomEvent,
    MouseEvent: window.MouseEvent,
    KeyboardEvent: window.KeyboardEvent,
    MutationObserver: window.MutationObserver,
    getComputedStyle: window.getComputedStyle.bind(window),
    requestAnimationFrame: window.requestAnimationFrame.bind(window),
    cancelAnimationFrame: window.cancelAnimationFrame.bind(window),
    fetch: fetchImpl,
  }
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined) {
      continue
    }
    if (!savedGlobals.has(key)) {
      savedGlobals.set(key, Object.getOwnPropertyDescriptor(globalThis, key))
    }
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
  }
}

function restoreGlobals() {
  for (const [key, descriptor] of savedGlobals) {
    if (descriptor) {
      Object.defineProperty(globalThis, key, descriptor)
    } else {
      delete globalThis[key]
    }
  }
  savedGlobals.clear()
}

function createFetchShim() {
  return async function shimFetch(input, init = {}) {
    const url = new URL(String(input), origin)
    const method = (init.method ?? 'GET').toUpperCase()
    let body = init.body
    if (typeof body === 'string') {
      try {
        body = JSON.parse(body)
      } catch {
        body = undefined
      }
    }
    if (!url.pathname.startsWith('/api/v1/')) {
      fail(`the UI issued a non-API request: ${method} ${url.pathname}`)
    }
    record(method, url, body)
    try {
      return handle(method, url, body)
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('FAIL:')) {
        throw error
      }
      return errorEnvelope(500, 'SMOKE_STANDIN_ERROR', `stand-in route failed: ${error}`)
    }
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function waitFor(label, predicate, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    let result = false
    try {
      result = predicate()
    } catch {
      result = false
    }
    if (result) {
      return result
    }
    if (Date.now() > deadline) {
      fail(`timed out after ${timeoutMs}ms waiting for ${label}`)
    }
    await sleep(25)
  }
}

function makeDomHelpers(window) {
  const document = window.document
  const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`)
  const text = () => document.body.textContent ?? ''
  const allTestIds = (id) => Array.from(document.querySelectorAll(`[data-testid="${id}"]`))

  function requireTestId(id) {
    const element = byTestId(id)
    if (!element) {
      fail(`expected element [data-testid="${id}"] was not rendered`)
    }
    return element
  }

  function click(element) {
    if (!element) {
      fail('attempted to click a missing element')
    }
    element.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true, view: window }))
  }

  function clickTestId(id) {
    click(requireTestId(id))
  }

  function setInputValue(input, value) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(input, value)
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
    input.dispatchEvent(new window.Event('change', { bubbles: true }))
  }

  function selectRadio(input) {
    // A native click keeps the radio's activation behaviour, which is how a
    // real browser drives a controlled radio control in React.
    input.click()
  }

  function submitForm(form) {
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
  }

  function applicationError() {
    return document.querySelector('.state-block--error')
  }

  function assertNoApplicationError(label) {
    const block = applicationError()
    if (block) {
      fail(`${label} showed an application error: ${(block.textContent ?? '').trim()}`)
    }
  }

  return {
    window,
    document,
    byTestId,
    allTestIds,
    text,
    requireTestId,
    click,
    clickTestId,
    setInputValue,
    selectRadio,
    submitForm,
    applicationError,
    assertNoApplicationError,
  }
}

function findRequests(method, pathname) {
  return requests.filter((request) => request.method === method && request.path === pathname)
}

function badgeText(paperId) {
  const element = globalThis.document.querySelector(`[data-testid="paper-schema-status-${paperId}"]`)
  return (element?.textContent ?? '').trim()
}

/* ------------------------------------------------------------------- setup */

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-knowledge-smoke-'))
let scenarioCount = 0

/** Open the real built app in a fresh jsdom window and run one scenario. */
async function withApp({ path: initialPath, activeWorkspaceId }, run) {
  scenarioCount += 1
  const dom = new JSDOM(html, { url: `${origin}${initialPath}`, pretendToBeVisual: true })
  const { window } = dom
  if (activeWorkspaceId) {
    window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, activeWorkspaceId)
  }
  applyGlobals(window, createFetchShim())
  const dom_ = makeDomHelpers(window)

  const target = path.join(scratch, `bundle-${scenarioCount}.mjs`)
  copyFileSync(bundleFile, target)

  try {
    await import(pathToFileURL(target).href)
    await waitFor('the application shell to load', () => dom_.text().includes('TransitScholar'), 15000)
    if (dom_.text().includes('Backend unavailable')) {
      fail('the UI reported the backend as unavailable')
    }
    await run(window, dom_)
  } finally {
    restoreGlobals()
    window.close()
  }
}

/** Submit the Wiki search form with a term and return the search request. */
async function searchThroughUi(dom_, term) {
  dom_.setInputValue(dom_.requireTestId('wiki-search-input'), term)
  await waitFor('the search term to register', () => !dom_.requireTestId('wiki-search-submit').disabled, 10000)
  const before = findRequests('GET', `/api/v1/workspaces/${W1}/wiki/search`).length
  dom_.submitForm(dom_.requireTestId('wiki-search-form'))
  return waitFor('the Wiki search request', () => {
    const all = findRequests('GET', `/api/v1/workspaces/${W1}/wiki/search`)
    return all.length > before ? all[all.length - 1] : false
  })
}

let exitCode = 0
try {
  /* ------------- A. Schema readiness + materialization (Schema-bound) --- */

  await withApp({ path: '/wiki', activeWorkspaceId: W1 }, async (window, dom_) => {
    await waitFor('the Wiki status panel', () => dom_.byTestId('wiki-status'))
    await waitFor('the paper Schema readiness list', () => dom_.byTestId('paper-schema-readiness-list'))
    await waitFor(
      'the per-paper Schema badges',
      () => badgeText('P1') && badgeText('P2') && badgeText('P4'),
    )

    // Every member paper reports the state the API returned for it.
    assert(badgeText('P1') === 'Schema ready', `P1 schema badge was "${badgeText('P1')}"`)
    assert(
      badgeText('P2') === 'Schema not materialized',
      `P2 schema badge was "${badgeText('P2')}"`,
    )
    assert(badgeText('P4') === 'Schema not materialized', `P4 schema badge was "${badgeText('P4')}"`)
    assert(!dom_.byTestId('materialize-paper-schema-P1'), 'a Schema-ready paper offered materialization')
    assert(dom_.byTestId('materialize-paper-schema-P2'), 'a not-materialized paper offered no materialization')
    dom_.assertNoApplicationError('the Schema readiness panel')

    // Materialization is requested through the API and the result is shown.
    dom_.clickTestId('materialize-paper-schema-P2')
    const materializeRequest = await waitFor(
      'the Schema materialization request',
      () => findRequests('POST', `/api/v1/workspaces/${W1}/papers/P2/schema/materialize`)[0],
    )
    assert(materializeRequest.method === 'POST', 'materialization was not submitted as a POST')
    await waitFor('the materialization outcome', () => dom_.byTestId('paper-schema-materialize-result-P2'))
    const outcome = (dom_.requireTestId('paper-schema-materialize-result-P2').textContent ?? '').trim()
    assert(outcome.includes('run-P2'), `the materialization outcome did not show the API run id: ${outcome}`)
    await waitFor('the re-read readiness', () => badgeText('P2') === 'Schema ready')
    assert(
      !dom_.byTestId('materialize-paper-schema-P2'),
      'a materialized paper still offered materialization after the API re-read',
    )
    pass('Workspace/Paper Schema readiness was inspectable and materialization was requested through the API')

    // An API conflict is surfaced explicitly (AC-014).
    dom_.clickTestId('materialize-paper-schema-P4')
    await waitFor(
      'the Schema materialization conflict',
      () => findRequests('POST', `/api/v1/workspaces/${W1}/papers/P4/schema/materialize`)[0],
    )
    const conflict = await waitFor('the API conflict state', () => {
      const block = dom_.applicationError()
      return block && (block.textContent ?? '').includes('WORKSPACE_BUSY') ? block : false
    })
    assert(
      (conflict.textContent ?? '').includes('HTTP 409'),
      `the conflict did not report the API status: ${conflict.textContent}`,
    )
    assert(
      dom_.byTestId('paper-schema-materialize-busy-P4'),
      'the workspace-busy conflict was not explained to the user',
    )
    assert(badgeText('P4') === 'Schema not materialized', 'a rejected materialization changed the reported readiness')
    pass('a rejected Schema materialization showed the API error envelope instead of failing silently')

    /* ------------------------------- B. Wiki page & entry detail views --- */

    await waitFor('the Schema Wiki page list', () => dom_.byTestId('wiki-page-list'))
    const pageLink = dom_.document.querySelector('a[href="/wiki/pages/PG1"]')
    assert(pageLink, 'the Wiki page list did not link to its detail view')
    dom_.click(pageLink)
    const pageDetail = await waitFor('the Wiki page detail', () => dom_.byTestId('wiki-page-detail'))
    const pageText = pageDetail.textContent ?? ''
    assert(pageText.includes('Paper one — Schema Wiki page'), 'the page detail did not show the API title')
    assert(pageText.includes('generic_research_paper version 1.0'), 'the page detail did not show its Schema identity')
    assert(pageText.includes('Build revision'), 'the page detail did not show the build revision')
    const pageSourceLink = dom_
      .requireTestId('wiki-page-source-paper')
      .querySelector('a[href="/library/P1"]')
    assert(
      (pageSourceLink?.textContent ?? '').trim() === 'Paper one',
      'the page detail did not resolve the source paper title from the Library',
    )
    assert(dom_.byTestId('wiki-page-summary'), 'the page summary was not rendered as readable prose')
    assert(dom_.byTestId('wiki-page-build-details'), 'the page build details were not exposed')
    dom_.assertNoApplicationError('the Wiki page detail')

    // Back to the Wiki overview, then open an Agent Learned entry.
    dom_.click(dom_.document.querySelector('a[href="/wiki"]'))
    await waitFor('the Wiki overview again', () => dom_.byTestId('wiki-status'))
    dom_.clickTestId('wiki-tab-agentic')
    await waitFor('the Agent Learned entry list', () => dom_.byTestId('wiki-agentic-entry-list'))
    const entryLink = dom_.document.querySelector('a[href="/wiki/entries/E1"]')
    assert(entryLink, 'the Agent Learned list did not link to its detail view')
    dom_.click(entryLink)
    const entryDetail = await waitFor('the Agent Learned entry detail', () => dom_.byTestId('wiki-entry-detail'))
    const entryText = entryDetail.textContent ?? ''
    assert(entryText.includes('Transit signal priority reduces intersection delay.'), 'the entry content was not rendered')
    assert(entryText.includes('RUN-9'), 'the entry detail did not show its originating research run')
    assert(dom_.byTestId('wiki-entry-evidence-refs'), 'the entry evidence references were not listed')
    assert(
      (dom_.requireTestId('wiki-entry-evidence-refs').textContent ?? '').includes('ev-1'),
      'the entry detail did not render its evidence reference',
    )
    assert(
      (dom_.requireTestId('wiki-entry-claim-refs').textContent ?? '').includes('claim-2'),
      'the entry detail did not render every source claim',
    )
    const entryPaperLink = dom_.requireTestId('wiki-entry-papers').querySelector('a[href="/library/P1"]')
    assert(
      (entryPaperLink?.textContent ?? '').trim().startsWith('Paper one'),
      'the entry detail did not resolve source paper titles from the Library',
    )
    assert(!dom_.byTestId('wiki-entry-superseded'), 'a current entry was presented as superseded')
    dom_.assertNoApplicationError('the Agent Learned entry detail')

    // The superseded-by relationship stays visible and navigable.
    dom_.click(dom_.document.querySelector('a[href="/wiki"]'))
    await waitFor('the Wiki overview again', () => dom_.byTestId('wiki-status'))
    dom_.clickTestId('wiki-tab-agentic')
    await waitFor('the Agent Learned entry list', () => dom_.byTestId('wiki-agentic-entry-list'))
    dom_.click(dom_.document.querySelector('a[href="/wiki/entries/E2"]'))
    const superseded = await waitFor(
      'the superseded entry note',
      () => dom_.byTestId('wiki-entry-superseded'),
    )
    assert(
      superseded.querySelector('a[href="/wiki/entries/E1"]'),
      'the superseded note did not link to the newer entry',
    )
    dom_.assertNoApplicationError('the superseded Agent Learned entry')
    pass('Wiki page and Agent Learned entry details rendered from structured API data')
  })

  /* ---------------------------- C. Search modes reported as available --- */

  await withApp({ path: '/wiki', activeWorkspaceId: W1 }, async (window, dom_) => {
    await waitFor('the Wiki status panel', () => dom_.byTestId('wiki-status'))
    dom_.clickTestId('wiki-tab-search')
    await waitFor('the Wiki search form', () => dom_.byTestId('wiki-search-input'))

    const semantic = dom_.requireTestId('wiki-search-mode-semantic')
    const lexical = dom_.requireTestId('wiki-search-mode-lexical')
    assert(!semantic.disabled, 'semantic search was not offered although the API reports it available')
    assert(lexical.checked, 'keyword matching was not the initial mode')

    dom_.selectRadio(semantic)
    const semanticRequest = await searchThroughUi(dom_, 'transit')
    assert(
      semanticRequest.query.mode === 'semantic',
      `the semantic search request used mode "${semanticRequest.query.mode}"`,
    )
    assert(semanticRequest.query.query === 'transit', 'the search term did not reach the API')
    await waitFor('the Wiki search results', () => dom_.byTestId('wiki-search-results'))
    const labels = dom_
      .allTestIds('wiki-hit-source')
      .map((element) => (element.textContent ?? '').trim())
    assert(labels.includes('Schema Wiki'), `Schema Wiki hits were not labelled: ${JSON.stringify(labels)}`)
    assert(labels.includes('Agent Learned'), `Agent Learned hits were not labelled: ${JSON.stringify(labels)}`)
    assert(
      (dom_.requireTestId('wiki-search-summary').textContent ?? '').includes('Semantic'),
      'the result summary did not report the match mode used',
    )

    dom_.selectRadio(lexical)
    const lexicalRequest = await searchThroughUi(dom_, 'delay')
    assert(
      lexicalRequest.query.mode === 'lexical',
      `the keyword search request used mode "${lexicalRequest.query.mode}"`,
    )
    await waitFor(
      'the keyword result summary',
      () => (dom_.requireTestId('wiki-search-summary').textContent ?? '').includes('Keyword'),
    )
    dom_.assertNoApplicationError('the Wiki search panel')
    pass('Wiki search submitted both reported match modes and labelled its results by knowledge source')
  })

  /* --------------------------------- D. Workspace without a Schema ----- */

  await withApp({ path: '/wiki', activeWorkspaceId: W2 }, async (window, dom_) => {
    await waitFor('the Wiki status panel', () => dom_.byTestId('wiki-status'))
    await waitFor(
      'the Schema Wiki unavailable explanation',
      () => dom_.byTestId('wiki-schema-unavailable'),
    )
    assert(
      !dom_.byTestId('paper-schema-readiness'),
      'a no-Schema workspace offered paper Schema readiness in the Wiki view',
    )
    assert(dom_.allTestIds('materialize-paper-schema-P3').length === 0, 'a no-Schema workspace offered materialization')
    dom_.assertNoApplicationError('the no-Schema workspace knowledge view')
    pass('a no-Schema workspace kept Schema Wiki unavailable without offering Schema materialization')
  })

  await withApp({ path: `/workspaces/${W2}`, activeWorkspaceId: W2 }, async (window, dom_) => {
    await waitFor('the Workspace settings', () => dom_.byTestId('workspace-schema-settings'))
    const note = await waitFor(
      'the paper Schema readiness explanation',
      () => dom_.byTestId('paper-schema-readiness-not-applicable'),
    )
    assert(
      (note.textContent ?? '').includes('no Schema binding'),
      `the no-Schema readiness explanation was unclear: ${note.textContent}`,
    )
    assert(dom_.allTestIds('materialize-paper-schema-P3').length === 0, 'a no-Schema workspace offered materialization')
    dom_.assertNoApplicationError('the no-Schema workspace settings')
    pass('a no-Schema workspace explained paper Schema materialization as a normal state')
  })

  /* --------------------------- E. Readiness in Workspace settings ------- */

  await withApp({ path: `/workspaces/${W1}`, activeWorkspaceId: W1 }, async (window, dom_) => {
    await waitFor('the Workspace settings', () => dom_.byTestId('workspace-schema-settings'))
    await waitFor(
      'the Workspace paper Schema readiness',
      () => dom_.byTestId('workspace-paper-schema-readiness'),
    )
    await waitFor('the paper Schema readiness list', () => dom_.byTestId('paper-schema-readiness-list'))
    await waitFor('the settings Schema badges', () => badgeText('P1') && badgeText('P4'))
    assert(badgeText('P1') === 'Schema ready', `P1 schema badge in settings was "${badgeText('P1')}"`)
    assert(
      badgeText('P4') === 'Schema not materialized',
      `P4 schema badge in settings was "${badgeText('P4')}"`,
    )
    assert(!dom_.byTestId('materialize-paper-schema-P1'), 'a Schema-ready paper offered materialization in settings')
    assert(dom_.byTestId('materialize-paper-schema-P4'), 'a not-materialized paper offered no materialization in settings')
    dom_.assertNoApplicationError('the Workspace settings view')
    pass('Workspace settings exposed the same API-derived per-Paper Schema readiness')
  })

  /* --------------------- F. Search with semantic mode unavailable ------- */

  state.semanticAvailable = false
  await withApp({ path: '/wiki', activeWorkspaceId: W1 }, async (window, dom_) => {
    await waitFor('the Wiki status panel', () => dom_.byTestId('wiki-status'))
    dom_.clickTestId('wiki-tab-search')
    await waitFor('the Wiki search form', () => dom_.byTestId('wiki-search-input'))

    assert(
      dom_.requireTestId('wiki-search-mode-semantic').disabled,
      'semantic search was offered although the capabilities API reports it unavailable',
    )
    assert(dom_.text().includes('Semantic search is not available'), 'the unavailable semantic mode was not explained')
    const request = await searchThroughUi(dom_, 'delay')
    assert(
      request.query.mode === 'lexical',
      `the fallback search used mode "${request.query.mode}" although semantic search is unavailable`,
    )
    await waitFor('the Wiki search results', () => dom_.byTestId('wiki-search-results'))
    dom_.assertNoApplicationError('the Wiki search panel without semantic search')
    pass('Wiki search stayed usable in the only mode the API reported')
  })

  process.stdout.write('PASS: richer Workspace knowledge UI smoke completed\n')
} catch (error) {
  exitCode = 1
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

process.exit(exitCode)
