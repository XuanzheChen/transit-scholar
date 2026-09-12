/**
 * Deterministic DOM smoke for the core Research experience (T-003).
 *
 * Renders the real production bundle (ui/dist) in jsdom with the same
 * `/api/v1/*` request surface the real FastAPI backend exposes, but backed by a
 * scripted in-process stub. That makes the timing-sensitive UI contract
 * reproducible:
 *
 *   1. A Conversation is created through the UI and a prompt is submitted.
 *   2. Submission returns immediately: the Run panel appears and Run status is
 *      still active, long before the run completes.
 *   3. Run status and Timeline events update while the agent is active, and
 *      Timeline reads use the API sequence cursor instead of re-fetching.
 *   4. Pause shows a visible "Pausing" state; once the API reports paused the
 *      same control offers Resume, and Resume targets the same AgentRun.
 *   5. Completion renders the final answer, collapses the Timeline by default,
 *      and keeps it available on demand.
 *   6. A citation can be inspected and its local PDF opened.
 *
 * Usage: node scripts/smoke-research-flow.mjs [--dist <dir>]
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
const WORKSPACE_ID = 'ws-smoke'
const CONVERSATION_ID = 'conv-smoke'
const RUN_ID = 'run-smoke'
const PAPER_ID = 'paper-smoke'
const FILE_ID = 'file-smoke'
const FINAL_ANSWER = 'The intervention reduces delay across both retrieved sources.'
const SETTLE_TIMEOUT_MS = 20000

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

const SCRIPTED_EVENTS = [
  { kind: 'planning', data: { research_question: 'Does the intervention reduce delay?' } },
  { kind: 'query', data: { query_id: 'q1', query_text: 'intervention delay evidence' } },
  { kind: 'retrieval', data: { action_type: 'retrieve_query', status: 'completed' } },
  {
    kind: 'evidence',
    data: {
      evidence_id: 'ev1',
      paper_id: PAPER_ID,
      pages: [3],
      preview: 'The intervention reduced delay by 18% in the trial.',
    },
  },
  { kind: 'synthesis', data: { role_status: 'completed' } },
]

const FINAL_CITATIONS = [
  {
    evidence_id: 'ev1',
    research_session_id: 'session-1',
    paper_id: PAPER_ID,
    paper_title: 'Causal Reinforcement Learning for Train Scheduling',
    source_kind: 'paper',
    pages: [3],
    block_id: 'block-7',
    character_start: 120,
    character_end: 310,
    parse_run_id: 'parse-1',
    canonical_source_version: 'v1',
    evidence_quote: 'The intervention reduced delay by 18% in the trial.',
  },
]

const backend = {
  runStatus: 'running',
  phase: 'query_planning',
  pauseRequested: false,
  revealed: 0,
  prompt: '',
  turnSubmitted: false,
  turnStatus: 'running',
  finalAnswer: null,
  citations: [],
  timelineRequests: [],
  pauseCalls: [],
  resumeCalls: [],
  turnSubmissions: 0,
  conversationTitle: null,
  deferredAnswerReads: 0,
  deferNextRunRead: false,
  deferredRunReadPending: false,
  releaseDeferredRunRead: null,
}

function runState() {
  return {
    agent_run_id: RUN_ID,
    workspace_id: WORKSPACE_ID,
    status: backend.runStatus,
    phase: backend.phase,
    user_goal: 'Does the intervention reduce delay?',
    pause_requested: backend.pauseRequested,
    display_status: backend.pauseRequested ? 'pause_requested' : backend.runStatus,
  }
}

function timelineSlice(afterSequence) {
  return SCRIPTED_EVENTS.slice(0, backend.revealed)
    .map((event, index) => ({
      sequence: index + 1,
      kind: event.kind,
      timestamp: new Date(Date.UTC(2024, 0, 1, 0, 0, index)).toISOString(),
      research_session_id: 'session-1',
      data: event.data,
    }))
    .filter((event) => event.sequence > afterSequence)
}

function conversationTurns() {
  if (!backend.turnSubmitted) {
    return []
  }
  return [
    {
      turn_id: 'turn-1',
      conversation_id: CONVERSATION_ID,
      sequence: 1,
      user_message: backend.prompt,
      resolved_user_goal: backend.prompt,
      agent_run_id: RUN_ID,
      status: backend.turnStatus,
      assistant_response:
        backend.finalAnswer === null ? null : { answer_text: backend.finalAnswer, citation_references: ['ev1'] },
      final_answer: backend.finalAnswer,
      answer_citations: backend.citations,
      error_message: null,
      created_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
      completed_at: null,
    },
  ]
}

function revealNextEvent() {
  if (backend.revealed < SCRIPTED_EVENTS.length) {
    backend.revealed += 1
  }
}

/** Drive the stub forward from the test, simulating the real runtime. */
function advanceToPaused() {
  backend.pauseRequested = false
  backend.runStatus = 'paused'
  backend.phase = 'paused'
}

