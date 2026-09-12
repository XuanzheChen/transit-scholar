/**
 * The single HTTP transport boundary for the TransitScholar Web UI.
 *
 * Every `/api/v1/*` call in the application goes through this module. View
 * components never call `fetch` directly, never interpret backend state
 * locally, and never duplicate backend business rules.
 */
import type { ApiErrorBody, ApiErrorEnvelope } from './types'

/** The frozen product API prefix. */
export const API_PREFIX = '/api/v1'

/** Code used when the browser could not reach the backend at all. */
export const BACKEND_UNAVAILABLE_CODE = 'BACKEND_UNAVAILABLE'

export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'

/** Query values accepted by the client; `undefined`/`null` entries are dropped. */
export type QueryValue = string | number | boolean | null | undefined

export interface ApiCallOptions {
  query?: Record<string, QueryValue>
  signal?: AbortSignal
}

export interface ApiRequestOptions extends ApiCallOptions {
  method?: HttpMethod
  body?: unknown
  /** Overrides the JSON content type (used for multipart uploads). */
  headers?: Record<string, string>
}

/**
 * Error raised for any failed API interaction.
 *
 * `status` is `0` and `code` is `BACKEND_UNAVAILABLE` when the request never
 * reached the backend (offline server, wrong origin, aborted network).
 */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Record<string, unknown>
  readonly method: string
  readonly path: string

  constructor(options: {
    code: string
    message: string
    status: number
    details?: Record<string, unknown>
    method: string
    path: string
  }) {
    super(options.message)
    this.name = 'ApiError'
    this.code = options.code
    this.status = options.status
    this.details = options.details ?? {}
    this.method = options.method
    this.path = options.path
  }

  /** True when the backend itself could not be reached. */
  get isBackendUnavailable(): boolean {
    return this.code === BACKEND_UNAVAILABLE_CODE
  }
}

/** Coarse failure categories used to drive user-visible UI states. */
export type ApiFailureKind =
  | 'unavailable'
  | 'unavailable_capability'
  | 'validation'
  | 'conflict'
  | 'not_found'
  | 'error'

const CONFLICT_CODES = new Set([
  'CONFLICT',
  'RUN_STATE_CONFLICT',
  'CONVERSATION_CONFLICT',
  'WORKSPACE_BUSY',
  'WORKSPACE_NOT_AVAILABLE',
  'WORKSPACE_CHANGED',
  'SCHEMA_VERSION_EXISTS',
  'SCHEMA_BINDING_IMMUTABLE',
  'PAPER_IN_USE',
  'INVALID_STATE',
  'SCHEMA_DISABLED',
  'SCHEMA_MISSING',
  'SCHEMA_BINDING_MISMATCH',
  'WIKI_UNSUPPORTED',
  'WIKI_MISSING',
  'WIKI_STALE',
  'WIKI_CORRUPT',
  'EMPTY_MEMBERSHIP',
])

const UNAVAILABLE_CAPABILITY_CODES = new Set([
  'PROVIDER_UNAVAILABLE',
  'RUNNER_BUSY',
  'FRONTEND_BUILD_MISSING',
])

/** Normalise any thrown value into an {@link ApiError}. */
export function toApiError(error: unknown, method = 'GET', path = ''): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  if (error instanceof DOMException && error.name === 'AbortError') {
    return new ApiError({
      code: 'REQUEST_ABORTED',
      message: 'The request was cancelled.',
      status: 0,
      method,
      path,
    })
  }
  if (error instanceof Error) {
    return new ApiError({
      code: BACKEND_UNAVAILABLE_CODE,
      message: 'The TransitScholar backend could not be reached.',
      status: 0,
      details: { cause: error.message },
      method,
      path,
    })
  }
  return new ApiError({
    code: BACKEND_UNAVAILABLE_CODE,
    message: 'The TransitScholar backend could not be reached.',
    status: 0,
    details: { cause: String(error) },
    method,
    path,
  })
}

/** Classify a failure for user-visible state handling. */
export function classifyApiFailure(error: unknown): ApiFailureKind {
  const apiError = toApiError(error)
  if (apiError.isBackendUnavailable) {
    return 'unavailable'
  }
  if (UNAVAILABLE_CAPABILITY_CODES.has(apiError.code)) {
    return 'unavailable_capability'
  }
  if (apiError.status === 422 || apiError.code === 'VALIDATION_ERROR') {
    return 'validation'
  }
  if (apiError.status === 409 || CONFLICT_CODES.has(apiError.code)) {
    return 'conflict'
  }
  if (apiError.status === 404 || apiError.code === 'NOT_FOUND') {
    return 'not_found'
  }
  return 'error'
}

