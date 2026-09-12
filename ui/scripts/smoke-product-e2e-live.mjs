/**
 * Real end-to-end local product smoke (T-010).
 *
 * Renders the real production bundle (ui/dist) inside a FRESH jsdom session and
 * drives the same interactions a browser would against a REAL running local
 * TransitScholar application (one origin serving `/api/v1/*` and the built
 * frontend), using a REAL paper PDF, the real library/ingestion pipeline, the
 * real Layer2 evidence parse + retrieval index, the real product/AgentRun
 * runtime, and the real citation projection:
 *
 *   1. create a Schema-bound workspace through the UI (permanent-binding
 *      warning and non-editable binding);
 *   2. import a real paper PDF through the Library and inspect the registered
 *      local PDF of the resulting Paper;
 *   3. add that Paper to the open Workspace through the UI;
 *   4. hand off to the harness so it can build the Paper's Layer2 evidence
 *      parse + retrieval index (the local product exposes no API for this), then
 *      wait for that preparation to complete;
 *   5. create a Conversation, submit a real research prompt, and watch the
 *      real AgentRun Timeline while the run is active;
 *   6. wait for the final answer, verify the Timeline collapsed but still
 *      reopens with its projected events;
 *   7. inspect an answer citation and open the cited local PDF;
 *   8. read the workspace Wiki (status, capability, Base vs Agentic sources,
 *      search) from the same API-derived values the UI renders;
 *   9. fail if the UI ever issues a non-`/api/v1/*` request.
 *
 * Usage:
 *   node scripts/smoke-product-e2e-live.mjs --base http://127.0.0.1:8017 \
 *     [--dist <dir>] [--pdf <file>] [--prompt "..."] [--schema <id@version>] \
 *     [--prepare-request <file>] [--prepare-ready <file>] \
 *     [--result-file <file>] [--run-timeout-ms 1800000]
 *
 * `--prepare-request` is written (JSON: workspaceId, paperId) once the Paper is
 * a Workspace member; the run then waits for `--prepare-ready` before asking a
 * research question, so the harness can prepare local evidence deterministically
 * instead of racing the UI.
 *
 * Requires: `npm run build` and a running local application with an available
 * agent runtime (`uvicorn ...`).
 */
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, existsSync } from 'node:fs'
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
const baseUrl = (argValue('--base', process.env.TRANSIT_SCHOLAR_SMOKE_API_BASE ?? 'http://127.0.0.1:8017')).replace(/\/+$/, '')
const pdfPath = path.resolve(
  argValue('--pdf', path.join(process.cwd(), '..', 'tests', 'fixtures', 'metadata', 'causal_reinforcement_learning_train_scheduling.pdf')),
)
const SCHEMA_OPTION = argValue('--schema', 'generic_research_paper@1.0')
const prepareRequest = argValue('--prepare-request', null)
const prepareReady = argValue('--prepare-ready', null)
const resultFile = argValue('--result-file', null)
const runTimeoutMs = Number(argValue('--run-timeout-ms', '1800000'))
const prompt = argValue(
  '--prompt',
  'What does the paper report about train scheduling on single-track railway networks, and which methods does it use?',
)

const NativeFormData = globalThis.FormData
const NativeFile = globalThis.File
const nativeFetch = globalThis.fetch.bind(globalThis)

function fail(message) {
  process.stderr.write(`FAIL: ${message}\n`)
  // Persist the machine-observable flow state before exiting, so a failing gate
  // can report exactly how far the UI got and which API calls it made. The
  // flow state is unavailable for the earliest argument/bundle failures.
  try {
    result.failure = message
    writeResult()
  } catch {
    /* the flow state was not available */
  }
  process.exit(1)
}

function pass(message) {
  process.stdout.write(`PASS: ${message}\n`)
}

function bundlePath() {
  const assetsDir = path.join(distDir, 'assets')
  const candidates = readdirSync(assetsDir).filter((name) => name.endsWith('.js'))
  if (candidates.length === 0) {
    fail(`no JavaScript bundle found in ${assetsDir}; run "npm run build" first`)
  }
  return path.join(assetsDir, candidates[0])
}