/**
 * Complete the AgentRun.
 *
 * ``deferAnswerReads`` reproduces the real backend ordering: the run publishes
 * its terminal status before the durable Conversation turn carries the final
 * answer (post-run workspace promotion runs in between). The turn therefore
 * keeps reporting ``running`` with no answer for that many conversation reads,
 * and only then serves the answer — with no further user action.
 */
function completeRun({ deferAnswerReads = 0 } = {}) {
  backend.revealed = SCRIPTED_EVENTS.length
  backend.runStatus = 'completed'
  backend.phase = 'completed'
  if (deferAnswerReads > 0) {
    backend.turnStatus = 'running'
    backend.finalAnswer = null
    backend.citations = []
    backend.deferredAnswerReads = deferAnswerReads
    return
  }
  persistTurnAnswer()
}

function persistTurnAnswer() {
  backend.turnStatus = 'completed'
  backend.finalAnswer = FINAL_ANSWER
  backend.citations = FINAL_CITATIONS
}

/* --------------------------------------------------------------- transport */

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
  if (route === `GET /api/v1/workspaces/${WORKSPACE_ID}`) {
    return jsonResponse({
      workspace_id: WORKSPACE_ID,
      name: 'Smoke research workspace',
      status: 'active',
      schema_mode: 'none',
      schema_binding: null,
      revision: 1,
      created_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
      updated_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
    })
  }
  if (route === `GET /api/v1/workspaces/${WORKSPACE_ID}/conversations`) {
    return jsonResponse({
      items: backend.conversationTitle
        ? [
            {
              conversation_id: CONVERSATION_ID,
              workspace_id: WORKSPACE_ID,
              title: backend.conversationTitle,
              created_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
            },
          ]
        : [],
    })
  }
  if (route === `POST /api/v1/workspaces/${WORKSPACE_ID}/conversations`) {
    const payload = init.body ? JSON.parse(String(init.body)) : {}
    backend.conversationTitle = payload.title ?? null
    return jsonResponse(
      {
        conversation_id: CONVERSATION_ID,
        workspace_id: WORKSPACE_ID,
        title: backend.conversationTitle,
        created_at: new Date(Date.UTC(2024, 0, 1)).toISOString(),
      },
      201,
    )
  }
  if (route === `GET /api/v1/conversations/${CONVERSATION_ID}`) {
    if (backend.deferredAnswerReads > 0) {
      backend.deferredAnswerReads -= 1
      if (backend.deferredAnswerReads === 0) {
        persistTurnAnswer()
      }
    }
    return jsonResponse({
      conversation_id: CONVERSATION_ID,
      workspace_id: WORKSPACE_ID,
      title: backend.conversationTitle,
      turns: conversationTurns(),
    })
  }
  if (route === `POST /api/v1/conversations/${CONVERSATION_ID}/turns`) {
    const payload = init.body ? JSON.parse(String(init.body)) : {}
    backend.prompt = payload.message ?? ''
    backend.turnSubmitted = true
    backend.turnStatus = 'running'
    backend.turnSubmissions += 1
    return jsonResponse({ turn_id: 'turn-1', agent_run_id: RUN_ID }, 202)
  }
  if (route === `GET /api/v1/runs/${RUN_ID}`) {
    revealNextEvent()
    const response = jsonResponse(runState())
    if (backend.deferNextRunRead) {
      backend.deferNextRunRead = false
      backend.deferredRunReadPending = true
      return new Promise((resolve) => {
        backend.releaseDeferredRunRead = () => {
          backend.deferredRunReadPending = false
          backend.releaseDeferredRunRead = null
          resolve(response)
        }
      })
    }
    return response
  }
  if (route === `GET /api/v1/runs/${RUN_ID}/timeline`) {
    const after = Number(url.searchParams.get('after_sequence') ?? '0')
    backend.timelineRequests.push(after)
    const events = timelineSlice(after)
    return jsonResponse({
      events,
      next_sequence: events.length > 0 ? events[events.length - 1].sequence : after,
    })
  }
  if (route === `POST /api/v1/runs/${RUN_ID}/pause`) {
    backend.pauseCalls.push(RUN_ID)
    backend.pauseRequested = true
    return jsonResponse(runState())
  }
  if (route === `POST /api/v1/runs/${RUN_ID}/resume`) {
    backend.resumeCalls.push(RUN_ID)
    backend.runStatus = 'running'
    backend.phase = 'query_planning'
    backend.pauseRequested = false
    return jsonResponse(runState(), 202)
  }
  if (route === `GET /api/v1/papers/${PAPER_ID}/files`) {
    return jsonResponse([
      {
        file_id: FILE_ID,
        original_filename: 'train_scheduling.pdf',
        mime_type: 'application/pdf',
        file_size_bytes: 204800,
        is_primary: true,
        page_count: 8,
      },
    ])
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
  const countTimelineEvents = () => document.querySelectorAll('[data-testid^="timeline-event-"]').length
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

  function fire(event, type) {
    event.dispatchEvent(new window.Event(type, { bubbles: true, cancelable: true }))
  }

  return { document, byTestId, countTimelineEvents, text, requireTestId, click, clickTestId, setInputValue, fire }
}

