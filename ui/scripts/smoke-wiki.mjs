/**
 * Live end-to-end DOM smoke for the Workspace Wiki UI (T-004).
 *
 * Renders the real production bundle (ui/dist) inside jsdom and drives the same
 * user interactions a browser would:
 *
 *   Scenario A — a Workspace created WITHOUT a Schema:
 *     the Wiki view explains that Schema Wiki content does not exist for it as a
 *     normal product state (no application error), the Agent Learned source
 *     renders from the API, and a Wiki search always produces an explicit,
 *     API-derived outcome instead of a silent/blank panel.
 *
 *   Scenario B — a Workspace created WITH an existing Schema, plus a real PDF
 *     imported through the Library and added to the Workspace:
 *     the Wiki status panel reports the exact status the Wiki API returns, the
 *     Schema Wiki content state (not built yet, with its build control) is
 *     represented, and no application error is shown.
 *
 *   Scenario C (optional, `--agentic-workspace <workspace-id>`) — a Workspace
 *     that already has Agent Learned knowledge:
 *     Agent Learned entries are listed, a Wiki search returns real structured
 *     hits tagged with their knowledge source, and a hit opens its detail view.
 *
 * All user-visible evidence is produced by the built UI against a REAL running
 * local API. The harness only reads the API afterwards to confirm what the UI
 * displayed; it never mutates the backend behind the UI's back.
 *
 * Usage:
 *   node scripts/smoke-wiki.mjs [--base http://127.0.0.1:8017]
 *                               [--dist <dir>] [--pdf <file>]
 *                               [--schema generic_research_paper@1.0]
 *                               [--agentic-workspace <workspace-id>]
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
const baseUrl = (
  argValue('--base', process.env.TRANSIT_SCHOLAR_SMOKE_API_BASE ?? 'http://127.0.0.1:8017')
).replace(/\/+$/, '')
const pdfPath = path.resolve(
  argValue(
    '--pdf',
    path.join(
      process.cwd(),
      '..',
      'tests',
      'fixtures',
      'metadata',
      'causal_reinforcement_learning_train_scheduling.pdf',
    ),
  ),
)
const SCHEMA_OPTION = argValue('--schema', 'generic_research_paper@1.0')
const AGENTIC_WORKSPACE = argValue('--agentic-workspace', '')
/** Must match ACTIVE_WORKSPACE_STORAGE_KEY in the app. */
const ACTIVE_WORKSPACE_KEY = 'transit-scholar.ui.active-workspace-id'

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

/** Direct API access used ONLY to confirm what the UI displayed. */
async function apiGet(pathname) {
  const response = await nativeFetch(`${baseUrl}${pathname}`)
  if (!response.ok) {
    fail(`GET ${pathname} -> HTTP ${response.status}`)
  }
  return response.json()
}

/* ------------------------------------------------------------- DOM plumbing */

const savedGlobals = new Map()

function applyGlobals(window, fetchImpl) {
  const values = {
    window,
    document: window.document,
    localStorage: window.localStorage,
    location: window.location,
    history: window.history,
    // The bundle must build jsdom FormData for PDF import; the fetch shim
    // converts it for Node's fetch (which rejects jsdom File values).
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
      form.append(
        key,
        new NativeFile([buffer], value.name || 'upload.pdf', { type: value.type || 'application/pdf' }),
      )
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
    element.dispatchEvent(
      new window.MouseEvent('click', { bubbles: true, cancelable: true, view: window }),
    )
  }

  function clickTestId(id) {
    click(requireTestId(id))
  }

  function clickLink(href) {
    const link = findLink(href)
    if (!link) {
      fail(`no link to ${href} was rendered`)
    }
    click(link)
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

  function submitForm(form) {
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }))
  }

  /** The error block is the shared, API-envelope-driven failure state. */
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
    text,
    requireTestId,
    findLink,
    click,
    clickTestId,
    clickLink,
    setInputValue,
    setSelectValue,
    setFileInput,
    submitForm,
    applicationError,
    assertNoApplicationError,
  }
}

/* ------------------------------------------------------------ app scenarios */

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-wiki-smoke-'))
const unique = Date.now().toString(36)
let scenarioCount = 0

/**
 * Open the real built app in a fresh jsdom window and run one scenario.
 *
 * `activeWorkspaceId` is a navigation selection only: it is stored the same way
 * the app stores the user's open Workspace, and every backend value is still
 * fetched from the API by the app.
 */