/**
 * Base URL prepended to every `/api/v1/*` path.
 *
 * Defaults to the empty string, which means "same origin as the page". This is
 * what makes the production single-origin FastAPI hosting model work without
 * any further configuration. `VITE_API_BASE_URL` may point the UI at another
 * origin for local development or for exercising the backend-unavailable state.
 */
export function resolveApiBaseUrl(): string {
  const raw = import.meta.env.VITE_API_BASE_URL
  if (typeof raw !== 'string') {
    return ''
  }
  return raw.trim().replace(/\/+$/, '')
}

function buildUrl(baseUrl: string, path: string, query?: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === '') {
      continue
    }
    search.set(key, String(value))
  }
  const queryString = search.toString()
  return `${baseUrl}${path}${queryString ? `?${queryString}` : ''}`
}

async function readErrorEnvelope(response: Response): Promise<ApiErrorBody | null> {
  try {
    const payload: unknown = await response.json()
    if (payload && typeof payload === 'object' && 'error' in payload) {
      const envelope = payload as ApiErrorEnvelope
      if (envelope.error && typeof envelope.error === 'object') {
        return {
          code: typeof envelope.error.code === 'string' ? envelope.error.code : 'HTTP_ERROR',
          message: typeof envelope.error.message === 'string' ? envelope.error.message : response.statusText,
          details:
            envelope.error.details && typeof envelope.error.details === 'object'
              ? envelope.error.details
              : {},
        }
      }
    }
  } catch {
    return null
  }
  return null
}

export interface ApiClientOptions {
  baseUrl?: string
  /** Injection seam for tests and alternate runtimes. */
  fetchImpl?: typeof fetch
}

/**
 * Thin, typed wrapper around the browser `fetch` API.
 *
 * The client only performs transport work: URL building, envelope parsing, and
 * normalising failures. It never caches, invents, or mutates backend state.
 */
export class ApiClient {
  private readonly baseUrl: string
  private readonly fetchImpl: typeof fetch

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? resolveApiBaseUrl()
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis)
  }

  getBaseUrl(): string {
    return this.baseUrl
  }

  async request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    const response = await this.requestResponse(path, options)
    if (response.status === 204 || response.headers.get('content-length') === '0') {
      return undefined as T
    }
    try {
      return (await response.json()) as T
    } catch (error) {
      throw new ApiError({
        code: 'INVALID_RESPONSE',
        message: 'The backend response could not be read.',
        status: response.status,
        details: { cause: error instanceof Error ? error.message : String(error) },
        method: options.method ?? 'GET',
        path,
      })
    }
  }

  /** Perform a request and return the raw response (used for PDF content). */
  async requestResponse(path: string, options: ApiRequestOptions = {}): Promise<Response> {
    const method = options.method ?? 'GET'
    const url = buildUrl(this.baseUrl, path, options.query)
    const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
    let body: BodyInit | undefined
    if (options.body instanceof FormData) {
      // The browser supplies the multipart boundary; never set Content-Type.
      body = options.body
    } else if (options.body !== undefined) {
      headers['Content-Type'] = headers['Content-Type'] ?? 'application/json'
      body = JSON.stringify(options.body)
    }

    let response: Response
    try {
      response = await this.fetchImpl(url, {
        method,
        headers,
        body,
        signal: options.signal,
      })
    } catch (error) {
      throw toApiError(error, method, path)
    }

    if (!response.ok) {
      const envelope = await readErrorEnvelope(response)
      throw new ApiError({
        code: envelope?.code ?? `HTTP_${response.status}`,
        message:
          envelope?.message ??
          (response.statusText || `Request failed with status ${response.status}`),
        status: response.status,
        details: envelope?.details ?? {},
        method,
        path,
      })
    }

    return response
  }

  get<T>(path: string, options: ApiCallOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'GET' })
  }

  post<T>(path: string, body?: unknown, options: ApiCallOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'POST', body })
  }

  patch<T>(path: string, body?: unknown, options: ApiCallOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PATCH', body })
  }

  delete<T>(path: string, options: ApiCallOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'DELETE' })
  }

  /** Upload a PDF through the existing multipart import endpoint. */
  postMultipart<T>(path: string, form: FormData, options: ApiCallOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'POST', body: form })
  }
}

/** Shared client used by the endpoint modules. */
export const apiClient = new ApiClient()
