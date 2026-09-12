/**
 * Live end-to-end DOM smoke for the core Research experience (T-003).
 *
 * Renders the real production bundle (ui/dist) inside jsdom and drives the same
 * interactions a browser would against a REAL running local API and Agent
 * runtime: create a workspace, create a Conversation, submit a prompt, observe
 * Run status and the research Timeline while the agent works, request pause and
 * resume, and finally inspect the answer citations and their local PDF.
 *
 * The harness only reads the API afterwards to confirm what the UI actually
 * did; it never mutates backend state behind the UI's back.
 *
 * Usage:
 *   node scripts/smoke-research-live.mjs [--base http://127.0.0.1:8017]
 *                                        [--dist <dir>]
 *                                        [--prompt "..."]
 *
 * Requires: `npm run build` and a running local API with an available agent
 * runtime (`uvicorn ...`).
 */
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { JSDOM } from 'jsdom'

const args = process.argv.slice(2)
function argValue(flag, fallback) {
  const index = args.indexOf(flag)
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback
}

const distDir = path.resolve(argValue('--dist', path.join(process.cwd(), 'dist')))
const baseUrl = (argValue('--base', process.env.TRANSIT_SCHOLAR_SMOKE_API_BASE ?? 'http://127.0.0.1:8017')).replace(/\/+$/, '')
const prompt = argValue('--prompt', 'What are the main findings reported in the papers in this workspace?')

const nativeFetch = globalThis.fetch.bind(globalThis)

function fail(message) {
  process.stderr.write(`FAIL: ${message}\n`)
  process.exit(1)
}

function pass(message) {
  process.stdout.write(`PASS: ${message}\n`)
}

function skip(message) {
  process.stdout.write(`SKIP: ${message}\n`)
}

function bundlePath() {
  const assetsDir = path.join(distDir, 'assets')
  const candidates = readdirSync(assetsDir).filter((name) => name.endsWith('.js'))
  if (candidates.length === 0) {
    fail(`no JavaScript bundle found in ${assetsDir}; run "npm run build" first`)
  }
  return path.join(assetsDir, candidates[0])
}

/** Direct API reads used ONLY to confirm what the UI did. */
async function apiGet(pathname) {
  const response = await nativeFetch(`${baseUrl}${pathname}`)
  if (!response.ok) {
    fail(`GET ${pathname} -> HTTP ${response.status}`)
  }
  return response.json()
}

/* ------------------------------------------------------------ DOM rendered */

const savedGlobals = new Map()