async function withApp({ path: initialPath, activeWorkspaceId }, run) {
  scenarioCount += 1
  const dom = new JSDOM(html, { url: `${baseUrl}${initialPath}`, pretendToBeVisual: true })
  const { window } = dom
  if (activeWorkspaceId) {
    window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, activeWorkspaceId)
  }
  applyGlobals(window, createFetchShim())
  const dom_ = makeDomHelpers(window)

  const target = path.join(scratch, `bundle-${unique}-${scenarioCount}.mjs`)
  copyFileSync(bundleFile, target)

  try {
    await import(pathToFileURL(target).href)
    await waitFor('the application shell to load', () => dom_.text().includes('TransitScholar'), 15000)
    if (dom_.text().includes('Backend unavailable')) {
      fail(`the UI cannot reach the API at ${baseUrl}`)
    }
    await run(window, dom_)
  } finally {
    restoreGlobals()
    window.close()
  }
}

/** Create a Workspace through the real UI and return its id. */
async function createWorkspaceThroughUi(dom_, name, schemaBound) {
  await waitFor('the Workspaces section', () => dom_.byTestId('new-workspace-button'), 15000)
  dom_.clickTestId('new-workspace-button')
  await waitFor('the new-workspace dialog', () => dom_.byTestId('create-workspace-dialog'))
  dom_.setInputValue(dom_.requireTestId('workspace-name-input'), name)

  if (!schemaBound) {
    dom_.clickTestId('schema-mode-none')
    dom_.clickTestId('create-workspace-submit')
    await waitFor(
      'the no-Schema workspace settings view',
      () =>
        dom_.window.location.pathname.startsWith('/workspaces/') &&
        dom_.byTestId('workspace-schema-settings'),
      20000,
    )
  } else {
    dom_.clickTestId('schema-mode-schema')
    const select = await waitFor(
      'the Schema catalog to load options',
      () => {
        const candidate = dom_.byTestId('schema-select')
        return candidate && candidate.options.length > 1 ? candidate : false
      },
      20000,
    )
    const optionValues = Array.from(select.options).map((option) => option.value)
    const schemaOption = optionValues.find((value) => value === SCHEMA_OPTION) ?? optionValues[1]
    dom_.setSelectValue(select, schemaOption)
    dom_.clickTestId('create-workspace-submit')
    await waitFor(
      'the Schema-bound workspace settings view',
      () =>
        dom_.window.location.pathname.startsWith('/workspaces/') &&
        dom_.byTestId('workspace-schema-version'),
      20000,
    )
  }

  const workspaceId = decodeURIComponent(dom_.window.location.pathname.split('/')[2] ?? '')
  if (!workspaceId) {
    fail(`creating workspace "${name}" did not open its settings route`)
  }
  return workspaceId
}

/** Submit the Wiki search form and return the panel's explicit outcome. */
async function submitWikiSearch(dom_, term) {
  dom_.clickTestId('wiki-tab-search')
  await waitFor('the Wiki search form', () => dom_.byTestId('wiki-search-input'), 20000)
  dom_.setInputValue(dom_.requireTestId('wiki-search-input'), term)
  await waitFor(
    'the search term to register',
    () => !dom_.requireTestId('wiki-search-submit').disabled,
    10000,
  )
  // jsdom does not reliably perform implicit form submission from a submit
  // button click, so the real submit event is dispatched on the form instead.
  dom_.submitForm(dom_.requireTestId('wiki-search-form'))

  return waitFor(
    'the Wiki search outcome',
    () => {
      if (dom_.byTestId('wiki-search-results')) {
        return 'results'
      }
      if (dom_.text().includes('No matching knowledge')) {
        return 'empty'
      }
      if (dom_.applicationError()) {
        return 'error'
      }
      return false
    },
    30000,
  )
}

/* -------------------------------------------------------------------- main */