/* ------------------------------------------------------- API (harness side) */

/** Direct API reads used ONLY to confirm what the UI did, never to mutate. */
async function apiGet(pathname) {
  const response = await nativeFetch(`${baseUrl}${pathname}`)
  if (!response.ok) {
    fail(`GET ${pathname} -> HTTP ${response.status}`)
  }
  return response.json()
}

/* ------------------------------------------------------------ DOM rendered */

const savedGlobals = new Map()
let apiRequests = []
let nonApiRequests = []

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
    HTMLTextAreaElement: window.HTMLTextAreaElement,
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

/** Convert a jsdom FormData body into a Node fetch FormData. */
async function normalizeBody(body) {
  if (!body || typeof body !== 'object' || typeof body.entries !== 'function') {
    return body
  }
  if (body.constructor && body.constructor.name !== 'FormData') {
    return body
  }
  const form = new NativeFormData()
  for (const [key, value] of body.entries()) {
    if (typeof value === 'string') {
      form.append(key, value)
      continue
    }
    if (value && typeof value.arrayBuffer === 'function') {
      const buffer = await value.arrayBuffer()
      form.append(key, new NativeFile([buffer], value.name || 'upload.pdf', { type: value.type || 'application/pdf' }))
      continue
    }
    form.append(key, String(value))
  }
  return form
}

function createFetchShim() {
  return async function shimFetch(input, init = {}) {
    const url = new URL(String(input), baseUrl)
    const record = `${init.method ?? 'GET'} ${url.pathname}`
    if (url.pathname.startsWith('/api/v1/')) {
      apiRequests.push(record)
    } else {
      nonApiRequests.push(record)
    }
    const body = await normalizeBody(init.body)
    return nativeFetch(url, { ...init, body })
  }
}

/* ------------------------------------------------------------- DOM helpers */

async function waitFor(label, predicate, timeoutMs = 30000) {
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
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
}

