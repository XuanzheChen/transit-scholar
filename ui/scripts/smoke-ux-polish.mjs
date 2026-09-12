/**
 * Deterministic DOM smoke for the UI-S5 UX polish (T-009).
 *
 * Renders the real production bundle (ui/dist) in jsdom against the same
 * `/api/v1/*` request surface the real FastAPI backend exposes, backed by a
 * scripted in-process stub, and exercises the polish added on top of the
 * completed feature set:
 *
 *   1. the shortcut reference opens from the shell button and from `?`, and
 *      closing it returns keyboard focus to the control that opened it;
 *   2. `g` + section letter navigates between product sections;
 *   3. `/` focuses the active view's main input;
 *   4. Ctrl+Enter submits the research prompt;
 *   5. arrow keys move between answer-citation references, and the citation
 *      dialog steps previous/next without closing;
 *   6. a citation offers the plain browser-PDF URL plus an optional `#page=`
 *      viewer hint, and the file-content URL stays the default open target.
 *
 * Nothing here changes backend semantics: every value rendered by the UI is
 * served by the scripted `/api/v1/*` responses.
 *
 * Usage: node scripts/smoke-ux-polish.mjs [--dist <dir>]
 */
import { copyFileSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { JSDOM } from 'jsdom'

const args = process.argv.slice(2)
const distIndex = args.indexOf('--dist')
const distDir = path.resolve(distIndex >= 0 ? args[distIndex + 1] : path.join(process.cwd(), 'dist'))

const ORIGIN = 'http://127.0.0.1:8000'
const WORKSPACE_ID = 'ws-ux'
const CONVERSATION_ID = 'conv-ux'
const RUN_ID = 'run-ux'
const PROMPT = 'What do the workspace papers report about the intervention?'
const FINAL_ANSWER = 'Both retrieved sources report that the intervention reduces delay.'
const SETTLE_TIMEOUT_MS = 20000

const CITATIONS = [
  {
    evidence_id: 'ev-1',
    research_session_id: 'session-1',
    paper_id: 'paper-ux-1',
    paper_title: 'Transit Signal Priority Field Trial',
    source_kind: 'paper',
    pages: [3],
    block_id: 'block-1',
    character_start: 100,
    character_end: 260,
    parse_run_id: 'parse-1',
    canonical_source_version: 'v1',
    evidence_quote: 'The intervention reduced delay by 18% in the trial.',
    file_id: 'file-ux-1',
    page: 3,
  },
  {
    evidence_id: 'ev-2',
    research_session_id: 'session-1',
    paper_id: 'paper-ux-2',
    paper_title: 'Scheduling Under Uncertainty',
    source_kind: 'paper',
    pages: [7],
    block_id: 'block-2',
    character_start: 400,
    character_end: 520,
    parse_run_id: 'parse-2',
    canonical_source_version: 'v1',
    evidence_quote: 'Delay gains persisted across the observed schedule horizon.',
    file_id: 'file-ux-2',
    page: 7,
  },
]

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

/* --------------------------------------------------------- scripted backend */

const backend = {
  submitted: false,
  turnSubmissions: 0,
}

function workspaceRecord() {
  return {
    workspace_id: WORKSPACE_ID,
    name: 'UX polish workspace',
    status: 'active',
    schema_mode: 'none',
    schema_binding: null,
    revision: 1,
    created_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
    updated_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
  }
}

function citationPayload(citation) {
  return {
    evidence_id: citation.evidence_id,
    research_session_id: citation.research_session_id,
    paper_id: citation.paper_id,
    paper_title: citation.paper_title,
    source_kind: citation.source_kind,
    pages: citation.pages,
    block_id: citation.block_id,
    character_start: citation.character_start,
    character_end: citation.character_end,
    parse_run_id: citation.parse_run_id,
    canonical_source_version: citation.canonical_source_version,
    evidence_quote: citation.evidence_quote,
  }
}

function conversationTurns() {
  if (!backend.submitted) {
    return []
  }
  return [
    {
      turn_id: 'turn-ux',
      conversation_id: CONVERSATION_ID,
      sequence: 1,
      user_message: PROMPT,
      resolved_user_goal: PROMPT,
      agent_run_id: RUN_ID,
      status: 'completed',
      assistant_response: {
        answer_text: FINAL_ANSWER,
        citation_references: CITATIONS.map((citation) => citation.evidence_id),
      },
      final_answer: FINAL_ANSWER,
      answer_citations: CITATIONS.map(citationPayload),
      error_message: null,
      created_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
      completed_at: new Date(Date.UTC(2024, 0, 1, 1)).toISOString(),
    },
  ]
}

function runState() {
  return {
    agent_run_id: RUN_ID,
    workspace_id: WORKSPACE_ID,
    status: 'completed',
    phase: 'completed',
    user_goal: PROMPT,
    pause_requested: false,
    display_status: 'completed',
  }
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    headers: { get: () => null },
    json: async () => body,
  }
}

