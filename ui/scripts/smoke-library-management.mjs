/**
 * Offline DOM smoke for the extended Library management UI (T-006).
 *
 * Renders the real production bundle (ui/dist) inside jsdom and drives the same
 * user interactions a browser would against a deterministic in-memory stand-in
 * for the frozen `/api/v1/*` API. It verifies the extended Library behaviors
 * required for this iteration:
 *
 *   1. metadata correction is submitted to `PATCH .../metadata` with only the
 *      changed fields and the paper is re-read from the API afterwards;
 *   2. a duplicate decision is submitted to `POST /api/v1/duplicate-relations/{id}/resolve`;
 *   3. bibliography records render for a paper that has them;
 *   4. "Remove from workspace" and "Delete from Library" are different actions,
 *      and Library deletion requires an explicit confirmation before any DELETE
 *      request is sent;
 *   5. a deleted paper exposes restoration when the API reports a deleted state;
 *   6. enrichment status renders and a refresh is requested through the API.
 *
 * The harness never calls the Product/Core Python layers and never talks to a
 * backend directly: every assertion is made on the rendered DOM and on the HTTP
 * requests the bundled UI itself issued to `/api/v1/*`.
 *
 * Usage:
 *   npm run build
 *   node scripts/smoke-library-management.mjs [--dist <dir>]
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
const PAPER_ID = 'P1'
const WORKSPACE_ID = 'W1'
const RELATION_ID = 'R1'

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

const state = {
  paper: {
    paper_id: PAPER_ID,
    title: 'Original title',
    publication_year: 2021,
    venue: 'Original venue',
    doi: '10.1000/original',
    arxiv_id: null,
    status: 'active',
    primary_file_id: 'F1',
    created_at: '2024-01-01T00:00:00',
    updated_at: '2024-01-01T00:00:00',
    normalized_title: 'original title',
    abstract: 'Original abstract.',
    normalized_doi: '10.1000/original',
    authors: [{ author_order: 1, full_name: 'A. Original', affiliation: null, orcid: null }],
    files: [
      {
        file_id: 'F1',
        original_filename: 'paper.pdf',
        mime_type: 'application/pdf',
        file_size_bytes: 1024,
        is_primary: true,
        page_count: 3,
      },
    ],
    duplicate_relations: [],
    deleted_at: null,
  },
  relations: [
    {
      relation_id: RELATION_ID,
      source_paper_id: PAPER_ID,
      target_paper_id: 'P2',
      relation_type: 'probable_duplicate',
      confidence: 0.82,
      status: 'pending',
      reasons: [{ signal: 'title_similarity', score: 0.82 }],
    },
  ],
  citations: [
    {
      id: 'bib1',
      paper_id: PAPER_ID,
      source_format: 'bibtex',
      raw_text: '@article{key, title={A cited work}}',
      structured_json: { title: 'A cited work', year: 2019 },
      parse_status: 'parsed',
      parse_warnings: [],
      is_selected: true,
    },
  ],
  candidates: [
    {
      id: 'cand1',
      paper_id: PAPER_ID,
      paper_file_id: 'F1',
      field_name: 'title',
      value_text: 'Original title',
      source_type: 'pdf_header',
      source_location: 'page 1',
      confidence: 0.9,
      is_selected: true,
    },
  ],
  enrichment: {
    paper_id: PAPER_ID,
    doi: '10.1000/original',
    metadata_enrichment_status: 'pending',
    providers: [
      {
        provider: 'crossref',
        status: 'pending',
        http_status: null,
        fetched_at: null,
        attempt_count: 0,
        next_retry_at: null,
        fields: [],
        error_code: null,
        error_message: null,
      },
    ],
    resolved: {},
    error_code: null,
    error_message: null,
  },
}

const requests = []

function record(method, pathname, body) {
  requests.push({ method, path: pathname, body })
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

function handle(method, pathname, body) {
  // ---- system + workspace shell
  if (method === 'GET' && pathname === '/api/v1/health') {
    return json({ status: 'ok' })
  }
  if (method === 'GET' && pathname === '/api/v1/capabilities') {
    return json({
      pause_resume: true,
      user_schema_creation: false,
      base_wiki: true,
      agentic_wiki: true,
      semantic_wiki_search: false,
      pdf_upload_max_bytes: 10485760,
    })
  }
  if (method === 'GET' && pathname === `/api/v1/workspaces/${WORKSPACE_ID}`) {
    return json({
      workspace_id: WORKSPACE_ID,
      name: 'Smoke workspace',
      status: 'active',
      schema_mode: 'none',
      schema_binding: null,
      revision: 1,
      created_at: '2024-01-01T00:00:00',
      updated_at: '2024-01-01T00:00:00',
    })
  }
  if (method === 'GET' && pathname === `/api/v1/workspaces/${WORKSPACE_ID}/papers`) {
    return json({
      items: [{ workspace_id: WORKSPACE_ID, paper_id: PAPER_ID, created_at: null, already_member: false }],
    })
  }

  // ---- papers
  if (method === 'GET' && pathname === `/api/v1/papers/${PAPER_ID}`) {
    return json(state.paper)
  }
  if (method === 'GET' && pathname === `/api/v1/papers/${PAPER_ID}/second-layer`) {
    return json({
      paper_id: PAPER_ID,
      status: 'ready',
      second_layer_ready: true,
      second_layer_blockers: [],
      error_code: null,
      error_message: null,
    })
  }
  if (method === 'PATCH' && pathname === `/api/v1/papers/${PAPER_ID}/metadata`) {
    const patch = body && typeof body === 'object' ? body : {}
    const updated = Object.keys(patch)
    assert(updated.length > 0, 'the UI submitted an empty metadata correction')
    for (const [key, value] of Object.entries(patch)) {
      state.paper[key] = value
    }
    if (typeof patch.title === 'string') {
      state.paper.normalized_title = patch.title.toLowerCase()
    }
    state.paper.updated_at = '2024-02-02T00:00:00'
    return json({
      paper_id: PAPER_ID,
      status: state.paper.status,
      updated_fields: updated,
      audit_log_id: 'audit-metadata',
    })
  }
  if (method === 'GET' && pathname === `/api/v1/papers/${PAPER_ID}/metadata-candidates`) {
    return json(state.candidates)
  }
  if (method === 'GET' && pathname === `/api/v1/papers/${PAPER_ID}/citations`) {
    return json(state.citations)
  }
  if (method === 'GET' && pathname === `/api/v1/papers/${PAPER_ID}/enrichment`) {
    return json(state.enrichment)
  }
  if (method === 'POST' && pathname === `/api/v1/papers/${PAPER_ID}/enrichment/refresh`) {
    state.enrichment = {
      ...state.enrichment,
      metadata_enrichment_status: 'fetched',
      resolved: { title: 'Enriched title' },
      providers: state.enrichment.providers.map((provider) => ({
        ...provider,
        status: 'fetched',
        http_status: 200,
        attempt_count: 1,
        fetched_at: '2024-03-03T00:00:00',
      })),
    }
    return json(state.enrichment)
  }
  if (method === 'GET' && pathname === `/api/v1/papers/${PAPER_ID}/duplicate-relations`) {
    return json({ items: state.relations })
  }
  if (method === 'POST' && pathname === `/api/v1/duplicate-relations/${RELATION_ID}/resolve`) {
    const decision = body && typeof body === 'object' ? body.decision : undefined
    const statusByDecision = {
      same_paper: 'confirmed',
      different_version: 'confirmed',
      not_duplicate: 'rejected',
      ignore: 'ignored',
    }
    assert(statusByDecision[decision], `the UI submitted an unknown duplicate decision: ${decision}`)
    state.relations = state.relations.map((relation) =>
      relation.relation_id === RELATION_ID ? { ...relation, status: statusByDecision[decision] } : relation,
    )
    return json({
      relation_id: RELATION_ID,
      status: statusByDecision[decision],
      decision,
      audit_log_id: 'audit-duplicate',
    })
  }
  if (method === 'DELETE' && pathname === `/api/v1/papers/${PAPER_ID}`) {
    state.paper = { ...state.paper, status: 'deleted', deleted_at: '2024-04-04T00:00:00' }
    return json({
      paper_id: PAPER_ID,
      status: 'deleted',
      updated_fields: ['status', 'deleted_at'],
      audit_log_id: 'audit-delete',
    })
  }
  if (method === 'POST' && pathname === `/api/v1/papers/${PAPER_ID}/restore`) {
    state.paper = { ...state.paper, status: 'active', deleted_at: null }
    return json({
      paper_id: PAPER_ID,
      status: 'active',
      updated_fields: ['status', 'deleted_at'],
      audit_log_id: 'audit-restore',
    })
  }

  // ---- workspace membership removal (must stay a different route/action)
  if (method === 'DELETE' && pathname === `/api/v1/workspaces/${WORKSPACE_ID}/papers/${PAPER_ID}`) {
    return json({
      workspace_id: WORKSPACE_ID,
      name: 'Smoke workspace',
      status: 'active',
      schema_mode: 'none',
      schema_binding: null,
      revision: 2,
      created_at: '2024-01-01T00:00:00',
      updated_at: '2024-01-01T00:00:00',
    })
  }

  return notFound(method, pathname)
}

/* ------------------------------------------------------------ DOM rendered */

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