let exitCode = 0
try {
  /* ------------------------------------------ A. Workspace without Schema */

  await withApp({ path: '/workspaces' }, async (window, dom_) => {
    const workspaceId = await createWorkspaceThroughUi(
      dom_,
      `Smoke wiki no schema ${unique}`,
      false,
    )
    dom_.clickLink('/wiki')
    await waitFor('the Wiki status panel', () => dom_.byTestId('wiki-status'), 20000)

    const unavailable = await waitFor(
      'the Schema Wiki unavailable explanation',
      () => dom_.byTestId('wiki-schema-unavailable'),
      20000,
    )
    const unavailableText = unavailable.textContent ?? ''
    if (!/created without a Schema/i.test(unavailableText)) {
      fail(`the no-Schema Wiki state did not explain the missing Schema binding: ${unavailableText}`)
    }
    if (!/normal workspace state/i.test(unavailableText)) {
      fail(`the no-Schema Wiki state did not present itself as a normal state: ${unavailableText}`)
    }
    dom_.assertNoApplicationError('the no-Schema Wiki view')

    const overview = await apiGet(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki`)
    const shownStatus = (dom_.requireTestId('wiki-status-badge').textContent ?? '').trim()
    if (shownStatus !== overview.base_wiki.status) {
      fail(
        `the Wiki UI shows status "${shownStatus}" but the API reports "${overview.base_wiki.status}"`,
      )
    }

    dom_.clickTestId('wiki-tab-agentic')
    await waitFor('the Agent Learned panel', () => dom_.byTestId('wiki-source-agentic'), 20000)
    await waitFor(
      'the Agent Learned content to resolve',
      () => dom_.byTestId('wiki-agentic-entry-list') || dom_.text().includes('No Agent Learned entries yet'),
      20000,
    )
    dom_.assertNoApplicationError('the Agent Learned panel')

    const searchOutcome = await submitWikiSearch(dom_, 'learning')
    if (searchOutcome === 'results') {
      const badges = Array.from(dom_.document.querySelectorAll('[data-testid="wiki-hit-source"]')).map(
        (element) => (element.textContent ?? '').trim(),
      )
      const unknown = badges.filter((label) => label !== 'Schema Wiki' && label !== 'Agent Learned')
      if (unknown.length > 0) {
        fail(`search hits were not tagged with a knowledge source: ${JSON.stringify(unknown)}`)
      }
    } else if (searchOutcome === 'error') {
      // The backend rejects Wiki search for a Workspace with no member papers;
      // the UI must surface that API state explicitly instead of failing blank.
      const errorText = (dom_.applicationError().textContent ?? '').trim()
      if (!errorText) {
        fail('the Wiki search failure did not surface an API-derived message')
      }
    }
    pass(
      `a no-Schema workspace explains Schema Wiki as unavailable without an application error; Wiki search produced an explicit "${searchOutcome}" outcome`,
    )
  })

  /* ----------------------------- B. Schema-bound Workspace + member paper */

  await withApp({ path: '/workspaces' }, async (window, dom_) => {
    const workspaceId = await createWorkspaceThroughUi(
      dom_,
      `Smoke wiki schema bound ${unique}`,
      true,
    )

    // Import a real PDF through the Library and add it to this Workspace so the
    // Schema Wiki capability path (bound Schema + member paper) is exercised.
    dom_.clickLink('/library')
    await waitFor('the Library section', () => dom_.byTestId('import-pdf-button'), 15000)
    dom_.clickTestId('import-pdf-button')
    await waitFor('the import dialog', () => dom_.byTestId('import-paper-dialog'))

    const fileBuffer = readFileSync(pdfPath)
    const jsdomFile = new window.File([new Uint8Array(fileBuffer)], path.basename(pdfPath), {
      type: 'application/pdf',
    })
    dom_.setFileInput(dom_.requireTestId('import-paper-file-input'), jsdomFile)
    dom_.clickTestId('import-paper-submit')

    const importResult = await waitFor(
      'the imported paper detail view or an import outcome',
      () => {
        if (window.location.pathname.startsWith('/library/') && dom_.byTestId('paper-status')) {
          return { kind: 'detail' }
        }
        const outcome = dom_.byTestId('import-paper-outcome')
        return outcome ? { kind: 'outcome', element: outcome } : false
      },
      180000,
    )
    if (importResult.kind === 'outcome') {
      const openExisting = importResult.element.querySelector('button')
      if (!openExisting) {
        fail(`the import did not produce a paper: ${(importResult.element.textContent ?? '').trim()}`)
      }
      dom_.click(openExisting)
      await waitFor(
        'the existing paper detail view',
        () => window.location.pathname.startsWith('/library/') && dom_.byTestId('paper-status'),
        30000,
      )
    }

    await waitFor('the membership control', () => dom_.byTestId('paper-workspace-membership'), 15000)
    if ((dom_.byTestId('paper-membership-state')?.textContent ?? '').includes('Not in this workspace')) {
      dom_.clickTestId('add-paper-to-workspace')
      await waitFor(
        'the membership state to report "In this workspace"',
        () => (dom_.byTestId('paper-membership-state')?.textContent ?? '').includes('In this workspace'),
        20000,
      )
    }

    dom_.clickLink('/wiki')
    await waitFor('the Wiki status panel', () => dom_.byTestId('wiki-status'), 20000)
    await waitFor('the Schema Wiki panel', () => dom_.byTestId('wiki-source-schema'), 20000)
    dom_.assertNoApplicationError('the Schema-bound Wiki view')

    const overview = await apiGet(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki`)
    const shownStatus = (dom_.requireTestId('wiki-status-badge').textContent ?? '').trim()
    if (shownStatus !== overview.base_wiki.status) {
      fail(
        `the Wiki UI shows status "${shownStatus}" but the API reports "${overview.base_wiki.status}"`,
      )
    }
    if (!dom_.byTestId('wiki-read-capability') || !dom_.byTestId('wiki-build-capability')) {
      fail('the Wiki view did not report the Schema Wiki read/build capability')
    }
    const schemaPanelText = (dom_.requireTestId('wiki-source-schema').textContent ?? '').trim()
    if (!schemaPanelText.includes('Schema Wiki')) {
      fail('the Schema-bound Wiki view did not label the Schema Wiki knowledge source')
    }
    if (overview.base_wiki_capability.build_supported && !dom_.byTestId('wiki-build-button')) {
      fail('the Schema Wiki build capability was reported but no build control was offered')
    }
    if (!dom_.text().includes('Agent Learned')) {
      fail('the Wiki view did not expose the Agent Learned knowledge source')
    }
    pass(
      `a Schema-bound workspace with a member paper reports the API Wiki status "${shownStatus}" and represents Schema Wiki content state`,
    )
  })

  /* ------------------------- C. Agent Learned entries and search results */

  if (AGENTIC_WORKSPACE) {
    await withApp({ path: '/wiki', activeWorkspaceId: AGENTIC_WORKSPACE }, async (window, dom_) => {
      await waitFor('the Wiki status panel', () => dom_.byTestId('wiki-status'), 20000)

      dom_.clickTestId('wiki-tab-agentic')
      const entryList = await waitFor(
        'the Agent Learned entry list',
        () => dom_.byTestId('wiki-agentic-entry-list'),
        20000,
      )
      const entryTitle = (entryList.querySelector('.card__title')?.textContent ?? '').trim()
      if (!entryTitle) {
        fail('the Agent Learned list did not show an entry title')
      }

      const searchOutcome = await submitWikiSearch(dom_, 'learning')
      if (searchOutcome !== 'results') {
        fail(`Wiki search did not return structured results (got "${searchOutcome}")`)
      }
      const hits = Array.from(dom_.document.querySelectorAll('[data-testid="wiki-search-hit"]'))
      const sourceLabels = hits.map((hit) =>
        (hit.querySelector('[data-testid="wiki-hit-source"]')?.textContent ?? '').trim(),
      )
      if (!sourceLabels.includes('Agent Learned')) {
        fail(`Wiki search hits were not tagged as Agent Learned content: ${JSON.stringify(sourceLabels)}`)
      }

      // When the API reports a degraded search, the panel must surface that
      // status and its backend code instead of presenting partial results as a
      // complete success.
      const apiSearch = await apiGet(
        `/api/v1/workspaces/${encodeURIComponent(AGENTIC_WORKSPACE)}/wiki/search?query=learning&include_stale=true`,
      )
      if (apiSearch.status !== 'ok') {
        if (!dom_.text().includes('Some knowledge sources could not be searched.')) {
          fail('the UI did not surface the degraded Wiki search status reported by the API')
        }
        if (apiSearch.error_code && !dom_.text().includes(apiSearch.error_code)) {
          fail('the UI did not surface the backend code for the degraded Wiki search')
        }
      }

      const detailLink = hits
        .map((hit) => hit.querySelector('a.wiki-hit__title'))
        .find((link) => link && (link.getAttribute('href') ?? '').startsWith('/wiki/entries/'))
      if (!detailLink) {
        fail('an Agent Learned search hit did not link to its detail view')
      }
      dom_.click(detailLink)
      await waitFor(
        'the Agent Learned entry detail view',
        () => dom_.byTestId('wiki-entry-detail'),
        20000,
      )
      const detailText = (dom_.requireTestId('wiki-entry-detail').textContent ?? '').trim()
      if (!detailText.includes(entryTitle)) {
        fail('the Agent Learned entry detail did not show the entry the search hit came from')
      }
      dom_.assertNoApplicationError('the Agent Learned search and detail flow')
      pass(
        `Agent Learned entries and Wiki search results render from the API and open their detail view (${hits.length} hit(s))`,
      )
    })
  }

  process.stdout.write('PASS: workspace Wiki UI smoke completed\n')
} catch (error) {
  exitCode = 1
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

process.exit(exitCode)
