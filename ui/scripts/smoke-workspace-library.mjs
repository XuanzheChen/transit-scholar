/**
 * Live end-to-end DOM smoke for the Workspace and basic Library UI (T-002).
 *
 * Renders the real production bundle (ui/dist) inside jsdom and drives the same
 * user interactions a browser would: create a workspace without a Schema, create
 * a Schema-bound workspace (checking the permanent-binding warning and the
 * read-only binding afterwards), import a PDF through the Library, and add then
 * remove that paper's workspace membership.
 *
 * Every write goes through the built UI against a REAL running local API. The
 * harness only reads the API afterwards to confirm what the UI actually did, so
 * it cannot pass by talking to the backend behind the UI's back.
 *
 * Usage:
 *   node scripts/smoke-workspace-library.mjs [--base http://127.0.0.1:8017]
 *                                            [--dist <dir>] [--pdf <file>]
 *
 * Requires: `npm run build` and a running local API (`uvicorn ...`).
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
const baseUrl = (argValue('--base', process.env.TRANSIT_SCHOLAR_SMOKE_API_BASE ?? 'http://127.0.0.1:8017')).replace(/\/+$/, '')
const pdfPath = path.resolve(
  argValue('--pdf', path.join(process.cwd(), '..', 'tests', 'fixtures', 'metadata', 'causal_reinforcement_learning_train_scheduling.pdf')),
)
const SCHEMA_OPTION = argValue('--schema', 'generic_research_paper@1.0')

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

function applyGlobals(window, fetchImpl) {
  const values = {
    window,
    document: window.document,
    localStorage: window.localStorage,
    location: window.location,
    history: window.history,
    // The bundle must build jsdom FormData; the fetch shim converts it for
    // Node's fetch (which does not accept jsdom File values).
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

  function fire(element, type) {
    element.dispatchEvent(new window.MouseEvent(type, { bubbles: true, cancelable: true, view: window }))
  }

  function click(element) {
    if (!element) {
      fail('attempted to click a missing element')
    }
    fire(element, 'click')
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

  function setFileInput(input, file) {
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
    input.dispatchEvent(new window.Event('change', { bubbles: true }))
  }

  return { document, byTestId, allByTestId, text, requireTestId, findLink, click, clickTestId, setInputValue, setSelectValue, setFileInput }
}

/* ------------------------------------------------------------------- setup */

const unique = Date.now().toString(36)
const noSchemaName = `Smoke no schema ${unique}`
const schemaName = `Smoke schema bound ${unique}`

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
const dom = new JSDOM(html, { url: `${baseUrl}/workspaces`, pretendToBeVisual: true })
const { window } = dom
applyGlobals(window, createFetchShim())
const domHelpers = makeDomHelpers(window)

const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-workspace-smoke-'))
const bundleTarget = path.join(scratch, `bundle-${unique}.mjs`)
copyFileSync(bundleFile, bundleTarget)