function makeDomHelpers(window) {
  const document = window.document
  const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`)
  const allByTestId = (id) => Array.from(document.querySelectorAll(`[data-testid="${id}"]`))
  const text = () => document.body.textContent ?? ''

  function requireTestId(id, label = id) {
    const element = byTestId(id)
    if (!element) {
      fail(`expected element [data-testid="${label}"] was not rendered`)
    }
    return element
  }

  function findLink(href) {
    return document.querySelector(`a[href="${href}"]`)
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

  /** Submit a form the way a browser does (jsdom does not navigate). */
  function submitTestId(id) {
    const form = requireTestId(id)
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
  }

  function setInputValue(input, value) {
    const prototype =
      input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, value)
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
    input.dispatchEvent(new window.Event('change', { bubbles: true }))
  }

  function setSelectValue(select, value) {
    Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set.call(select, value)
    select.dispatchEvent(new window.Event('change', { bubbles: true }))
    select.dispatchEvent(new window.Event('input', { bubbles: true }))
  }

  function setFileInput(input, file) {
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
    input.dispatchEvent(new window.Event('change', { bubbles: true }))
  }

  function workspaceCard(name) {
    const list = byTestId('workspace-list')
    if (!list) {
      return null
    }
    return (
      Array.from(list.querySelectorAll('li.card')).find(
        (item) => (item.querySelector('.card__title')?.textContent ?? '').trim() === name,
      ) ?? null
    )
  }

  return {
    document,
    byTestId,
    allByTestId,
    text,
    requireTestId,
    findLink,
    click,
    clickTestId,
    submitTestId,
    setInputValue,
    setSelectValue,
    setFileInput,
    workspaceCard,
  }
}

/* ------------------------------------------------------------------- setup */

const unique = Date.now().toString(36)
const workspaceName = `Product smoke ${unique}`
const conversationTitle = `Product smoke conversation ${unique}`

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
// A fresh browser session: brand-new jsdom window, empty local storage, direct
// entry at the Workspaces route.
const dom = new JSDOM(html, { url: `${baseUrl}/workspaces`, pretendToBeVisual: true })
const { window } = dom
if (window.localStorage.length !== 0) {
  fail('the smoke session did not start with empty browser storage')
}
applyGlobals(window, createFetchShim())
const domHelpers = makeDomHelpers(window)

const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-product-smoke-'))
const bundleTarget = path.join(scratch, `bundle-${unique}.mjs`)
copyFileSync(bundleFile, bundleTarget)

let exitCode = 0
const result = {
  workspaceName,
  workspaceId: null,
  paperId: null,
  conversationId: null,
  runId: null,
  prompt,
  timelineEventCountActive: 0,
  timelineEventCountAfterReopen: 0,
  citationEvidenceId: null,
  citationPaperId: null,
  citationPages: null,
  citationHref: null,
  finalAnswer: null,
  wikiStatus: null,
  wikiAgenticCount: null,
  wikiSearchOutcome: null,
  apiRequests: [],
}
function writeResult() {
  if (!resultFile) {
    return
  }
  result.apiRequests = apiRequests
  writeFileSync(resultFile, JSON.stringify(result, null, 2), 'utf8')
}

try {
  await import(pathToFileURL(bundleTarget).href)

  /* ------------------------------------------------- 0. shell and session */

  await waitFor('the Workspaces section to load', () => domHelpers.text().includes('New workspace'), 20000)
  if (domHelpers.text().includes('Backend unavailable')) {
    fail(`the UI cannot reach the API at ${baseUrl}`)
  }
  const capabilities = await apiGet('/api/v1/capabilities')
  if (!capabilities.pause_resume) {
    fail(`the running application reports no agent runtime: ${JSON.stringify(capabilities)}`)
  }
  pass('fresh browser session reached the real local application with an available agent runtime')

  /* ------------------------------------------- 1. Schema-bound workspace */

  domHelpers.clickTestId('new-workspace-button')
  await waitFor('the new-workspace dialog', () => domHelpers.byTestId('create-workspace-dialog'))
  domHelpers.setInputValue(domHelpers.requireTestId('workspace-name-input'), workspaceName)
  domHelpers.clickTestId('schema-mode-schema')
  const warning = await waitFor('the permanent Schema binding warning', () => domHelpers.byTestId('schema-binding-warning'), 15000)
  const warningText = warning.textContent ?? ''
  if (!/permanent/i.test(warningText) || !/cannot be changed/i.test(warningText)) {
    fail(`the Schema binding warning was not explicit about permanence: ${warningText}`)
  }
  const select = await waitFor(
    'the Schema catalog to load options',
    () => {
      const candidate = domHelpers.byTestId('schema-select')
      return candidate && candidate.options.length > 1 ? candidate : false
    },
    20000,
  )
  const optionValues = Array.from(select.options).map((option) => option.value)
  const schemaOption = optionValues.find((value) => value === SCHEMA_OPTION) ?? optionValues[1]
  domHelpers.setSelectValue(select, schemaOption)
  domHelpers.clickTestId('create-workspace-submit')

  await waitFor(
    'the Schema-bound workspace settings view',
    () => window.location.pathname.startsWith('/workspaces/') && domHelpers.byTestId('workspace-schema-version'),
    20000,
  )
  const workspaceId = decodeURIComponent(window.location.pathname.split('/')[2] ?? '')
  result.workspaceId = workspaceId
  const workspace = await apiGet(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}`)
  if (workspace.schema_mode !== 'bound' || !workspace.schema_binding) {
    fail(`the created workspace has no permanent Schema binding: ${JSON.stringify(workspace)}`)
  }
  if (!domHelpers.byTestId('workspace-schema-immutable')) {
    fail('the workspace settings did not mark the Schema binding as non-editable')
  }
  pass(`created the Schema-bound Workspace through the UI (${workspaceId})`)

  /* ------------------------------------------------- 2. real PDF import */

  domHelpers.click(domHelpers.findLink('/library'))
  await waitFor('the Library section', () => domHelpers.byTestId('import-pdf-button'), 20000)
  domHelpers.clickTestId('import-pdf-button')
  await waitFor('the import dialog', () => domHelpers.byTestId('import-paper-dialog'))

  const fileBuffer = readFileSync(pdfPath)
  const jsdomFile = new window.File([new Uint8Array(fileBuffer)], path.basename(pdfPath), { type: 'application/pdf' })
  domHelpers.setFileInput(domHelpers.requireTestId('import-paper-file-input'), jsdomFile)
  await waitFor('the selected file summary', () => domHelpers.byTestId('import-paper-file-summary'), 10000)
  domHelpers.clickTestId('import-paper-submit')

  await waitFor(
    'the imported paper detail view',
    () => window.location.pathname.startsWith('/library/') && domHelpers.byTestId('paper-status'),
    180000,
  )
  const paperId = decodeURIComponent(window.location.pathname.split('/')[2] ?? '')
  result.paperId = paperId
  if (!paperId) {
    fail('the import did not open the resulting paper detail view')
  }
  const paper = await apiGet(`/api/v1/papers/${encodeURIComponent(paperId)}`)
  if (paper.paper_id !== paperId) {
    fail(`the imported paper is not readable through GET /api/v1/papers/{id}`)
  }
  const files = await apiGet(`/api/v1/papers/${encodeURIComponent(paperId)}/files`)
  if (!files.some((item) => item.file_id && item.mime_type === 'application/pdf')) {
    fail('the imported Paper has no registered PDF file')
  }
  const openPdf = domHelpers.requireTestId('open-primary-pdf')
  const pdfHref = openPdf.getAttribute('href') ?? ''
  if (!pdfHref.includes('/api/v1/files/')) {
    fail(`Open PDF did not use the file-content endpoint: ${pdfHref}`)
  }
  const pdfResponse = await nativeFetch(new URL(pdfHref, baseUrl))
  if (!pdfResponse.ok) {
    fail(`the imported local PDF could not be fetched: HTTP ${pdfResponse.status}`)
  }
  if (!(pdfResponse.headers.get('content-type') ?? '').includes('pdf')) {
    fail(`the registered local PDF was not served as a PDF: ${pdfResponse.headers.get('content-type')}`)
  }
  pass(`imported a real PDF through the Library and opened its registered local PDF (${paperId})`)

  /* ----------------------------------- 3. add the Paper to the Workspace */

  await waitFor('the membership control', () => domHelpers.byTestId('paper-workspace-membership'), 20000)
  domHelpers.clickTestId('add-paper-to-workspace')
  await waitFor(
    'the membership state to report "In this workspace"',
    () => (domHelpers.byTestId('paper-membership-state')?.textContent ?? '').includes('In this workspace'),
    30000,
  )
  const membership = await apiGet(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/papers`)
  if (!membership.items.some((item) => item.paper_id === paperId)) {
    fail('the UI reported membership but the workspace papers API does not list the paper')
  }
  pass('added the Paper to the open Workspace through the UI')

  /* ------------------ 4. local Layer2 evidence preparation (harness step) */

  if (prepareRequest) {
    writeFileSync(
      prepareRequest,
      JSON.stringify({ workspaceId, paperId, fileId: files[0]?.file_id ?? null }),
      'utf8',
    )
    if (!prepareReady) {
      fail('--prepare-request requires --prepare-ready')
    }
    const deadline = Date.now() + 900000
    while (!existsSync(prepareReady)) {
      if (Date.now() > deadline) {
        fail(`timed out waiting for local evidence preparation (${prepareReady})`)
      }
      await new Promise((resolve) => setTimeout(resolve, 200))
    }
    const ready = JSON.parse(readFileSync(prepareReady, 'utf8'))
    if (ready.status !== 'ready' || ready.paperId !== paperId) {
      fail(`local evidence preparation did not report ready for ${paperId}: ${JSON.stringify(ready)}`)
    }
    pass(`local Layer2 evidence parse + retrieval index is ready (${ready.parseRunId ?? 'parse'})`)
  }

  /* --------------------------------- 5. Conversation and the real AgentRun */

  domHelpers.click(domHelpers.findLink('/research'))
  await waitFor('the Research view', () => domHelpers.byTestId('research-view'), 30000)
  domHelpers.clickTestId('new-conversation-button')
  await waitFor('the new-conversation dialog', () => domHelpers.byTestId('new-conversation-dialog'))
  domHelpers.setInputValue(domHelpers.requireTestId('conversation-title-input'), conversationTitle)
  domHelpers.clickTestId('create-conversation-submit')

  await waitFor(
    'the created conversation to become active',
    () => domHelpers.byTestId('active-conversation') && domHelpers.text().includes(conversationTitle),
    30000,
  )
  const conversationId = domHelpers
    .byTestId('conversation-list')
    ?.querySelector('[data-testid^="conversation-item-"]')
    ?.getAttribute('data-testid')
    ?.replace('conversation-item-', '')
  if (!conversationId) {
    fail('the created conversation was not listed')
  }
  result.conversationId = conversationId

  domHelpers.setInputValue(domHelpers.requireTestId('prompt-input'), prompt)
  domHelpers.clickTestId('research-primary-control')

  await waitFor(
    'the active run panel while the run is still executing',
    () => domHelpers.byTestId('active-run') && domHelpers.byTestId('run-status'),
    60000,
  )
  const submitted = await apiGet(`/api/v1/conversations/${encodeURIComponent(conversationId)}`)
  const runId = submitted.turns[submitted.turns.length - 1]?.agent_run_id ?? null
  if (!runId) {
    fail('the submitted turn has no AgentRun identifier')
  }
  result.runId = runId

  // Structured intermediate output must be visible while the run is active.
  await waitFor(
    'live Timeline events while the AgentRun is still active',
    () => {
      const events = domHelpers.document.querySelectorAll('[data-testid^="timeline-event-"]').length
      result.timelineEventCountActive = events
      return events >= 1
    },
    120000,
  )
  const timeline = domHelpers.byTestId('run-timeline')
  if (!timeline || timeline.open !== true) {
    fail('the research Timeline was not expanded while the run is active')
  }
  const liveState = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
  if (liveState.status === 'completed' || liveState.status === 'failed') {
    fail('the run finished before the UI could observe its live Timeline')
  }
  pass(`observed ${result.timelineEventCountActive} structured Timeline events while the real AgentRun was active (${runId})`)

  /* ------------------------- 6. final answer and collapsed-but-openable Timeline */

  // The durable turn's answer is written after the run's terminal status (the
  // post-run workspace promotion runs in between), so wait for the UI to
  // display it, and fail fast when the API reports a run that can never
  // produce an answer instead of consuming the whole run timeout.
  const answerDeadline = Date.now() + runTimeoutMs
  let lastRunCheck = 0
  for (;;) {
    const answer = domHelpers.byTestId('turn-final-answer')
    if (answer && (answer.textContent ?? '').trim().length > 0) {
      break
    }
    if (Date.now() - lastRunCheck > 1500) {
      lastRunCheck = Date.now()
      const state = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
      if (state.status === 'failed' || state.status === 'cancelled') {
        fail(`the AgentRun ended as ${state.status} before the UI displayed an answer`)
      }
    }
    if (Date.now() > answerDeadline) {
      fail(`timed out after ${runTimeoutMs}ms waiting for the final answer to be displayed`)
    }
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  const finalRun = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
  if (finalRun.status !== 'completed') {
    fail(`expected a completed AgentRun, the API reports ${finalRun.status}`)
  }
  const completedTimeline = domHelpers.byTestId(`turn-timeline-${runId}`)
  if (!completedTimeline) {
    fail('the completed turn does not keep its research Timeline available')
  }
  if (completedTimeline.open !== false) {
    fail('the completed research Timeline was not collapsed by default')
  }
  completedTimeline.open = true
  completedTimeline.dispatchEvent(new window.Event('toggle'))
  await waitFor(
    'the completed turn Timeline to render its events on demand',
    () => {
      const events = completedTimeline.querySelectorAll('[data-testid^="timeline-event-"]').length
      result.timelineEventCountAfterReopen = events
      return events >= 1
    },
    60000,
  )
  const turn = (await apiGet(`/api/v1/conversations/${encodeURIComponent(conversationId)}`)).turns.at(-1)
  result.finalAnswer = turn?.final_answer ?? null
  if (!result.finalAnswer) {
    fail('the persisted turn carries no final answer')
  }
  if ((turn?.user_message ?? '') !== prompt) {
    fail('the persisted turn does not carry the submitted prompt')
  }
  pass('final answer displayed with the research Timeline collapsed by default and reopenable with its projected events')

  /* --------------------------------- 7. citation inspection + cited PDF */

  if (!(await waitFor('the answer citations to render', () => domHelpers.byTestId('answer-citations'), 30000))) {
    fail('the completed turn displays no answer citations')
  }
  domHelpers.clickTestId('citation-reference-1')
  const dialog = await waitFor('the citation detail dialog', () => domHelpers.byTestId('citation-detail-dialog'), 30000)
  const dialogText = dialog.textContent ?? ''
  for (const expected of ['Paper title', 'Paper identity', 'Page locator', 'Evidence excerpt', 'Evidence identity']) {
    if (!dialogText.includes(expected)) {
      fail(`the citation dialog does not expose ${expected}: ${dialogText.slice(0, 400)}`)
    }
  }
  if (!dialogText.includes(paperId)) {
    fail(`the citation dialog does not reference the imported paper ${paperId}`)
  }
  const citationPdf = await waitFor('the Open PDF action', () => domHelpers.byTestId('open-citation-pdf'), 30000)
  const citationHref = citationPdf.getAttribute('href') ?? ''
  if (!citationHref.includes('/api/v1/files/')) {
    fail(`Open PDF did not use the file-content endpoint: ${citationHref}`)
  }
  const citationResponse = await nativeFetch(new URL(citationHref, baseUrl))
  if (!citationResponse.ok) {
    fail(`the cited local PDF could not be fetched: HTTP ${citationResponse.status}`)
  }
  const citation = (turn?.answer_citations ?? [])[0]
  if (!citation) {
    fail('the persisted turn exposes no answer citations')
  }
  if (!dialogText.includes(citation.evidence_id)) {
    fail('the citation dialog did not render the API-provided evidence identity')
  }
  result.citationEvidenceId = citation.evidence_id
  result.citationPaperId = citation.paper_id ?? null
  result.citationPages = citation.pages ?? null
  result.citationHref = citationHref
  const evidence = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}/timeline?after_sequence=0`).catch(() => null)
  const evidenceVisible = evidence?.events?.length >= 1
  if (!evidenceVisible) {
    fail('the citation evidence cannot be traced back to the run Timeline')
  }
  pass(`inspected citation ${citation.evidence_id} for Paper ${result.citationPaperId} and opened the cited local PDF`)

  /* --------------------------------------- 8. persisted reload (API only) */

  domHelpers.click(domHelpers.findLink('/library'))
  await waitFor('the Library section', () => domHelpers.byTestId('import-pdf-button'), 20000)
  domHelpers.click(domHelpers.findLink('/research'))
  await waitFor(
    'the persisted conversation, answer, and citations to reload from the API',
    () => {
      const answer = domHelpers.byTestId('turn-final-answer')
      return answer && (answer.textContent ?? '').includes(result.finalAnswer) ? answer : false
    },
    30000,
  )
  await waitFor(
    'the persisted answer citations to reload from the API',
    () => domHelpers.byTestId('answer-citations'),
    30000,
  )
  pass('reopened the Conversation and reloaded the persisted answer and citations from the API')

  /* --------------------------------------------- 9. Wiki (Schema-bound) */

  domHelpers.click(domHelpers.findLink('/wiki'))
  await waitFor('the Wiki view', () => domHelpers.byTestId('wiki-status'), 30000)
  const wiki = await apiGet(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki`)
  const statusBadge = (domHelpers.byTestId('wiki-status-badge')?.textContent ?? '').trim()
  if (statusBadge !== wiki.base_wiki.status) {
    fail(`the Wiki status badge (${statusBadge}) does not match the API (${wiki.base_wiki.status})`)
  }
  const agenticCount = domHelpers.byTestId('wiki-agentic-count')?.textContent ?? ''
  if (!agenticCount.includes(String(wiki.agentic_wiki_entry_count))) {
    fail(`the Wiki Agent Learned count does not match the API: ${agenticCount}`)
  }
  if (!domHelpers.byTestId('wiki-source-schema')) {
    fail('the Schema-bound Wiki does not render its Base Wiki source section')
  }
  const tabCount = (domHelpers.byTestId('wiki-tab-agentic-count')?.textContent ?? '').trim()
  if (tabCount !== String(wiki.agentic_wiki_entry_count)) {
    fail(`the Agent Learned tab count (${tabCount}) does not match the API`)
  }
  domHelpers.clickTestId('wiki-tab-agentic')
  await waitFor(
    'the Agent Learned Wiki source section',
    () => domHelpers.byTestId('wiki-source-agentic'),
    20000,
  )
  const agenticBadge = (domHelpers.byTestId('wiki-agentic-entry-count')?.textContent ?? '').trim()
  if (!agenticBadge.includes(String(wiki.agentic_wiki_entry_count))) {
    fail(`the Agent Learned panel count does not match the API: ${agenticBadge}`)
  }
  domHelpers.clickTestId('wiki-tab-search')
  await waitFor('the Wiki search source section', () => domHelpers.byTestId('wiki-search-input'), 20000)
  if (!domHelpers.byTestId('wiki-search-submit')) {
    fail('the Wiki search form has no submit control')
  }
  domHelpers.setInputValue(domHelpers.requireTestId('wiki-search-input'), 'train scheduling')
  domHelpers.submitTestId('wiki-search-form')
  const searchDeadline = Date.now() + 60000
  const searchPanel = () =>
    domHelpers.byTestId('wiki-panel-search')?.textContent ?? ''
  while (
    !domHelpers.byTestId('wiki-search-summary') &&
    !/WIKI_MISSING/.test(searchPanel()) &&
    Date.now() < searchDeadline
  ) {
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  const hits = domHelpers.allByTestId('wiki-search-hit').length
  if (domHelpers.byTestId('wiki-search-summary')) {
    result.wikiSearchOutcome = 'results'
    if (wiki.agentic_wiki_entry_count > 0 && hits === 0) {
      fail(`the workspace has ${wiki.agentic_wiki_entry_count} Agentic Wiki entries but the UI search returned no hits`)
    }
  } else if (/WIKI_MISSING/.test(searchPanel())) {
    // A workspace whose Base Wiki was never built is a real backend state: the
    // API answers 409 WIKI_MISSING and the UI must surface that instead of
    // inventing search hits.
    result.wikiSearchOutcome = 'WIKI_MISSING'
  } else {
    fail(
      `the Wiki search neither returned results nor surfaced the backend state: ` +
        `${searchPanel().slice(0, 400)}`,
    )
  }
  result.wikiStatus = wiki.base_wiki.status
  result.wikiAgenticCount = wiki.agentic_wiki_entry_count
  pass(
    `read the Schema-bound Wiki (status ${statusBadge}, Base + Agentic sources, search -> ${result.wikiSearchOutcome})`,
  )

  /* ------------------------------------ 10. API-only transport boundary */

  if (nonApiRequests.length > 0) {
    fail(`the UI performed non-API requests during the flow: ${nonApiRequests.join(', ')}`)
  }
  for (const required of ['papers/import', '/timeline', '/papers/', '/conversations', '/wiki']) {
    if (!apiRequests.some((request) => request.includes(required))) {
      fail(`the flow never exercised ${required} through the API`)
    }
  }
  pass(`the whole flow ran over /api/v1 only (${apiRequests.length} UI API requests)`)

  writeResult()
  restoreGlobals()
  window.close()
  process.stdout.write('PASS: real end-to-end local product smoke completed\n')
} catch (error) {
  exitCode = 1
  writeResult()
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

process.exit(exitCode)