function errorResponse(code, message, status) {
  return jsonResponse({ error: { code, message, details: {} } }, status)
}

function paperFiles(paperId) {
  const citation = CITATIONS.find((candidate) => candidate.paper_id === paperId)
  if (!citation) {
    return null
  }
  return [
    {
      file_id: citation.file_id,
      original_filename: `${paperId}.pdf`,
      mime_type: 'application/pdf',
      file_size_bytes: 204800,
      is_primary: true,
      page_count: 10,
    },
  ]
}

function fetchShim(input, init = {}) {
  const url = new URL(String(input), ORIGIN)
  const method = (init.method ?? 'GET').toUpperCase()
  const route = `${method} ${url.pathname}`

  if (route === 'GET /api/v1/health') {
    return jsonResponse({ status: 'healthy' })
  }
  if (route === 'GET /api/v1/capabilities') {
    return jsonResponse({
      pause_resume: true,
      user_schema_creation: true,
      base_wiki: true,
      agentic_wiki: false,
      semantic_wiki_search: false,
      pdf_upload_max_bytes: 104857600,
    })
  }
  if (route === 'GET /api/v1/workspaces') {
    return jsonResponse({ items: [workspaceRecord()] })
  }
  if (route === `GET /api/v1/workspaces/${WORKSPACE_ID}`) {
    return jsonResponse(workspaceRecord())
  }
  if (route === `GET /api/v1/workspaces/${WORKSPACE_ID}/papers`) {
    return jsonResponse({ items: [] })
  }
  if (route === `GET /api/v1/workspaces/${WORKSPACE_ID}/conversations`) {
    return jsonResponse({
      items: [
        {
          conversation_id: CONVERSATION_ID,
          workspace_id: WORKSPACE_ID,
          title: 'UX polish review',
          created_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
        },
      ],
    })
  }
  if (route === `GET /api/v1/conversations/${CONVERSATION_ID}`) {
    return jsonResponse({
      conversation_id: CONVERSATION_ID,
      workspace_id: WORKSPACE_ID,
      title: 'UX polish review',
      turns: conversationTurns(),
    })
  }
  if (route === `POST /api/v1/conversations/${CONVERSATION_ID}/turns`) {
    backend.submitted = true
    backend.turnSubmissions += 1
    return jsonResponse({ turn_id: 'turn-ux', agent_run_id: RUN_ID }, 202)
  }
  if (route === `GET /api/v1/runs/${RUN_ID}`) {
    return jsonResponse(runState())
  }
  if (route === `GET /api/v1/runs/${RUN_ID}/timeline`) {
    return jsonResponse({ events: [], next_sequence: 0 })
  }
  if (route === 'GET /api/v1/papers') {
    return jsonResponse({ items: [] })
  }
  if (route === 'GET /api/v1/schemas') {
    return jsonResponse([])
  }
  const filesMatch = /^\/api\/v1\/papers\/([^/]+)\/files$/.exec(url.pathname)
  if (method === 'GET' && filesMatch) {
    const files = paperFiles(decodeURIComponent(filesMatch[1]))
    if (files) {
      return jsonResponse(files)
    }
  }

  return errorResponse('NOT_FOUND', `unstubbed route ${route}`, 404)
}

/* ------------------------------------------------------------- jsdom setup */

const savedGlobals = new Map()