function applyGlobals(window) {
  const values = {
    window,
    document: window.document,
    localStorage: window.localStorage,
    location: window.location,
    history: window.history,
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
    fetch: (input, init) => nativeFetch(new URL(String(input), baseUrl), init),
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

async function waitFor(label, predicate, timeoutMs = 120000) {
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
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
}

function makeDomHelpers(window) {
  const document = window.document
  const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`)
  const countTimelineEvents = () => document.querySelectorAll('[data-testid^="timeline-event-"]').length
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

  return { byTestId, countTimelineEvents, text, requireTestId, findLink, click, clickTestId, setInputValue }
}

/* ------------------------------------------------------------------- setup */

const unique = Date.now().toString(36)
const workspaceName = `Smoke research ${unique}`
const conversationTitle = `Smoke conversation ${unique}`

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
const dom = new JSDOM(html, { url: `${baseUrl}/workspaces`, pretendToBeVisual: true })
const { window } = dom
applyGlobals(window)
const domHelpers = makeDomHelpers(window)

const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-research-live-'))
const bundleTarget = path.join(scratch, `bundle-${unique}.mjs`)
copyFileSync(bundleFile, bundleTarget)

let runId = null
let exitCode = 0
try {
  await import(pathToFileURL(bundleTarget).href)

  await waitFor('the Workspaces section to load', () => domHelpers.text().includes('New workspace'), 20000)
  if (domHelpers.text().includes('Backend unavailable')) {
    fail(`the UI cannot reach the API at ${baseUrl}`)
  }

  /* ------------------------------------------------------- 1. workspace */

  domHelpers.clickTestId('new-workspace-button')
  await waitFor('the new-workspace dialog', () => domHelpers.byTestId('create-workspace-dialog'))
  domHelpers.setInputValue(domHelpers.requireTestId('workspace-name-input'), workspaceName)
  domHelpers.clickTestId('schema-mode-none')
  domHelpers.clickTestId('create-workspace-submit')

  await waitFor(
    'the new workspace settings view',
    () => window.location.pathname.startsWith('/workspaces/') && domHelpers.byTestId('workspace-schema-settings'),
    30000,
  )
  pass('created a workspace through the UI')

  /* ---------------------------------------------------- 2. conversation */

  domHelpers.click(domHelpers.findLink('/research'))
  await waitFor('the Research view', () => domHelpers.byTestId('research-view'), 20000)
  domHelpers.clickTestId('new-conversation-button')
  await waitFor('the new-conversation dialog', () => domHelpers.byTestId('new-conversation-dialog'))
  domHelpers.setInputValue(domHelpers.requireTestId('conversation-title-input'), conversationTitle)
  domHelpers.clickTestId('create-conversation-submit')

  await waitFor(
    'the created conversation to become active',
    () => domHelpers.byTestId('active-conversation') && domHelpers.text().includes(conversationTitle),
    30000,
  )
  const conversationId = (domHelpers.byTestId('conversation-list')?.querySelector('[data-testid^="conversation-item-"]'))
    ?.getAttribute('data-testid')
    ?.replace('conversation-item-', '')
  if (!conversationId) {
    fail('the created conversation was not listed')
  }
  pass(`created a Conversation through the UI (${conversationId})`)

  /* ------------------------------------------- 3. submit prompt and monitor */

  domHelpers.setInputValue(domHelpers.requireTestId('prompt-input'), prompt)
  domHelpers.clickTestId('research-primary-control')

  await waitFor(
    'the run panel to appear while the run is active',
    () => domHelpers.byTestId('active-run') && domHelpers.byTestId('run-status'),
    30000,
  )
  const conversation = await apiGet(`/api/v1/conversations/${encodeURIComponent(conversationId)}`)
  const activeTurn = conversation.turns[conversation.turns.length - 1]
  runId = activeTurn?.agent_run_id ?? null
  if (!runId) {
    fail('the submitted turn has no AgentRun identifier')
  }
  const initialState = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
  if (initialState.status === 'completed' || initialState.status === 'failed') {
    skip('the run finished before the prompt returned; submission did not block on completion')
  } else {
    pass(`prompt submission returned while the run is still ${initialState.status}`)
  }

  await waitFor('the first Timeline events', () => domHelpers.countTimelineEvents() >= 1, 120000)
  pass('the research Timeline is displayed while the run is active')

  const timeline = domHelpers.byTestId('run-timeline')
  if (timeline && timeline.open !== true) {
    fail('the research Timeline was not expanded while the run is active')
  }

  /* ------------------------------------------------------- 4. pause/resume */

  const pauseControl = domHelpers.byTestId('research-primary-control')
  if (pauseControl && pauseControl.getAttribute('data-control') === 'pause' && !pauseControl.disabled) {
    domHelpers.clickTestId('research-primary-control')
    const pausingShown = await waitFor(
      'the primary control to leave the Pause state',
      () => {
        const control = domHelpers.byTestId('research-primary-control')
        return control && control.getAttribute('data-control') !== 'pause' ? control : false
      },
      30000,
    )
    const kind = pausingShown.getAttribute('data-control')
    if (kind === 'pausing') {
      pass('pause entered a visible Pausing state')
      await waitFor(
        'the primary control to offer Resume after the API reported paused',
        () => {
          const control = domHelpers.byTestId('research-primary-control')
          return control && control.getAttribute('data-control') === 'resume'
        },
        180000,
      )
      domHelpers.clickTestId('research-primary-control')
      pass('requested resume of the same paused run')
    } else if (kind === 'resume') {
      pass('the API reported the run paused before the pausing state could be observed')
      domHelpers.clickTestId('research-primary-control')
      pass('requested resume of the same paused run')
    }
    const afterResume = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
    if (afterResume.agent_run_id !== runId) {
      fail('resume replaced the AgentRun identifier')
    }
  } else {
    skip('the run was not in a pausable state; pause/resume was not exercised by this run')
  }

  /* ------------------------------------------- 5. completion and citations */

  await waitFor(
    'the final answer to be displayed',
    () => {
      const answer = domHelpers.byTestId('turn-final-answer')
      return answer && (answer.textContent ?? '').trim().length > 0 ? answer : false
    },
    600000,
  )
  const finalRun = await apiGet(`/api/v1/runs/${encodeURIComponent(runId)}`)
  if (finalRun.status !== 'completed') {
    fail(`expected a completed run, the API reports ${finalRun.status}`)
  }
  const completedTimeline = domHelpers.byTestId(`turn-timeline-${runId}`)
  if (!completedTimeline) {
    fail('the completed turn does not expose its research Timeline')
  }
  if (completedTimeline.open !== false) {
    fail('the completed research Timeline was not collapsed by default')
  }
  pass('completion displayed the final answer and collapsed the Timeline by default')

  /* ------------------------------------------------------- 6. citation PDF */

  if (domHelpers.byTestId('answer-citations')) {
    domHelpers.clickTestId('citation-reference-1')
    const dialog = await waitFor('the citation detail dialog', () => domHelpers.byTestId('citation-detail-dialog'), 30000)
    if (!(dialog.textContent ?? '').includes('Evidence identity')) {
      fail('the citation dialog did not expose the evidence provenance')
    }
    const pdfLink = await waitFor('the Open PDF action', () => domHelpers.byTestId('open-citation-pdf'), 30000)
    const href = pdfLink.getAttribute('href') ?? ''
    if (!href.includes('/api/v1/files/')) {
      fail(`Open PDF did not use the file-content endpoint: ${href}`)
    }
    const pdfResponse = await nativeFetch(new URL(href, baseUrl))
    if (!pdfResponse.ok) {
      fail(`the cited PDF could not be fetched: HTTP ${pdfResponse.status}`)
    }
    pass('inspected an answer citation and opened its local PDF')
  } else {
    skip('the completed answer reported no citations; citation inspection was not exercised')
  }

  restoreGlobals()
  window.close()
  process.stdout.write('PASS: live research smoke completed\n')
} catch (error) {
  exitCode = 1
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

process.exit(exitCode)
