/**
 * Public entry point of the frontend API boundary.
 *
 * Import `api` for typed endpoint access and the error helpers for
 * user-visible failure handling. Nothing outside `src/api/` may call `fetch`.
 */
import { apiClient } from './client'
import { createConversationEndpoints } from './endpoints/conversations'
import { createPaperEndpoints } from './endpoints/papers'
import { createRunEndpoints } from './endpoints/runs'
import { createSchemaEndpoints } from './endpoints/schemas'
import { createSystemEndpoints } from './endpoints/system'
import { createWikiEndpoints } from './endpoints/wiki'
import { createWorkspaceEndpoints } from './endpoints/workspaces'

export const api = {
  system: createSystemEndpoints(apiClient),
  papers: createPaperEndpoints(apiClient),
  workspaces: createWorkspaceEndpoints(apiClient),
  conversations: createConversationEndpoints(apiClient),
  runs: createRunEndpoints(apiClient),
  schemas: createSchemaEndpoints(apiClient),
  wiki: createWikiEndpoints(apiClient),
}

export type Api = typeof api

export { ApiClient, ApiError, apiClient, API_PREFIX, BACKEND_UNAVAILABLE_CODE } from './client'
export { classifyApiFailure, resolveApiBaseUrl, toApiError } from './client'
export type { ApiCallOptions, ApiFailureKind, ApiRequestOptions, HttpMethod, QueryValue } from './client'
export type * from './types'