let exitCode = 0
try {
  await import(pathToFileURL(bundleTarget).href)

  await waitFor('the Workspaces section to load', () => domHelpers.text().includes('New workspace'), 15000)
  if (domHelpers.text().includes('Backend unavailable')) {
    fail(`the UI cannot reach the API at ${baseUrl}`)
  }

  /* ------------------------------------------- 1. workspace without Schema */

  domHelpers.clickTestId('new-workspace-button')
  await waitFor('the new-workspace dialog', () => domHelpers.byTestId('create-workspace-dialog'))
  domHelpers.setInputValue(domHelpers.requireTestId('workspace-name-input'), noSchemaName)
  domHelpers.clickTestId('schema-mode-none')
  domHelpers.clickTestId('create-workspace-submit')

  const noSchemaSettings = await waitFor(
    'the no-Schema workspace settings view',
    () => window.location.pathname.startsWith('/workspaces/') && domHelpers.byTestId('workspace-schema-settings'),
    20000,
  )
  if (!noSchemaSettings) {
    fail('the no-Schema workspace settings view was not rendered')
  }
  const noSchemaId = decodeURIComponent(window.location.pathname.split('/')[2] ?? '')
  if (!noSchemaId) {
    fail('creating a workspace did not open its settings route')
  }
  const noSchemaRecord = await apiGet(`/api/v1/workspaces/${encodeURIComponent(noSchemaId)}`)
  if (noSchemaRecord.name !== noSchemaName || noSchemaRecord.schema_mode !== 'none') {
    fail(`no-Schema workspace was not persisted as expected: ${JSON.stringify(noSchemaRecord)}`)
  }
  if (!domHelpers.text().includes('This workspace has no Schema binding')) {
    fail('no-Schema workspace settings did not explain the normal no-Schema state')
  }
  pass('created a workspace without a Schema through the UI')

  /* ----------------------------------------------- 2. Schema-bound workspace */

  domHelpers.click(domHelpers.findLink('/workspaces'))
  await waitFor('the workspace list', () => domHelpers.text().includes(noSchemaName))
  domHelpers.clickTestId('new-workspace-button')
  await waitFor('the new-workspace dialog', () => domHelpers.byTestId('create-workspace-dialog'))
  domHelpers.setInputValue(domHelpers.requireTestId('workspace-name-input'), schemaName)
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
    () => window.location.pathname.startsWith('/workspaces/') && window.location.pathname !== `/workspaces/${noSchemaId}` && domHelpers.byTestId('workspace-schema-version'),
    20000,
  )

  const schemaId = decodeURIComponent(window.location.pathname.split('/')[2] ?? '')
  const schemaRecord = await apiGet(`/api/v1/workspaces/${encodeURIComponent(schemaId)}`)
  if (schemaRecord.schema_mode === 'none' || !schemaRecord.schema_binding) {
    fail(`Schema-bound workspace has no binding: ${JSON.stringify(schemaRecord)}`)
  }
  const expectedVersion = schemaOption.split('@')[1] ?? schemaRecord.schema_binding.schema_version
  const settingsBlock = domHelpers.requireTestId('workspace-schema-settings')
  const shownVersion = domHelpers.requireTestId('workspace-schema-version').textContent?.trim()
  if (shownVersion !== schemaRecord.schema_binding.schema_version) {
    fail(`settings show version ${shownVersion} but the API reports ${schemaRecord.schema_binding.schema_version}`)
  }
  if (schemaRecord.schema_binding.schema_version !== expectedVersion) {
    fail('the selected Schema version was not the one bound')
  }
  if (!domHelpers.byTestId('workspace-schema-immutable')) {
    fail('the settings view did not mark the Schema binding as non-editable')
  }
  if (settingsBlock.querySelector('select, input')) {
    fail('the settings view offers an editable control for the permanent Schema binding')
  }
  if (!/cannot be changed/i.test(settingsBlock.textContent ?? '')) {
    fail('the settings view did not state that the Schema binding is immutable')
  }
  pass('created a Schema-bound workspace through the UI with a visible permanent-binding warning')

  /* --------------------------------------------------------- 3. PDF import */

  domHelpers.click(domHelpers.findLink('/library'))
  await waitFor('the Library section', () => domHelpers.byTestId('import-pdf-button'), 15000)
  domHelpers.clickTestId('import-pdf-button')
  await waitFor('the import dialog', () => domHelpers.byTestId('import-paper-dialog'))

  const fileBuffer = readFileSync(pdfPath)
  const jsdomFile = new window.File([new Uint8Array(fileBuffer)], path.basename(pdfPath), { type: 'application/pdf' })
  domHelpers.setFileInput(domHelpers.requireTestId('import-paper-file-input'), jsdomFile)
  domHelpers.clickTestId('import-paper-submit')

  // A fresh PDF navigates straight to the new paper. Importing the same PDF
  // again is reported by the API as a duplicate, and the dialog then offers the
  // existing paper instead of navigating on its own. Both are normal outcomes.
  const importResult = await waitFor(
    'the imported paper detail view or an import outcome',
    () => {
      if (window.location.pathname.startsWith('/library/') && domHelpers.byTestId('paper-status')) {
        return { kind: 'detail' }
      }
      const outcome = domHelpers.byTestId('import-paper-outcome')
      return outcome ? { kind: 'outcome', element: outcome } : false
    },
    180000,
  )

  if (importResult.kind === 'outcome') {
    const outcomeText = (importResult.element.textContent ?? '').trim()
    const openExisting = importResult.element.querySelector('button')
    if (!openExisting) {
      fail(`the import did not produce a paper: ${outcomeText}`)
    }
    domHelpers.click(openExisting)
    await waitFor(
      'the existing paper detail view',
      () => window.location.pathname.startsWith('/library/') && domHelpers.byTestId('paper-status'),
      30000,
    )
    pass(`the PDF was already in the Library; the UI opened the existing paper (${outcomeText})`)
  }

  const paperId = decodeURIComponent(window.location.pathname.split('/')[2] ?? '')
  if (!paperId) {
    fail('the import did not open the resulting paper')
  }
  const library = await apiGet('/api/v1/papers?limit=500')
  if (!library.items.some((item) => item.paper_id === paperId)) {
    fail(`the imported paper ${paperId} is not listed by GET /api/v1/papers`)
  }
  pass(`imported a PDF through the UI and it appears in the Library (${paperId})`)

  /* ------------------------------------------------- 4. Workspace membership */

  await waitFor('the membership control', () => domHelpers.byTestId('paper-workspace-membership'), 15000)
  await waitFor(
    'the membership state to report "Not in this workspace"',
    () => (domHelpers.byTestId('paper-membership-state')?.textContent ?? '').includes('Not in this workspace'),
    15000,
  )

  domHelpers.clickTestId('add-paper-to-workspace')
  await waitFor(
    'the membership state to report "In this workspace"',
    () => (domHelpers.byTestId('paper-membership-state')?.textContent ?? '').includes('In this workspace'),
    20000,
  )
  const afterAdd = await apiGet(`/api/v1/workspaces/${encodeURIComponent(schemaId)}/papers`)
  if (!afterAdd.items.some((item) => item.paper_id === paperId)) {
    fail('the UI reported membership but the workspace papers API does not list the paper')
  }
  pass('added the imported paper to the Schema-bound workspace through the UI')

  domHelpers.clickTestId('remove-paper-from-workspace')
  await waitFor(
    'the membership state to report "Not in this workspace" again',
    () => (domHelpers.byTestId('paper-membership-state')?.textContent ?? '').includes('Not in this workspace'),
    20000,
  )
  const afterRemove = await apiGet(`/api/v1/workspaces/${encodeURIComponent(schemaId)}/papers`)
  if (afterRemove.items.some((item) => item.paper_id === paperId)) {
    fail('the paper is still a workspace member after the UI remove action')
  }
  const libraryAfterRemove = await apiGet('/api/v1/papers?limit=500')
  if (!libraryAfterRemove.items.some((item) => item.paper_id === paperId)) {
    fail('removing the paper from the workspace also removed it from the global Library')
  }
  pass('removed the paper from the workspace; it remains in the global Library')

  /* -------------------------------------------- 5. Library list still shows it */

  domHelpers.click(domHelpers.findLink('/library'))
  await waitFor(
    'the imported paper card in the Library list',
    () => domHelpers.byTestId(`paper-card-${paperId}`),
    20000,
  )
  pass('imported paper is listed in the Library after membership removal')

  restoreGlobals()
  process.stdout.write('PASS: workspace and basic Library UI smoke completed\n')
} catch (error) {
  exitCode = 1
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
  window.close()
}

process.exit(exitCode)