function createFetchShim(window) {
  return async function shimFetch(input, init = {}) {
    const url = new URL(String(input), window.location.href)
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
    record(method, url.pathname, body)
    try {
      return handle(method, url.pathname, body)
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('FAIL:')) {
        throw error
      }
      return errorEnvelope(500, 'SMOKE_STANDIN_ERROR', `stand-in route failed: ${error}`)
    }
  }
}

/* ------------------------------------------------------------- DOM helpers */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function waitFor(label, predicate, timeoutMs = 15000) {
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
      const body = (globalThis.document?.body?.textContent ?? '').replace(/\s+/g, ' ').slice(0, 800)
      fail(`timed out after ${timeoutMs}ms waiting for ${label}\n--- rendered text ---\n${body}`)
    }
    await sleep(30)
  }
}

function makeDomHelpers(window) {
  const document = window.document
  const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`)
  const text = () => document.body.textContent ?? ''

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

  function setSelectValue(select, value) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set
    setter.call(select, value)
    select.dispatchEvent(new window.Event('change', { bubbles: true }))
    select.dispatchEvent(new window.Event('input', { bubbles: true }))
  }

  return { byTestId, text, requireTestId, click, clickTestId, setInputValue, setSelectValue }
}

function findRequests(method, pathname) {
  return requests.filter((request) => request.method === method && request.path === pathname)
}

/* ------------------------------------------------------------------- setup */

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
const dom = new JSDOM(html, { url: `${origin}/library/${PAPER_ID}`, pretendToBeVisual: true })
const { window } = dom
// Open a Workspace so the membership block renders beside the Library actions.
window.localStorage.setItem('transit-scholar.ui.active-workspace-id', WORKSPACE_ID)
applyGlobals(window, createFetchShim(window))
const dom$ = makeDomHelpers(window)

const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-library-smoke-'))
const bundleTarget = path.join(scratch, 'bundle.mjs')
copyFileSync(bundleFile, bundleTarget)

let exitCode = 0
try {
  await import(pathToFileURL(bundleTarget).href)

  await waitFor('the paper detail view', () => dom$.byTestId('paper-status'), 20000)
  assert(!dom$.text().includes('Backend unavailable'), 'the UI reported the backend as unavailable')

  /* ------------------------------------- 1. metadata correction + refresh */

  await waitFor('the metadata correction form', () => dom$.byTestId('metadata-correction-form'))
  assert(
    dom$.requireTestId('metadata-title-input').value === 'Original title',
    'the metadata form did not start from the API-reported title',
  )
  assert(
    dom$.requireTestId('metadata-correction-submit').disabled,
    'the correction submit button was enabled with no changes',
  )

  dom$.setInputValue(dom$.requireTestId('metadata-title-input'), 'Corrected title')
  await waitFor('the correction submit button to enable', () => !dom$.requireTestId('metadata-correction-submit').disabled)
  dom$.clickTestId('metadata-correction-submit')

  const patchRequest = await waitFor(
    'the metadata PATCH request',
    () => findRequests('PATCH', `/api/v1/papers/${PAPER_ID}/metadata`)[0],
  )
  assert(
    patchRequest.body.title === 'Corrected title',
    `the correction did not send the new title: ${JSON.stringify(patchRequest.body)}`,
  )
  assert(
    !('venue' in patchRequest.body) && !('doi' in patchRequest.body) && !('authors' in patchRequest.body),
    `the correction sent unchanged fields: ${JSON.stringify(patchRequest.body)}`,
  )
  // The Paper is re-read from the API and the corrected value is displayed.
  await waitFor('the corrected title to be displayed', () => dom$.text().includes('Corrected title'))
  await waitFor('the metadata correction result', () => dom$.byTestId('metadata-correction-result'))
  assert(
    findRequests('GET', `/api/v1/papers/${PAPER_ID}`).length >= 2,
    'the UI did not re-read the paper from the API after the correction',
  )
  pass('metadata correction was submitted and the paper was refreshed from the API')

  /* ------------------------------------------- 2. duplicate adjudication */

  await waitFor('the duplicate relation list', () => dom$.byTestId('duplicate-relation-list'))
  const decisionSelect = await waitFor(
    'the duplicate decision control',
    () => dom$.byTestId(`duplicate-decision-${RELATION_ID}`),
  )
  dom$.setSelectValue(decisionSelect, 'not_duplicate')
  await sleep(60)
  dom$.clickTestId(`duplicate-resolve-${RELATION_ID}`)

  const resolveRequest = await waitFor(
    'the duplicate resolution request',
    () => findRequests('POST', `/api/v1/duplicate-relations/${RELATION_ID}/resolve`)[0],
  )
  assert(
    resolveRequest.body.decision === 'not_duplicate',
    `the submitted duplicate decision was ${JSON.stringify(resolveRequest.body)}`,
  )
  await waitFor(
    'the decided relation to become read-only',
    () => !dom$.byTestId(`duplicate-resolve-${RELATION_ID}`),
  )
  const relationBadge = dom$.requireTestId(`duplicate-relation-${RELATION_ID}`).querySelector('.badge')
  assert(
    (relationBadge?.textContent ?? '').includes('Not a duplicate'),
    `the relation status was not refreshed from the API: ${relationBadge?.textContent}`,
  )
  pass('a duplicate decision was submitted for an available duplicate relation')

  /* -------------------------------------------------- 3. bibliography */

  const bibliography = await waitFor('the bibliography records', () => dom$.byTestId('bibliography-record-bib1'))
  const bibliographyText = bibliography.textContent ?? ''
  assert(
    bibliographyText.includes('A cited work') && bibliographyText.includes('bibtex'),
    `the bibliography record did not render its API values: ${bibliographyText}`,
  )
  assert(bibliographyText.includes('Parsed'), 'the bibliography record did not show its parse status')
  pass('bibliography records rendered for a paper that has them')

  /* ------------------------- 4. Library deletion vs. workspace removal */

  const membershipButton = await waitFor(
    'the workspace removal control',
    () => dom$.byTestId('remove-paper-from-workspace'),
  )
  const deleteButton = dom$.requireTestId('delete-library-paper')
  assert(
    membershipButton !== deleteButton,
    'the same control was used for workspace removal and Library deletion',
  )
  assert(
    (membershipButton.textContent ?? '').includes('Remove from workspace'),
    'the workspace action was not labelled "Remove from workspace"',
  )
  assert(
    (deleteButton.textContent ?? '').includes('Delete from Library'),
    'the Library action was not labelled "Delete from Library"',
  )
  assert(
    findRequests('DELETE', `/api/v1/papers/${PAPER_ID}`).length === 0,
    'the paper was deleted from the Library before any confirmation',
  )

  dom$.clickTestId('delete-library-paper')
  const dialog = await waitFor('the Library deletion confirmation', () => dom$.byTestId('delete-library-confirm-dialog'))
  assert(
    (dialog.textContent ?? '').includes('Remove from workspace') &&
      (dialog.textContent ?? '').includes('workspace'),
    'the confirmation did not distinguish workspace removal from Library deletion',
  )
  assert(
    findRequests('DELETE', `/api/v1/papers/${PAPER_ID}`).length === 0,
    'opening the confirmation deleted the paper before the user confirmed',
  )

  dom$.clickTestId('confirm-delete-library-paper')
  await waitFor(
    'the confirmed Library deletion request',
    () => findRequests('DELETE', `/api/v1/papers/${PAPER_ID}`)[0],
  )
  pass('Library deletion required confirmation and stayed distinct from workspace removal')

  /* -------------------------------------- 5. restoration when deleted */

  await waitFor('the deleted state panel', () => dom$.byTestId('library-deletion-deleted-state'))
  assert(!dom$.byTestId('delete-library-paper'), 'a deleted paper still offered Library deletion')
  dom$.clickTestId('restore-library-paper')
  await waitFor(
    'the restoration request',
    () => findRequests('POST', `/api/v1/papers/${PAPER_ID}/restore`)[0],
  )
  await waitFor('the paper to be restorable again through deletion', () => dom$.byTestId('delete-library-paper'))
  pass('deleted paper restoration was exposed when the API reported a deleted state')

  /* ------------------------------------------------------ 6. enrichment */

  assert(
    dom$.byTestId('enrichment-disclosure'),
    'the enrichment diagnostics were not placed behind an advanced disclosure',
  )
  assert(
    dom$.byTestId('metadata-candidates-disclosure'),
    'the metadata candidates were not placed behind an advanced disclosure',
  )
  await waitFor('the enrichment panel', () => dom$.byTestId('paper-enrichment'))
  dom$.clickTestId('refresh-enrichment')
  await waitFor(
    'the enrichment refresh request',
    () => findRequests('POST', `/api/v1/papers/${PAPER_ID}/enrichment/refresh`)[0],
  )
  await waitFor('the refreshed enrichment status', () =>
    (dom$.byTestId('paper-enrichment')?.textContent ?? '').includes('Provider data retrieved'),
  )
  pass('enrichment status rendered and a refresh was requested through the API')

  restoreGlobals()
  process.stdout.write('PASS: extended Library management smoke completed\n')
} catch (error) {
  exitCode = 1
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
  window.close()
}

process.exit(exitCode)
