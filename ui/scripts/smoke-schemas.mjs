/**
 * Offline DOM smoke for the Schema catalog and Schema Builder UI (T-008).
 *
 * Renders the real production bundle (ui/dist) inside jsdom and drives the same
 * user interactions a browser would against a deterministic in-memory stand-in
 * for the frozen `/api/v1/*` API. It verifies the Schema behaviors required for
 * this iteration:
 *
 *   1. the catalog lists the Schema definitions and versions the API returns,
 *      and one version can be opened read-only with no edit-in-place action
 *      (C-008);
 *   2. an invalid draft is rejected by the validate API, the issues are shown
 *      beside the matching draft content, and creation stays unavailable
 *      (AC-013);
 *   3. a valid draft can only be created after the explicit immutable-version
 *      confirmation, and the created request body matches the structured draft;
 *   4. an API conflict on creation is shown explicitly instead of being turned
 *      into a local success (AC-014);
 *   5. Workspace creation can then select the newly created Schema version
 *      through the existing Workspace API;
 *   6. when the capabilities API reports Schema creation as unavailable, the
 *      UI says so instead of offering an action it cannot complete (AC-014).
 *
 * The harness never calls the Product/Core Python layers and never talks to a
 * backend: every assertion is made on the rendered DOM and on the HTTP requests
 * the bundled UI itself issued to `/api/v1/*`.
 *
 * Usage:
 *   npm run build
 *   node scripts/smoke-schemas.mjs [--dist <dir>]
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
const TS = '2024-06-01T09:00:00'

const FIELD_TYPES = ['string', 'number', 'boolean', 'enum', 'list', 'object']
const IDENTITY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

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

function field(overrides) {
  return {
    id: 'research_question',
    label: 'Research question',
    question: 'What research question does the paper address?',
    description: '',
    type: 'string',
    constraints: {},
    evidence_required: true,
    allow_inference: true,
    ...overrides,
  }
}

function definitionFor(schemaId, version, name, description, sections) {
  return {
    schema_id: schemaId,
    version,
    name,
    description,
    status_semantics: null,
    sections,
  }
}

function describeDraft(draft) {
  return {
    schema_id: draft.schema_id,
    version: draft.version,
    name: draft.name ?? null,
    description: draft.description ?? null,
    schema_hash: `hash-${draft.schema_id}-${draft.version}`.replace(/[^A-Za-z0-9._-]/g, ''),
    definition: {
      ...draft,
      name: draft.name ?? null,
      description: draft.description ?? null,
      status_semantics: null,
    },
  }
}

/**
 * Faithful-enough stand-in for the frozen Schema validation rules
 * (`SchemaDefinition` pydantic model): the UI must react to the API response,
 * so the stand-in returns the same issue shapes and locations.
 */
