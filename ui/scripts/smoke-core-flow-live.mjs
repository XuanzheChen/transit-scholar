/**
 * Live end-to-end DOM smoke for the complete UI-S1 core research flow (T-005).
 *
 * Renders the real production bundle (ui/dist) inside a FRESH jsdom session
 * (empty browser storage, no prior navigation) and drives the same interactions
 * a browser would against a REAL running single-origin local server:
 *
 *   1. create a Schema-bound workspace (permanent-binding warning + read-only
 *      binding) and a workspace without a Schema;
 *   2. import a PDF through the Library and add that Paper to the open
 *      workspace;
 *   3. create a Conversation, submit a prompt, and watch the AgentRun status and
 *      research Timeline while the run is still active;
 *   4. request pause, observe the Pausing state, resume the same AgentRun;
 *   5. read the final answer with the Timeline collapsed, inspect an answer
 *      citation, and open the cited local PDF;
 *   6. open the workspace Wiki and read its API-derived status.
 *
 * Every write goes through the built UI. The only direct API access is
 * afterwards and read-only, to confirm what the UI already did. The fetch shim
 * fails the run if the UI ever performs a request outside `/api/v1/*`, which is
 * the machine-checkable form of "the flow needs no direct Product/Core
 * invocation".
 *
 * Usage:
 *   node scripts/smoke-core-flow-live.mjs [--base http://127.0.0.1:8017]
 *                                         [--dist <dir>] [--pdf <file>]
 *                                         [--schema generic_research_paper@1.0]
 *                                         [--release-file <path>]
 *
 * `--release-file` is written after the UI has requested pause. The scripted
 * integration server holds the in-flight research action until that file
 * appears, so the pause/resume window is deterministic rather than timing-based.
 *
 * Requires: `npm run build` and a running local API with an available agent
 * runtime (`uvicorn ...`).
 */
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
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
const releaseFile = argValue('--release-file', null)
const resultFile = argValue('--result-file', null)
const prompt = argValue('--prompt', 'What do the workspace papers report about transit interventions?')

const NativeFormData = globalThis.FormData
const NativeFile = globalThis.File
const nativeFetch = globalThis.fetch.bind(globalThis)

function fail(message) {
  process.stderr.write(`FAIL: ${message}\n`)
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

/** Direct API access used ONLY to confirm what the UI did, never to mutate. */
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

  /** The workspace card whose title matches `name`. */
  function workspaceCard(name) {
    const list = byTestId('workspace-list')
    if (!list) {
      return null
    }
    return Array.from(list.querySelectorAll('li.card')).find(
      (item) => (item.querySelector('.card__title')?.textContent ?? '').trim() === name,
    ) ?? null
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
    setInputValue,
    setSelectValue,
    setFileInput,
    workspaceCard,
  }
}

/* ------------------------------------------------------------------- setup */

const unique = Date.now().toString(36)
const schemaWorkspaceName = `Core flow schema ${unique}`
const coreWorkspaceName = `Core flow ${unique}`
const conversationTitle = `Core flow conversation ${unique}`

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

const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-core-flow-'))
const bundleTarget = path.join(scratch, `bundle-${unique}.mjs`)
copyFileSync(bundleFile, bundleTarget)