function applyGlobals(window, fetchImpl) {
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

async function waitFor(label, predicate, timeoutMs = SETTLE_TIMEOUT_MS) {
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
    await new Promise((resolve) => setTimeout(resolve, 40))
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

  function press(key, init = {}) {
    window.dispatchEvent(
      new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }),
    )
  }

  return { document, byTestId, text, requireTestId, click, clickTestId, setInputValue, press }
}

/* -------------------------------------------------------------------- run */

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
const dom = new JSDOM(html, { url: `${ORIGIN}/research`, pretendToBeVisual: true })
const { window } = dom
window.localStorage.setItem('transit-scholar.ui.active-workspace-id', WORKSPACE_ID)
applyGlobals(window, fetchShim)
const dom2 = makeDomHelpers(window)

const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-ux-smoke-'))
const bundleTarget = path.join(scratch, `bundle-${Date.now()}.mjs`)
copyFileSync(bundleFile, bundleTarget)

let exitCode = 0
try {
  await import(pathToFileURL(bundleTarget).href)

  await waitFor(
    'the Research view to load',
    () => dom2.byTestId('research-view') && dom2.text().includes('Conversations'),
  )
  if (dom2.text().includes('Backend unavailable')) {
    fail('the UI reported the backend as unavailable')
  }

  /* --------------------------------------------- 1. shortcut reference */

  const helpButton = dom2.requireTestId('shortcuts-help-button')
  helpButton.focus()
  dom2.click(helpButton)
  const helpDialog = await waitFor('the shortcut reference dialog', () =>
    dom2.byTestId('shortcuts-dialog'),
  )
  const helpText = helpDialog.textContent ?? ''
  for (const label of ['Workspaces', 'Research', 'Library', 'Wiki', 'Schemas']) {
    if (!helpText.includes(label)) {
      fail(`the shortcut reference does not document the ${label} section`)
    }
  }
  for (const key of ['/', 'Ctrl', 'Enter', '?', 'Esc']) {
    if (!helpText.includes(key)) {
      fail(`the shortcut reference does not document "${key}"`)
    }
  }
  window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }))
  await waitFor('the shortcut reference to close', () => !dom2.byTestId('shortcuts-dialog'))
  if (window.document.activeElement !== helpButton) {
    fail('closing the shortcut reference did not return focus to the help control')
  }
  pass('the shortcut reference opened, documented its keys, and restored focus on close')

  dom2.press('?', { shiftKey: true })
  await waitFor('the shortcut reference from "?"', () => dom2.byTestId('shortcuts-dialog'))
  window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }))
  await waitFor('the shortcut reference to close again', () => !dom2.byTestId('shortcuts-dialog'))
  pass('the "?" shortcut opened the shortcut reference')

  /* ------------------------------------------------- 2. section chords */

  dom2.press('g')
  dom2.press('w')
  await waitFor(
    'g then w to open Workspaces',
    () => window.location.pathname === '/workspaces' && dom2.byTestId('new-workspace-button'),
  )

  dom2.press('g')
  dom2.press('l')
  await waitFor(
    'g then l to open the Library',
    () => window.location.pathname === '/library' && dom2.byTestId('import-pdf-button'),
  )

  dom2.press('g')
  dom2.press('s')
  await waitFor(
    'g then s to open Schemas',
    () => window.location.pathname === '/schemas' && dom2.byTestId('new-schema-button'),
  )

  dom2.press('g')
  dom2.press('r')
  await waitFor(
    'g then r to return to Research',
    () => window.location.pathname === '/research' && dom2.byTestId('research-view'),
  )
  pass('g + section letter navigated through Workspaces, Library, Schemas, and Research')

  /* --------------------------------------------- 3. focus and submit */

  dom2.press('/')
  const promptInput = await waitFor(
    'the "/" shortcut to focus the research prompt',
    () => {
      const input = dom2.byTestId('prompt-input')
      return input && window.document.activeElement === input ? input : false
    },
  )
  pass('the "/" shortcut focused the active view main input')

  dom2.setInputValue(promptInput, PROMPT)
  promptInput.dispatchEvent(
    new window.KeyboardEvent('keydown', {
      key: 'Enter',
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    }),
  )
  await waitFor('Ctrl+Enter to submit the prompt', () => backend.turnSubmissions === 1)
  await waitFor(
    'the final answer after the submitted turn',
    () => {
      const answer = dom2.byTestId('turn-final-answer')
      return answer && (answer.textContent ?? '').includes(FINAL_ANSWER) ? answer : false
    },
  )
  pass('Ctrl+Enter submitted the prompt and rendered the API answer')

  /* --------------------------------------- 4. citation navigation */

  await waitFor('the answer citations', () => dom2.byTestId('answer-citations'))
  const firstChip = dom2.requireTestId('citation-reference-1')
  const secondChip = dom2.requireTestId('citation-reference-2')

  // List-level arrow navigation moves focus between citation references.
  firstChip.focus()
  firstChip.dispatchEvent(
    new window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }),
  )
  await waitFor('arrow keys to move between citation references', () => {
    return window.document.activeElement === secondChip
  })
  secondChip.dispatchEvent(
    new window.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true }),
  )
  await waitFor('arrow keys to move back between citation references', () => {
    return window.document.activeElement === firstChip
  })

  dom2.click(firstChip)
  const dialog = await waitFor('the citation detail dialog', () => dom2.byTestId('citation-detail-dialog'))
  if (!(dialog.textContent ?? '').includes('Citation 1 of 2')) {
    fail(`the citation dialog did not report its position: ${dialog.textContent}`)
  }
  const firstPdf = dom2.requireTestId('open-citation-pdf')
  const firstHref = firstPdf.getAttribute('href') ?? ''
  if (!firstHref.endsWith(`/api/v1/files/${CITATIONS[0].file_id}/content`)) {
    fail(`Open PDF did not use the plain file-content endpoint: ${firstHref}`)
  }
  if (firstPdf.getAttribute('target') !== '_blank') {
    fail('Open PDF does not open in the browser-native viewer')
  }
  const firstPagedPdf = await waitFor('the page-specific PDF hint', () =>
    dom2.byTestId('open-citation-pdf-page'),
  )
  if (!(firstPagedPdf.getAttribute('href') ?? '').endsWith(`/content#page=${CITATIONS[0].page}`)) {
    fail(`the page-specific PDF hint was wrong: ${firstPagedPdf.getAttribute('href')}`)
  }

  // The dialog steps between citations without closing.
  window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'ArrowRight' }))
  await waitFor(
    'the citation dialog to step to the second citation',
    () => (dom2.byTestId('citation-position')?.textContent ?? '').includes('Citation 2 of 2'),
  )
  const secondPagedPdf = await waitFor('the second page-specific PDF hint', () => {
    const hint = dom2.byTestId('open-citation-pdf-page')
    return hint && (hint.getAttribute('href') ?? '').endsWith(`/content#page=${CITATIONS[1].page}`)
      ? hint
      : false
  })
  if (!(secondPagedPdf.getAttribute('href') ?? '').includes('/api/v1/files/')) {
    fail('the page-specific PDF hint abandoned the file-content endpoint')
  }
  dom2.clickTestId('citation-previous')
  await waitFor(
    'the Previous control to step back to the first citation',
    () => (dom2.byTestId('citation-position')?.textContent ?? '').includes('Citation 1 of 2'),
  )
  dom2.clickTestId('citation-next')
  await waitFor(
    'the Next control to step forward to the second citation',
    () => (dom2.byTestId('citation-position')?.textContent ?? '').includes('Citation 2 of 2'),
  )
  dom2.clickTestId('citation-previous')
  await waitFor(
    'the Previous control to return to the first citation',
    () => (dom2.byTestId('citation-position')?.textContent ?? '').includes('Citation 1 of 2'),
  )
  pass('citations navigated by arrow keys, previous/next controls, and a page-specific PDF hint')

  // Closing the dialog returns focus to the reference that opened it.
  window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }))
  await waitFor('the citation dialog to close', () => !dom2.byTestId('citation-detail-dialog'))
  await waitFor('focus to return to the citation reference', () => {
    return window.document.activeElement === firstChip
  })
  if (window.document.body.classList.contains('modal-open')) {
    fail('closing the dialog did not release the page scroll lock')
  }
  pass('closing the citation dialog restored focus and released the scroll lock')

  restoreGlobals()
  window.close()
  process.stdout.write('PASS: UX polish smoke completed\n')
} catch (error) {
  exitCode = 1
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

process.exit(exitCode)