function validateDraft(draft) {
  const issues = []
  const record = draft && typeof draft === 'object' ? draft : {}
  if (!IDENTITY_PATTERN.test(record.schema_id ?? '')) {
    issues.push({
      type: 'string_pattern_mismatch',
      loc: ['schema_id'],
      message: "String should match pattern '^[A-Za-z0-9][A-Za-z0-9._-]*$'",
    })
  }
  if (!IDENTITY_PATTERN.test(record.version ?? '')) {
    issues.push({
      type: 'string_pattern_mismatch',
      loc: ['version'],
      message: "String should match pattern '^[A-Za-z0-9][A-Za-z0-9._-]*$'",
    })
  }
  const sections = Array.isArray(record.sections) ? record.sections : []
  if (sections.length === 0) {
    issues.push({ type: 'too_short', loc: ['sections'], message: 'List should have at least 1 item' })
  }
  const seen = new Map()
  sections.forEach((section, sectionIndex) => {
    const record_ = section && typeof section === 'object' ? section : {}
    if (typeof record_.id !== 'string' || record_.id.length === 0) {
      issues.push({
        type: 'string_too_short',
        loc: ['sections', sectionIndex, 'id'],
        message: 'String should have at least 1 character',
      })
    }
    const fields = Array.isArray(record_.fields) ? record_.fields : []
    if (fields.length === 0) {
      issues.push({
        type: 'too_short',
        loc: ['sections', sectionIndex, 'fields'],
        message: 'List should have at least 1 item',
      })
    }
    fields.forEach((value, fieldIndex) => {
      const field_ = value && typeof value === 'object' ? value : {}
      const base = ['sections', sectionIndex, 'fields', fieldIndex]
      for (const key of ['id', 'label', 'question']) {
        if (typeof field_[key] !== 'string' || field_[key].length === 0) {
          issues.push({
            type: 'string_too_short',
            loc: [...base, key],
            message: 'String should have at least 1 character',
          })
        }
      }
      if (!FIELD_TYPES.includes(field_.type)) {
        issues.push({
          type: 'literal_error',
          loc: [...base, 'type'],
          message: `Input should be ${FIELD_TYPES.map((item) => `'${item}'`).join(' or ')}`,
        })
      }
      if (field_.type === 'enum') {
        const options = Array.isArray(field_.options) ? field_.options : []
        if (options.length === 0) {
          issues.push({
            type: 'value_error',
            loc: base,
            message: `Value error, enum field '${field_.id}' must define a non-empty options list`,
          })
        }
      }
      if (typeof field_.id === 'string' && field_.id.length > 0) {
        if (seen.has(field_.id)) {
          issues.push({
            type: 'value_error',
            loc: [],
            message: `Value error, duplicate field id '${field_.id}' in sections '${seen.get(field_.id)}' and '${record_.id}'`,
          })
        } else {
          seen.set(field_.id, record_.id)
        }
      }
    })
  })
  return { valid: issues.length === 0, issues }
}

const state = {
  userSchemaCreation: true,
  schemas: [
    describeDraft(
      definitionFor(
        'generic_research_paper',
        '1.0',
        'Generic Research Paper',
        'The default Schema used to describe a paper.',
        [
          {
            id: 'overview',
            label: 'Overview',
            fields: [field({})],
          },
        ],
      ),
    ),
    describeDraft(
      definitionFor('transit_policy', '1.0', 'Transit Policy Analysis', 'Earlier policy Schema.', [
        {
          id: 'policy',
          label: 'Policy',
          fields: [field({ id: 'measure', label: 'Measure', question: 'What measure is evaluated?' })],
        },
      ]),
    ),
  ],
  workspaces: {},
}

const requests = []

