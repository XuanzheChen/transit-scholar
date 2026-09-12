/**
 * DOM smoke for the two user-visible backend connection states.
 *
 * Renders the real production bundle (ui/dist) inside jsdom twice:
 *
 *   1. with a failing `fetch`  -> the UI must report "Backend unavailable"
 *   2. with a healthy API      -> the UI must report "Backend connected"
 *
 * This is evidence for the requirement that the application can display both
 * states; it is not part of the production build.
 *
 * Usage: node scripts/smoke-backend-states.mjs [--dist <dir>]
 */
import { mkdtempSync, readFileSync, readdirSync, copyFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { JSDOM } from 'jsdom'

const args = process.argv.slice(2)
const distIndex = args.indexOf('--dist')
const distDir = path.resolve(distIndex >= 0 ? args[distIndex + 1] : path.join(process.cwd(), 'dist'))

function fail(message) {
  process.stderr.write(`FAIL: ${message}\n`)
  process.exit(1)
}

function bundlePath() {
  const assetsDir = path.join(distDir, 'assets')
  const candidates = readdirSync(assetsDir).filter((name) => name.endsWith('.js'))
  if (candidates.length === 0) {
    fail(`no JavaScript bundle found in ${assetsDir}; run "npm run build" first`)
  }
  return path.join(assetsDir, candidates[0])
}

const HEALTHY_CAPABILITIES = {
  pause_resume: true,
  user_schema_creation: true,
  base_wiki: true,
  agentic_wiki: false,
  semantic_wiki_search: false,
  pdf_upload_max_bytes: 104857600,
}

function response(body) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    headers: { get: () => 'application/json' },
    json: async () => body,
  }
}

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

async function renderWithFetch(fetchImpl, bundleFile) {
  const html = readFileSync(path.join(distDir, 'index.html'), 'utf8')
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8000/', pretendToBeVisual: true })
  const { window } = dom

  applyGlobals(window, fetchImpl)

  // A unique file name per render keeps the module registry from reusing the
  // previously evaluated bundle instance.
  const scratch = mkdtempSync(path.join(tmpdir(), 'transit-ui-smoke-'))
  const target = path.join(scratch, `bundle-${Date.now()}-${Math.random().toString(36).slice(2)}.mjs`)
  copyFileSync(bundleFile, target)

  try {
    await import(pathToFileURL(target).href)
    // Let the provider's first health check settle.
    await new Promise((resolve) => setTimeout(resolve, 400))
    return { text: window.document.body.textContent ?? '', close: () => window.close() }
  } finally {
    rmSync(scratch, { recursive: true, force: true })
  }
}

const bundleFile = bundlePath()

// 1. Backend unavailable ----------------------------------------------------
const unavailable = await renderWithFetch(
  () => Promise.reject(new TypeError('fetch failed')),
  bundleFile,
)
const unavailableText = unavailable.text
unavailable.close()

if (!unavailableText.includes('Backend unavailable')) {
  fail(`"Backend unavailable" was not rendered; saw: ${unavailableText.slice(0, 400)}`)
}
for (const label of ['Workspaces', 'Research', 'Library', 'Wiki', 'Schemas']) {
  if (!unavailableText.includes(label)) {
    fail(`product navigation item "${label}" missing while backend is unavailable`)
  }
}
process.stdout.write('PASS: backend-unavailable state rendered with product navigation\n')

// 2. Backend connected ------------------------------------------------------
const connected = await renderWithFetch(async (url) => {
  if (String(url).includes('/api/v1/health')) {
    return response({ status: 'healthy' })
  }
  if (String(url).includes('/api/v1/capabilities')) {
    return response(HEALTHY_CAPABILITIES)
  }
  return response({ items: [] })
}, bundleFile)
const connectedText = connected.text
connected.close()
restoreGlobals()

if (!connectedText.includes('Backend connected')) {
  fail(`"Backend connected" was not rendered; saw: ${connectedText.slice(0, 400)}`)
}
process.stdout.write('PASS: backend-connected state rendered\n')
process.stdout.write('PASS: backend connection state smoke completed\n')