let runId = null
let exitCode = 0
// Structured outcome of the flow, written for the integration gate to
// cross-check the API-visible identifiers the UI actually produced.
const result = {
  schemaWorkspaceId: null,
  coreWorkspaceId: null,
  paperId: null,
  conversationId: null,
  runId: null,
  citationEvidenceId: null,
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
  const navText = domHelpers.document.querySelector('.sidebar__nav')?.textContent ?? ''
  for (const label of ['Workspaces', 'Research', 'Library', 'Wiki', 'Schemas']) {
    if (!navText.includes(label)) {
      fail(`product navigation does not expose ${label}`)
    }
  }
  for (const internal of ['Layer1', 'Layer2', 'Layer3', 'Ledger', 'RoleRuntime', 'ResearchSession']) {
    if (navText.includes(internal)) {
      fail(`product navigation exposes the internal concept ${internal}`)
    }
  }
  pass('fresh session: backend connected and product navigation is user-facing')

  /* ------------------------------------ 1. Schema-bound workspace (AC-003) */

  domHelpers.clickTestId('new-workspace-button')
  await waitFor('the new-workspace dialog', () => domHelpers.byTestId('create-workspace-dialog'))
  domHelpers.setInputValue(domHelpers.requireTestId('workspace-name-input'), schemaWorkspaceName)
  domHelpers.clickTestId('schema-mode-schema')

  const warning = await waitFor(
    'the permanent Schema binding warning',
    () => domHelpers.byTestId('schema-binding-warning'),
    15000,
  )
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
  const schemaWorkspaceId = decodeURIComponent(window.location.pathname.split('/')[2] ?? '')
  result.schemaWorkspaceId = schemaWorkspaceId
  const schemaRecord = await apiGet(`/api/v1/workspaces/${encodeURIComponent(schemaWorkspaceId)}`)
  if (schemaRecord.schema_mode === 'none' || !schemaRecord.schema_binding) {
    fail(`the Schema-bound workspace has no binding: ${JSON.stringify(schemaRecord)}`)
  }
  const settingsBlock = domHelpers.requireTestId('workspace-schema-settings')
  if (settingsBlock.querySelector('select, input, textarea')) {
    fail('the workspace settings offer an editable control for the permanent Schema binding')
  }
  if (!domHelpers.byTestId('workspace-schema-immutable')) {
    fail('the workspace settings did not mark the Schema binding as non-editable')
  }
  pass(`created a Schema-bound workspace through the UI (${schemaWorkspaceId})`)

  /* ---------------------------------------- 2. no-Schema workspace (primary) */

  domHelpers.click(domHelpers.findLink('/workspaces'))
  await waitFor('the workspace list to include the new workspace', () => domHelpers.workspaceCard(schemaWorkspaceName), 20000)
  domHelpers.clickTestId('new-workspace-button')
  await waitFor('the new-workspace dialog', () => domHelpers.byTestId('create-workspace-dialog'))
  domHelpers.setInputValue(domHelpers.requireTestId('workspace-name-input'), coreWorkspaceName)
  domHelpers.clickTestId('schema-mode-none')
  domHelpers.clickTestId('create-workspace-submit')

  await waitFor(
    'the no-Schema workspace settings view',
    () => window.location.pathname.startsWith('/workspaces/') && domHelpers.byTestId('workspace-schema-settings'),
    20000,
  )
  const coreWorkspaceId = decodeURIComponent(window.location.pathname.split('/')[2] ?? '')
  result.coreWorkspaceId = coreWorkspaceId
  if (!coreWorkspaceId || coreWorkspaceId === schemaWorkspaceId) {
    fail('creating the no-Schema workspace did not open its own settings route')
  }
  const coreRecord = await apiGet(`/api/v1/workspaces/${encodeURIComponent(coreWorkspaceId)}`)
  if (coreRecord.schema_mode !== 'none' || !coreRecord.name.includes(coreWorkspaceName)) {
    fail(`the no-Schema workspace was not persisted as expected: ${JSON.stringify(coreRecord)}`)
  }
  if (!domHelpers.text().includes('This workspace has no Schema binding')) {
    fail('the no-Schema workspace settings did not explain the normal no-Schema state')
  }
  pass(`created the primary workspace without a Schema (${coreWorkspaceId})`)

  /* ------------------------------------------------ 3. PDF import (AC-009) */

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
  const library = await apiGet('/api/v1/papers?limit=500')
  if (!library.items.some((item) => item.paper_id === paperId)) {
    fail(`the imported paper ${paperId} is not listed by GET /api/v1/papers`)
  }
  await waitFor(
    'the paper readiness state to resolve to an explicit backend-reported state',
    () => {
      const readiness = domHelpers.byTestId('paper-readiness')
      if (!readiness) {
        return false
      }
      const readinessText = readiness.textContent ?? ''
      return (
        readiness.querySelector('.badge') !== null ||
        readinessText.includes('does not report readiness') ||
        readinessText.includes('can be used for research')
      )
    },
    30000,
  )
  pass(`imported a PDF through the Library and opened its paper detail (${paperId})`)

  /* ------------------------------- 4. workspace membership + local PDF (AC-009) */

  await waitFor('the membership control', () => domHelpers.byTestId('paper-workspace-membership'), 20000)
  await waitFor(
    'the membership state to report "Not in this workspace"',
    () => (domHelpers.byTestId('paper-membership-state')?.textContent ?? '').includes('Not in this workspace'),
    20000,
  )
  domHelpers.clickTestId('add-paper-to-workspace')
  await waitFor(
    'the membership state to report "In this workspace"',
    () => (domHelpers.byTestId('paper-membership-state')?.textContent ?? '').includes('In this workspace'),
    30000,
  )
  const membership = await apiGet(`/api/v1/workspaces/${encodeURIComponent(coreWorkspaceId)}/papers`)
  if (!membership.items.some((item) => item.paper_id === paperId)) {
    fail('the UI reported membership but the workspace papers API does not list the paper')
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
  pass('added the Paper to the open workspace and opened its registered local PDF')

  /* --------------------- 4b. workspace view reflects the membership (AC-009) */

  domHelpers.click(domHelpers.findLink('/workspaces'))
  await waitFor('the workspace list', () => domHelpers.workspaceCard(coreWorkspaceName), 20000)
  domHelpers.click(domHelpers.findLink(`/workspaces/${encodeURIComponent(coreWorkspaceId)}`))
  await waitFor(
    'the workspace settings to list the added paper',
    () => domHelpers.byTestId(`remove-paper-${paperId}`),
    30000,
  )
  const addCandidateSelect = domHelpers.byTestId('add-workspace-paper-select')
  if (
    addCandidateSelect &&
    Array.from(addCandidateSelect.options).some((option) => option.value === paperId)
  ) {
    fail('the workspace lists the paper as a member but still offers it as an addable candidate')
  }
  const workspaceMembers = await apiGet(`/api/v1/workspaces/${encodeURIComponent(coreWorkspaceId)}/papers`)
  if (workspaceMembers.items.length !== 1) {
    fail(`the workspace should hold exactly the added paper, the API reports ${workspaceMembers.items.length}`)
  }
  pass('the workspace settings reflect the same membership as the Library')

  /* ------------------------- 5. Conversation, prompt, live run (AC-004/AC-005) */

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
    30000,
  )
  const conversation = await apiGet(`/api/v1/conversations/${encodeURIComponent(conversationId)}`)
  runId = conversation.turns[conversation.turns.length - 1]?.agent_run_id ?? null
  if (!runId) {
    fail('the submitted turn has no AgentRun identifier')
  }
  result.runId = runId
  const earlyState = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
  if (earlyState.status === 'completed' || earlyState.status === 'failed') {
    fail('the run finished before the UI could monitor it; the flow was not exercised')
  }
  await waitFor(
    'the first Timeline events',
    () => domHelpers.document.querySelectorAll('[data-testid^="timeline-event-"]').length >= 1,
    60000,
  )
  const timeline = domHelpers.byTestId('run-timeline')
  if (!timeline || timeline.open !== true) {
    fail('the research Timeline was not expanded while the run is active')
  }
  pass(`submitted a prompt and observed the active run with a live Timeline (${runId})`)

  /* --------------------------------------------- 6. pause and resume (AC-007) */

  const pauseControl = await waitFor(
    'the primary control to offer Pause for the running AgentRun',
    () => {
      const control = domHelpers.byTestId('research-primary-control')
      return control && control.getAttribute('data-control') === 'pause' && !control.disabled ? control : false
    },
    60000,
  )
  if (!pauseControl) {
    fail('the primary control never offered Pause for a running AgentRun')
  }
  domHelpers.clickTestId('research-primary-control')
  const afterPauseClick = await waitFor(
    'the primary control to leave the Pause state',
    () => {
      const control = domHelpers.byTestId('research-primary-control')
      return control && control.getAttribute('data-control') !== 'pause' ? control : false
    },
    60000,
  )
  if (releaseFile) {
    writeFileSync(releaseFile, 'release', 'utf8')
  }
  if (afterPauseClick.getAttribute('data-control') === 'pausing') {
    pass('requesting pause produced a visible cooperative Pausing state')
  } else {
    pass(`the API reported the paused run before the Pausing state was observed (${afterPauseClick.getAttribute('data-control')})`)
  }
  await waitFor(
    'the primary control to offer Resume after the API reported paused',
    () => {
      const control = domHelpers.byTestId('research-primary-control')
      return control && control.getAttribute('data-control') === 'resume' ? control : false
    },
    180000,
  )
  const pausedState = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
  if (pausedState.status !== 'paused') {
    fail(`the UI offered Resume but the API reports ${pausedState.status}`)
  }
  domHelpers.clickTestId('research-primary-control')
  await waitFor(
    'the resumed AgentRun to leave the paused state',
    () => {
      const control = domHelpers.byTestId('research-primary-control')
      return control && control.getAttribute('data-control') !== 'resume'
    },
    60000,
  )
  const resumedState = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
  if (resumedState.agent_run_id !== runId) {
    fail('resume replaced the AgentRun instead of continuing the same run')
  }
  pass('paused and resumed the same AgentRun through the single primary control')

  /* --------------------------- 7. final answer + collapsed Timeline (AC-006) */

  await waitFor(
    'the final answer to be displayed',
    () => {
      const answer = domHelpers.byTestId('turn-final-answer')
      return answer && (answer.textContent ?? '').trim().length > 0 ? answer : false
    },
    300000,
  )
  const finalRun = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
  if (finalRun.status !== 'completed') {
    fail(`expected a completed run, the API reports ${finalRun.status}`)
  }
  const completedTimeline = domHelpers.byTestId(`turn-timeline-${runId}`)
  if (!completedTimeline) {
    fail('the completed turn does not keep its research Timeline available')
  }
  if (completedTimeline.open !== false) {
    fail('the completed research Timeline was not collapsed by default')
  }
  // The collapsed Timeline stays available: opening it fetches and renders the
  // real projected events for the finished run.
  completedTimeline.open = true
  completedTimeline.dispatchEvent(new window.Event('toggle'))
  await waitFor(
    'the completed turn Timeline to render its events on demand',
    () => completedTimeline.querySelectorAll('[data-testid^="timeline-event-"]').length >= 1,
    30000,
  )
  pass('received the final answer with the research Timeline collapsed by default and available on demand')

  /* --------------------------------------- 8. citations and cited PDF (AC-008) */

  if (!domHelpers.byTestId('answer-citations')) {
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
  const citationApi = await apiGet(`/api/v1/conversations/${encodeURIComponent(conversationId)}`)
  const persistedTurn = citationApi.turns[citationApi.turns.length - 1]
  if ((persistedTurn?.user_message ?? '') !== prompt) {
    fail('the persisted turn does not carry the submitted prompt')
  }
  const citation = (persistedTurn?.answer_citations ?? [])[0]
  if (!citation || !dialogText.includes(citation.evidence_id)) {
    fail('the citation dialog did not render the API-provided evidence identity')
  }
  result.citationEvidenceId = citation.evidence_id
  pass('inspected an answer citation and opened the cited local PDF')

  /* ----------------- 9. persisted turns reload from the API only (AC-004) */

  const persistedAnswer = persistedTurn?.final_answer ?? ''
  if (!persistedAnswer) {
    fail('the persisted turn has no final answer to reload')
  }
  domHelpers.click(domHelpers.findLink('/library'))
  await waitFor('the Library section', () => domHelpers.byTestId('import-pdf-button'), 20000)
  domHelpers.click(domHelpers.findLink('/research'))
  await waitFor(
    'the persisted conversation, answer, and citations to reload from the API',
    () => {
      const answer = domHelpers.byTestId('turn-final-answer')
      const turns = domHelpers.byTestId('conversation-turns')
      return turns && answer && (answer.textContent ?? '').includes(persistedAnswer) ? answer : false
    },
    30000,
  )
  if (!domHelpers.byTestId('answer-citations')) {
    fail('reopening the conversation lost the persisted answer citations')
  }
  pass('reopened the conversation and reloaded the persisted turn, answer, and citations from the API')

  /* ----------------------------------------- 10. Wiki, no-Schema (AC-012) */

  domHelpers.click(domHelpers.findLink('/wiki'))
  await waitFor('the Wiki view', () => domHelpers.byTestId('wiki-status'), 30000)
  const coreOverview = await apiGet(`/api/v1/workspaces/${encodeURIComponent(coreWorkspaceId)}/wiki`)
  const coreStatusBadge = domHelpers.byTestId('wiki-status-badge')?.textContent?.trim()
  if (coreStatusBadge !== coreOverview.base_wiki.status) {
    fail(`the Wiki status badge (${coreStatusBadge}) does not match the API (${coreOverview.base_wiki.status})`)
  }
  if (!domHelpers.byTestId('wiki-schema-unavailable')) {
    fail('the no-Schema workspace Wiki did not explain that Schema Wiki is unavailable')
  }
  const unavailableBlock = domHelpers.byTestId('wiki-schema-unavailable')
  if (!unavailableBlock.querySelector('.state-block--unavailable')) {
    fail('the no-Schema workspace Wiki did not render the normal unavailable state')
  }
  if (unavailableBlock.querySelector('.state-block--error')) {
    fail('the no-Schema workspace Wiki presented the unsupported Schema Wiki as an application error')
  }
  const agenticCount = domHelpers.byTestId('wiki-agentic-count')?.textContent ?? ''
  if (!agenticCount.includes(String(coreOverview.agentic_wiki_entry_count))) {
    fail(`the Wiki Agent Learned count does not match the API: ${agenticCount}`)
  }
  pass('opened the workspace Wiki and read the API-derived no-Schema state')

  /* --------------------------------- 11. Wiki, Schema-bound workspace (AC-011) */

  domHelpers.click(domHelpers.findLink('/workspaces'))
  const schemaCard = await waitFor('the Schema-bound workspace card', () => domHelpers.workspaceCard(schemaWorkspaceName), 20000)
  const openButton = Array.from(schemaCard.querySelectorAll('button')).find((button) =>
    (button.textContent ?? '').includes('Open workspace'),
  )
  domHelpers.click(openButton)
  await waitFor('the Research view of the Schema-bound workspace', () => domHelpers.byTestId('research-view'), 30000)
  domHelpers.click(domHelpers.findLink('/wiki'))
  await waitFor('the Wiki view of the Schema-bound workspace', () => domHelpers.byTestId('wiki-status'), 30000)
  const schemaOverview = await apiGet(`/api/v1/workspaces/${encodeURIComponent(schemaWorkspaceId)}/wiki`)
  const schemaStatusBadge = domHelpers.byTestId('wiki-status-badge')?.textContent?.trim()
  if (schemaStatusBadge !== schemaOverview.base_wiki.status) {
    fail(`the Schema-bound Wiki status badge (${schemaStatusBadge}) does not match the API (${schemaOverview.base_wiki.status})`)
  }
  const readCapability = (domHelpers.byTestId('wiki-read-capability')?.textContent ?? '').trim()
  const expectedRead = schemaOverview.base_wiki_capability.read_supported ? 'Readable' : 'Not readable'
  if (readCapability !== expectedRead) {
    fail(`the Wiki read capability (${readCapability}) does not match the API (${expectedRead})`)
  }
  pass('opened the Schema-bound workspace Wiki and read its API-derived capability and status')

  /* ------------------------------ 12. API-only transport boundary (C-001) */

  if (nonApiRequests.length > 0) {
    fail(`the UI performed non-API requests during the flow: ${nonApiRequests.join(', ')}`)
  }
  for (const required of ['papers/import', '/pause', '/resume', '/timeline']) {
    if (!apiRequests.some((request) => request.includes(required))) {
      fail(`the flow never exercised ${required} through the API`)
    }
  }
  pass(`the whole flow ran over /api/v1 only (${apiRequests.length} UI API requests)`)

  writeResult()
  restoreGlobals()
  window.close()
  process.stdout.write('PASS: UI-S1 core research flow smoke completed\n')
} catch (error) {
  exitCode = 1
  writeResult()
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

process.exit(exitCode)