function record(method, url, body) {
  const entry = {
    method,
    path: url.pathname,
    query: Object.fromEntries(url.searchParams.entries()),
    body,
    /** Parsed response payload, filled in by the fetch shim. */
    responseBody: undefined,
  }
  requests.push(entry)
  return entry
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

function findSchema(schemaId, version) {
  return state.schemas.find((item) => item.schema_id === schemaId && item.version === version) ?? null
}

function workspaceRecord(workspaceId, name, schema) {
  return {
    workspace_id: workspaceId,
    name,
    status: 'active',
    schema_mode: schema ? 'bound' : 'none',
    schema_binding: schema
      ? {
          schema_id: schema.schema_id,
          schema_version: schema.version,
          schema_hash: schema.schema_hash,
        }
      : null,
    revision: 1,
    created_at: TS,
    updated_at: TS,
  }
}

function handle(method, url, body) {
  const pathname = url.pathname

  // ---- system
  if (method === 'GET' && pathname === '/api/v1/health') {
    return json({ status: 'ok' })
  }
  if (method === 'GET' && pathname === '/api/v1/capabilities') {
    return json({
      pause_resume: true,
      user_schema_creation: state.userSchemaCreation,
      base_wiki: true,
      agentic_wiki: true,
      semantic_wiki_search: true,
      pdf_upload_max_bytes: 10485760,
    })
  }

  // ---- schemas
  if (method === 'GET' && pathname === '/api/v1/schemas') {
    return json(state.schemas)
  }
  if (method === 'POST' && pathname === '/api/v1/schemas/validate') {
    return json(validateDraft(body))
  }
  if (method === 'POST' && pathname === '/api/v1/schemas') {
    const existing = findSchema(body?.schema_id, body?.version)
    if (existing) {
      return errorEnvelope(409, 'SCHEMA_VERSION_EXISTS', 'Schema version already exists')
    }
    const created = describeDraft(body)
    state.schemas.push(created)
    return json(created, 201)
  }
  const versionMatch = /^\/api\/v1\/schemas\/([^/]+)\/versions\/([^/]+)$/.exec(pathname)
  if (method === 'GET' && versionMatch) {
    const record_ = findSchema(decodeURIComponent(versionMatch[1]), decodeURIComponent(versionMatch[2]))
    return record_ ? json(record_) : errorEnvelope(404, 'NOT_FOUND', 'Schema not found')
  }

  // ---- workspaces
  if (method === 'GET' && pathname === '/api/v1/workspaces') {
    return json({ items: Object.values(state.workspaces) })
  }
  if (method === 'POST' && pathname === '/api/v1/workspaces') {
    const workspaceId = `W${Object.keys(state.workspaces).length + 1}`
    const schema = body?.schema
      ? findSchema(body.schema.schema_id, body.schema.version)
      : null
    if (body?.schema && !schema) {
      return errorEnvelope(404, 'NOT_FOUND', 'Schema not found')
    }
    const created = workspaceRecord(workspaceId, body?.name ?? 'Untitled', schema)
    state.workspaces[workspaceId] = created
    return json(created, 201)
  }
  const workspaceMatch = /^\/api\/v1\/workspaces\/([^/]+)$/.exec(pathname)
  if (method === 'GET' && workspaceMatch) {
    const record_ = state.workspaces[decodeURIComponent(workspaceMatch[1])]
    return record_ ? json(record_) : errorEnvelope(404, 'NOT_FOUND', 'Unknown workspace')
  }
  const workspacePapersMatch = /^\/api\/v1\/workspaces\/([^/]+)\/papers$/.exec(pathname)
  if (method === 'GET' && workspacePapersMatch) {
    return json({ items: [] })
  }

  // ---- papers
  if (method === 'GET' && pathname === '/api/v1/papers') {
    return json({ items: [] })
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
    const entry = record(method, url, body)
    let response
    try {
      response = handle(method, url, body)
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('FAIL:')) {
        throw error
      }
      response = errorEnvelope(500, 'SMOKE_STANDIN_ERROR', `stand-in route failed: ${error}`)
    }
    // Keep the API payload the UI actually received, so assertions can compare
    // the rendered state against the API result rather than re-deriving it.
    response
      .clone()
      .json()
      .then((payload) => {
        entry.responseBody = payload
      })
      .catch(() => {
        entry.responseBody = null
      })
    return response
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
  const allTestIds = (id) => Array.from(document.querySelectorAll(`[data-testid="${id}"]`))
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
    const prototype =
      input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, value)
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
    input.dispatchEvent(new window.Event('change', { bubbles: true }))
  }

  function setSelectValue(select, value) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set
    setter.call(select, value)
    select.dispatchEvent(new window.Event('change', { bubbles: true }))
    select.dispatchEvent(new window.Event('input', { bubbles: true }))
  }

  function toggle(input) {
    input.click()
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

  function editControlsWithin(element) {
    return Array.from(element.querySelectorAll('button')).filter((button) =>
      /edit/i.test(button.textContent ?? ''),
    )
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
    setSelectValue,
    toggle,
    applicationError,
    assertNoApplicationError,
    editControlsWithin,
  }
}

function findRequests(method, pathname) {
  return requests.filter((request) => request.method === method && request.path === pathname)
}

/* ------------------------------------------------------------------- setup */