/* -------------------------------------------------------------------- run */

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
const dom = new JSDOM(html, { url: `${ORIGIN}/research`, pretendToBeVisual: true })
const { window } = dom
window.localStorage.setItem('transit-scholar.ui.active-workspace-id', WORKSPACE_ID)
applyGlobals(window, fetchShim)
const dom2 = makeDomHelpers(window)

const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-research-smoke-'))
const bundleTarget = path.join(scratch, `bundle-${Date.now()}.mjs`)
copyFileSync(bundleFile, bundleTarget)

let exitCode = 0
try {
  await import(pathToFileURL(bundleTarget).href)

  // The navigation summary also contains the word "Conversations", so the
  // loaded state must be keyed on the rendered Conversation panel itself:
  // otherwise this wait can pass before the Conversation list API resolves and
  // the follow-up interaction races the first render.
  await waitFor(
    'the Research view to load',
    () => dom2.byTestId('research-view') && dom2.byTestId('conversation-list-panel') && dom2.byTestId('new-conversation-button'),
    15000,
  )
  if (dom2.text().includes('Backend unavailable')) {
    fail('the UI reported the backend as unavailable')
  }
  dom2.requireTestId('research-view')
  pass('Research view rendered inside the open workspace')

  /* ------------------------------------------------ 1. create conversation */

  dom2.clickTestId('new-conversation-button')
  await waitFor('the new-conversation dialog', () => dom2.byTestId('new-conversation-dialog'))
  dom2.setInputValue(dom2.requireTestId('conversation-title-input'), 'Delay intervention review')
  dom2.clickTestId('create-conversation-submit')

  await waitFor(
    'the created conversation to become active',
    () => dom2.byTestId('active-conversation') && dom2.byTestId(`conversation-item-${CONVERSATION_ID}`),
  )
  if (backend.conversationTitle !== 'Delay intervention review') {
    fail('the Conversation was not created through the Conversation API')
  }
  pass('created a Conversation through the UI')

  /* ---------------------------------------------- 2. submit a prompt quickly */

  dom2.setInputValue(dom2.requireTestId('prompt-input'), 'Does the intervention reduce delay?')
  dom2.clickTestId('research-primary-control')

  const activeRun = await waitFor(
    'the Run panel to appear while the run is still active',
    () => {
      const control = dom2.byTestId('research-primary-control')
      const status = dom2.byTestId('run-status')
      return control && status && control.getAttribute('data-control') === 'pause' ? status : false
    },
  )
  if (backend.turnSubmissions !== 1) {
    fail(`expected exactly one turn submission, saw ${backend.turnSubmissions}`)
  }
  if (!['running', 'created'].includes(backend.runStatus)) {
    fail(`the run had already finished when the prompt returned (${backend.runStatus})`)
  }
  if (dom2.requireTestId('prompt-input').disabled !== true) {
    fail('the prompt input was not locked while the submitted run is active')
  }
  if (!(activeRun.textContent ?? '').includes('Running')) {
    fail(`the Run status was not shown as active: ${activeRun.textContent}`)
  }
  if ((dom2.requireTestId('prompt-input').value ?? '') !== '') {
    fail('the prompt was not cleared after a successful submission')
  }
  pass('prompt submission returned to the UI without waiting for the AgentRun to complete')

  /* ------------------------------------------------- 3. live status/timeline */

  await waitFor(
    'Timeline events to accumulate while the run is active',
    () => dom2.countTimelineEvents() >= 2,
    15000,
  )
  const timeline = dom2.requireTestId('run-timeline')
  if (timeline.open !== true) {
    fail('the research Timeline was not expanded while the run is active')
  }
  const distinctCursors = new Set(backend.timelineRequests)
  if (distinctCursors.size < 2 || !backend.timelineRequests.some((value) => value > 0)) {
    fail(`Timeline reads did not use the API sequence cursor: ${backend.timelineRequests.join(',')}`)
  }
  const ordered = backend.timelineRequests.every((value, index, all) => index === 0 || value >= all[index - 1])
  if (!ordered) {
    fail(`Timeline sequence cursor went backwards: ${backend.timelineRequests.join(',')}`)
  }
  pass(`Run status and Timeline updated while active (cursors: ${backend.timelineRequests.join(', ')})`)

  /* ------------------------------------------------------- 4. pause/resume */

  // Hold one polling response while it still reports `running`. The newer
  // pause response must win even when this older response arrives afterward.
  backend.deferNextRunRead = true
  await waitFor(
    'an in-flight stale Run poll',
    () => backend.deferredRunReadPending,
  )
  dom2.clickTestId('research-primary-control')
  await waitFor(
    'the primary control to show the Pausing state',
    () => dom2.requireTestId('research-primary-control').getAttribute('data-control') === 'pausing',
  )
  const pausing = dom2.requireTestId('research-primary-control')
  if (!(pausing.textContent ?? '').includes('Pausing')) {
    fail(`the pausing control did not read "Pausing": ${pausing.textContent}`)
  }
  if (pausing.disabled !== true) {
    fail('the pausing control was not disabled while the pause request is pending')
  }
  if (backend.pauseCalls.length !== 1 || backend.pauseCalls[0] !== RUN_ID) {
    fail(`pause did not target the active run: ${JSON.stringify(backend.pauseCalls)}`)
  }
  backend.releaseDeferredRunRead?.()
  await new Promise((resolve) => setTimeout(resolve, 80))
  if (dom2.requireTestId('research-primary-control').getAttribute('data-control') !== 'pausing') {
    fail('an older Run poll overwrote the newer pause response')
  }
  pass('an out-of-order stale Run poll could not overwrite the newer API state')
  pass('pause entered a visible pausing state for the active run')

  // The cooperative pause reaches a safe point: the API now reports paused.
  advanceToPaused()

  await waitFor(
    'the primary control to offer Resume after the API reported paused',
    () => dom2.requireTestId('research-primary-control').getAttribute('data-control') === 'resume',
  )

  dom2.clickTestId('research-primary-control')
  await waitFor('the resume API call', () => backend.resumeCalls.length === 1)
  if (backend.resumeCalls[0] !== RUN_ID) {
    fail(`resume targeted ${backend.resumeCalls[0]} instead of ${RUN_ID}`)
  }
  if (backend.turnSubmissions !== 1) {
    fail('resume created a replacement run by submitting another turn')
  }
  pass('Resume continued the same paused AgentRun')

  /* ------------------------------------------- 5. completion and citations */

  // The run reaches its terminal status while the durable turn still has no
  // answer (the real ordering). No further user action is taken: the view must
  // keep refreshing the persisted turn until the answer lands.
  completeRun({ deferAnswerReads: 3 })
  const answer = await waitFor(
    'the final answer to be displayed after completion',
    () => {
      const element = dom2.byTestId('turn-final-answer')
      return element && (element.textContent ?? '').includes(FINAL_ANSWER) ? element : false
    },
    20000,
  )
  if (!(answer.textContent ?? '').includes(FINAL_ANSWER)) {
    fail('the final answer text was not rendered from the API turn')
  }

  await waitFor(
    'the active run panel to be replaced by the persisted completed turn',
    () => !dom2.byTestId('run-timeline') && dom2.byTestId(`turn-timeline-${RUN_ID}`),
  )
  const completedTimeline = dom2.requireTestId(`turn-timeline-${RUN_ID}`)
  if (completedTimeline.open !== false) {
    fail('the completed Timeline was not collapsed by default')
  }
  pass(
    'a terminal AgentRun whose durable answer lands later still displayed the final answer',
  )
  pass('completion displayed the final answer and collapsed the Timeline by default')

  // The collapsed Timeline stays available: opening it fetches and renders it.
  completedTimeline.open = true
  dom2.fire(completedTimeline, 'toggle')
  await waitFor(
    'the completed Timeline to render its events on demand',
    () => dom2.countTimelineEvents() >= SCRIPTED_EVENTS.length,
  )
  pass('the completed research Timeline remained available on demand')

  /* ------------------------------------------------------- 6. citation PDF */

  const citations = dom2.requireTestId('answer-citations')
  if (!(citations.textContent ?? '').includes('Causal Reinforcement Learning')) {
    fail('the answer citation did not expose the paper title from the API')
  }
  dom2.clickTestId('citation-reference-1')

  const dialog = await waitFor('the citation detail dialog', () => dom2.byTestId('citation-detail-dialog'))
  const dialogText = dialog.textContent ?? ''
  for (const expected of [
    'Causal Reinforcement Learning for Train Scheduling',
    PAPER_ID,
    'p. 3',
    'The intervention reduced delay by 18% in the trial.',
    'ev1',
    'paper',
  ]) {
    if (!dialogText.includes(expected)) {
      fail(`the citation dialog did not expose "${expected}": ${dialogText}`)
    }
  }

  const pdfLink = dom2.requireTestId('open-citation-pdf')
  const href = pdfLink.getAttribute('href') ?? ''
  if (!href.endsWith(`/api/v1/files/${FILE_ID}/content`)) {
    fail(`Open PDF did not use the file-content endpoint: ${href}`)
  }
  if (pdfLink.getAttribute('target') !== '_blank') {
    fail('Open PDF does not open in the browser-native viewer')
  }
  pass('citation detail exposed provenance and opened the local PDF')

  restoreGlobals()
  window.close()
  process.stdout.write('PASS: research flow smoke completed\n')
} catch (error) {
  exitCode = 1
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

process.exit(exitCode)