const bundleFile = bundlePath()
const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-schemas-smoke-'))
let scenarioCount = 0

/** Open the real built app in a fresh jsdom window and run one scenario. */
async function withApp({ path: initialPath }, run) {
  scenarioCount += 1
  const dom = new JSDOM(html, { url: `${origin}${initialPath}`, pretendToBeVisual: true })
  const { window } = dom
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

/** Fill the initial one-Section/one-Field draft through the rendered inputs. */
async function fillDraft(dom_, options) {
  const {
    schemaId,
    version,
    sectionId = 'overview',
    sectionLabel = 'Overview',
    fieldId = 'research_question',
    fieldLabel = 'Research question',
    question = 'What research question does the paper address?',
    fieldType = 'string',
    options: enumOptions = '',
  } = options
  dom_.setInputValue(dom_.requireTestId('schema-id-input'), schemaId)
  dom_.setInputValue(dom_.requireTestId('schema-version-input'), version)
  dom_.setInputValue(dom_.requireTestId('schema-section-id-0'), sectionId)
  dom_.setInputValue(dom_.requireTestId('schema-section-label-0'), sectionLabel)
  dom_.setInputValue(dom_.requireTestId('schema-field-id-0-0'), fieldId)
  dom_.setInputValue(dom_.requireTestId('schema-field-label-0-0'), fieldLabel)
  dom_.setInputValue(dom_.requireTestId('schema-field-question-0-0'), question)
  if (fieldType !== 'string') {
    dom_.setSelectValue(dom_.requireTestId('schema-field-type-0-0'), fieldType)
  }
  if (fieldType === 'enum') {
    await waitFor('the enum options input', () => dom_.byTestId('schema-field-options-0-0'))
    dom_.setInputValue(dom_.requireTestId('schema-field-options-0-0'), enumOptions)
  }
}

/** Validate the current draft and return whether the API accepted it. */
async function validateDraftThroughUi(dom_) {
  const before = findRequests('POST', '/api/v1/schemas/validate').length
  dom_.clickTestId('schema-validate-submit')
  const request = await waitFor('the validation response', () => {
    const all = findRequests('POST', '/api/v1/schemas/validate')
    const last = all[all.length - 1]
    return all.length > before && last && last.responseBody !== undefined ? last : false
  })
  const expected = request.responseBody?.valid === true ? 'valid' : 'invalid'
  if (expected === 'valid') {
    await waitFor('the immutable-version confirmation step', () => dom_.byTestId('schema-builder-confirm'))
  } else {
    await waitFor('the rejected draft state', () => dom_.byTestId('schema-validation-rejected'))
  }
  return expected
}

/** Confirm the immutable version and create it through the UI. */
async function confirmAndCreate(dom_) {
  await waitFor('the immutable-version confirmation step', () => dom_.byTestId('schema-builder-confirm'))
  const checkbox = dom_.requireTestId('schema-immutable-confirm')
  assert(!checkbox.checked, 'the immutable-version confirmation started pre-checked')
  assert(
    dom_.requireTestId('schema-create-submit').disabled,
    'creation was offered before the immutable-version confirmation was given',
  )
  const before = findRequests('POST', '/api/v1/schemas').length
  dom_.toggle(checkbox)
  await waitFor('the confirmation to be accepted', () => !dom_.requireTestId('schema-create-submit').disabled)
  dom_.clickTestId('schema-create-submit')
  await waitFor('the creation request', () => {
    const all = findRequests('POST', '/api/v1/schemas')
    return all.length > before ? all[all.length - 1] : false
  })
  return findRequests('POST', '/api/v1/schemas').slice(-1)[0]
}

let exitCode = 0
try {
  /* ------------------------------- A. Catalog and read-only version ----- */

  await withApp({ path: '/schemas' }, async (window, dom_) => {
    const catalog = await waitFor('the Schema catalog list', () => dom_.byTestId('schema-catalog-list'))
    const catalogText = catalog.textContent ?? ''
    assert(catalogText.includes('generic_research_paper'), 'the catalog did not list generic_research_paper')
    assert(catalogText.includes('transit_policy'), 'the catalog did not list transit_policy')
    assert(
      dom_.byTestId('schema-catalog-item-generic_research_paper@1.0'),
      'the catalog did not list generic_research_paper version 1.0',
    )
    assert(
      dom_.byTestId('schema-catalog-item-transit_policy@1.0'),
      'the catalog did not list transit_policy version 1.0',
    )

    // A created version offers no edit-in-place action.
    assert(
      dom_.editControlsWithin(catalog).length === 0,
      'the Schema catalog offered an edit action',
    )

    // Open one version read-only.
    const openLink = dom_.document.querySelector(
      'a[href="/schemas/generic_research_paper/versions/1.0"]',
    )
    assert(openLink, 'the catalog did not link to the Schema version detail')
    dom_.click(openLink)

    const detail = await waitFor('the Schema version detail', () => dom_.byTestId('schema-version-detail'))
    const detailText = detail.textContent ?? ''
    assert(detailText.includes('generic_research_paper'), 'the detail did not show the Schema ID')
    assert(
      dom_.requireTestId('schema-version-value').textContent?.trim() === '1.0',
      'the detail did not show the API version',
    )
    assert(detailText.includes('Research question'), 'the detail did not render the stored fields')
    assert(
      dom_.byTestId('schema-version-section-overview'),
      'the detail did not render the stored Sections',
    )
    assert(dom_.byTestId('schema-version-immutable'), 'the detail did not state that the version is immutable')
    assert(
      dom_.editControlsWithin(detail).length === 0,
      'the Schema version detail offered an edit-in-place action',
    )
    assert(
      detail.querySelector('input, select, textarea') === null,
      'the Schema version detail rendered editable controls',
    )
    assertNoEditEndpoint()
    dom_.assertNoApplicationError('the Schema catalog and version detail')
    pass('the catalog listed Schema versions and one version opened read-only with no edit action')
  })

  /* --------------------------- B. Invalid draft, then valid creation ---- */

  await withApp({ path: '/schemas' }, async (window, dom_) => {
    await waitFor('the Schema catalog list', () => dom_.byTestId('schema-catalog-list'))
    dom_.clickTestId('new-schema-button')
    await waitFor('the Schema builder dialog', () => dom_.byTestId('schema-builder-dialog'))
    assert(
      dom_.byTestId('schema-immutability-guidance'),
      'the builder did not show immutability guidance while editing',
    )
    assert(dom_.byTestId('schema-add-section'), 'the builder did not offer adding a Section')
    assert(dom_.byTestId('schema-add-field-0'), 'the builder did not offer adding a Field')

    // An intentionally incomplete draft: invalid identity, blank Field text,
    // and an enum Field without options.
    dom_.setInputValue(dom_.requireTestId('schema-id-input'), 'bad draft')
    dom_.setInputValue(dom_.requireTestId('schema-version-input'), '2.0')
    dom_.setInputValue(dom_.requireTestId('schema-section-id-0'), 'overview')
    dom_.setInputValue(dom_.requireTestId('schema-section-label-0'), 'Overview')
    dom_.setInputValue(dom_.requireTestId('schema-field-id-0-0'), 'priority')
    dom_.setSelectValue(dom_.requireTestId('schema-field-type-0-0'), 'enum')
    await waitFor('the enum options input', () => dom_.byTestId('schema-field-options-0-0'))

    const firstOutcome = await validateDraftThroughUi(dom_)
    assert(firstOutcome === 'invalid', 'the API rejected the draft but the UI advanced to creation')
    const summary = dom_.requireTestId('schema-validation-summary')
    assert(
      (summary.textContent ?? '').includes('required'),
      `the validation summary did not explain the missing values: ${summary.textContent}`,
    )
    const inline = dom_.requireTestId('schema-field-issues-0-0')
    assert(
      (inline.textContent ?? '').includes('non-empty options list'),
      `the enum issue was not shown beside the Field: ${inline.textContent}`,
    )
    assert(
      dom_.byTestId('schema-validation-rejected'),
      'the rejected draft was not marked as rejected',
    )
    assert(!dom_.byTestId('schema-builder-confirm'), 'an invalid draft reached the confirmation step')
    assert(
      findRequests('POST', '/api/v1/schemas').length === 0,
      'an invalid draft issued a creation request',
    )
    pass('an invalid Schema draft showed API validation issues and could not be created')

    // Complete the draft; the same validation API now accepts it.
    await fillDraft(dom_, {
      schemaId: 'transit_policy',
      version: '2.0',
      fieldId: 'priority',
      fieldLabel: 'Priority',
      question: 'What priority does the paper assign?',
      fieldType: 'enum',
      options: 'high, medium, low',
    })
    const secondOutcome = await validateDraftThroughUi(dom_)
    assert(secondOutcome === 'valid', 'a complete draft was not accepted by the validation API')

    const immutableWarning = dom_.requireTestId('schema-version-immutable-warning')
    assert(
      /cannot be edited|immutable/i.test(immutableWarning.textContent ?? ''),
      'the confirmation did not state that the version is immutable',
    )
    assert(
      (dom_.requireTestId('schema-draft-review').textContent ?? '').includes('transit_policy'),
      'the confirmation did not review the draft identity',
    )

    const createRequest = await confirmAndCreate(dom_)
    const body = createRequest.body
    assert(body.schema_id === 'transit_policy', `the created Schema ID was ${body.schema_id}`)
    assert(body.version === '2.0', `the created version was ${body.version}`)
    assert(body.sections.length === 1, 'the created draft did not contain exactly one Section')
    assert(body.sections[0].id === 'overview', 'the Section ID did not reach the API')
    assert(body.sections[0].label === 'Overview', 'the Section label did not reach the API')
    const createdField = body.sections[0].fields[0]
    assert(createdField.id === 'priority', 'the Field ID did not reach the API')
    assert(createdField.type === 'enum', 'the Field type did not reach the API')
    assert(
      JSON.stringify(createdField.options) === JSON.stringify(['high', 'medium', 'low']),
      `the enum options were not split into a list: ${JSON.stringify(createdField.options)}`,
    )
    assert(createdField.evidence_required === true, 'the evidence setting did not reach the API')

    await waitFor('the created-Schema banner', () => dom_.byTestId('schema-created-banner'))
    assert(
      (dom_.requireTestId('schema-created-banner').textContent ?? '').includes('transit_policy'),
      'the created-Schema banner did not name the new version',
    )
    await waitFor(
      'the catalog to list the new version',
      () => dom_.byTestId('schema-catalog-item-transit_policy@2.0'),
    )
    dom_.assertNoApplicationError('the Schema builder flow')
    pass('a valid Schema draft was created only after the immutable-version confirmation')
  })

  /* ------------------------------------- C. Creation conflict is explicit */

  await withApp({ path: '/schemas' }, async (window, dom_) => {
    await waitFor('the Schema catalog list', () => dom_.byTestId('schema-catalog-list'))
    dom_.clickTestId('new-schema-button')
    await waitFor('the Schema builder dialog', () => dom_.byTestId('schema-builder-dialog'))
    await fillDraft(dom_, { schemaId: 'transit_policy', version: '2.0' })
    const outcome = await validateDraftThroughUi(dom_)
    assert(outcome === 'valid', 'the duplicate draft was not valid at definition level')
    await confirmAndCreate(dom_)

    const conflict = await waitFor('the creation conflict state', () => {
      const block = dom_.applicationError()
      return block && (block.textContent ?? '').includes('SCHEMA_VERSION_EXISTS') ? block : false
    })
    assert(
      (conflict.textContent ?? '').includes('HTTP 409'),
      `the conflict did not report the API status: ${conflict.textContent}`,
    )
    assert(
      dom_.byTestId('schema-builder-confirm'),
      'the conflict closed the builder instead of reporting the API error',
    )
    pass('an API creation conflict was shown explicitly instead of being treated as success')
  })

  /* --------------------- D. Workspace creation selects the new version --- */

  await withApp({ path: '/workspaces' }, async (window, dom_) => {
    await waitFor('the Workspace list', () => dom_.byTestId('new-workspace-button'))
    dom_.clickTestId('new-workspace-button')
    await waitFor('the new-workspace dialog', () => dom_.byTestId('create-workspace-dialog'))
    dom_.setInputValue(dom_.requireTestId('workspace-name-input'), 'Schema builder smoke workspace')
    dom_.clickTestId('schema-mode-schema')

    const select = await waitFor('the Schema catalog options', () => {
      const candidate = dom_.byTestId('schema-select')
      return candidate && candidate.options.length > 1 ? candidate : false
    })
    const optionValues = Array.from(select.options).map((option) => option.value)
    assert(
      optionValues.includes('transit_policy@2.0'),
      `the newly created Schema version was not selectable: ${JSON.stringify(optionValues)}`,
    )
    assert(
      (dom_.requireTestId('schema-binding-warning').textContent ?? '').includes('permanent'),
      'the permanent Schema binding warning was not shown',
    )

    const before = findRequests('POST', '/api/v1/workspaces').length
    dom_.setSelectValue(select, 'transit_policy@2.0')
    dom_.clickTestId('create-workspace-submit')
    const createRequest = await waitFor('the Workspace creation request', () => {
      const all = findRequests('POST', '/api/v1/workspaces')
      return all.length > before ? all[all.length - 1] : false
    })
    assert(
      createRequest.body?.schema?.schema_id === 'transit_policy' &&
        createRequest.body?.schema?.version === '2.0',
      `the Workspace was not created with the new Schema version: ${JSON.stringify(createRequest.body)}`,
    )

    const version = await waitFor(
      'the Workspace settings Schema version',
      () => dom_.byTestId('workspace-schema-version'),
      20000,
    )
    assert(
      version.textContent?.trim() === '2.0',
      `the Workspace settings showed version ${version.textContent} instead of 2.0`,
    )
    dom_.assertNoApplicationError('the Workspace creation with the new Schema version')
    pass('a Workspace was created bound to the Schema version created through the Schema Builder')
  })

  /* ----------------------- E. Schema creation capability unavailable ----- */

  state.userSchemaCreation = false
  await withApp({ path: '/schemas' }, async (window, dom_) => {
    await waitFor('the Schema catalog list', () => dom_.byTestId('schema-catalog-list'))
    await waitFor(
      'the Schema creation unavailable state',
      () => dom_.byTestId('schema-creation-unavailable'),
    )
    assert(
      dom_.requireTestId('new-schema-button').disabled,
      'Schema creation was offered although the capabilities API reports it unavailable',
    )
    dom_.assertNoApplicationError('the Schema catalog without Schema creation')
    pass('an unavailable Schema creation capability was reported explicitly')
  })

  process.stdout.write('PASS: Schema catalog and Schema Builder UI smoke completed\n')
} catch (error) {
  exitCode = 1
  process.stderr.write(`FAIL: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`)
} finally {
  rmSync(scratch, { recursive: true, force: true })
}

process.exit(exitCode)

/** The frontend has no edit/update Schema endpoint, so no request may target one. */
function assertNoEditEndpoint() {
  const offenders = requests.filter(
    (request) =>
      /^\/api\/v1\/schemas\/[^/]+\/versions\/[^/]+$/.test(request.path) &&
      request.method !== 'GET',
  )
  assert(
    offenders.length === 0,
    `the UI issued a non-read Schema version request: ${JSON.stringify(offenders)}`,
  )
}
