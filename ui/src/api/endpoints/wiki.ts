import type { ApiCallOptions, ApiClient } from '../client'
import type {
  AgenticWikiEntry,
  AgenticWikiEntryListResponse,
  BaseWikiStatus,
  WikiBuildResponse,
  WikiEntity,
  WikiEntityListResponse,
  WikiOverview,
  WikiPage,
  WikiPageListResponse,
  WikiSearchMode,
  WikiSearchResponse,
} from '../types'

/** Workspace Wiki endpoints (Schema Wiki and Agent Learned content). */
export function createWikiEndpoints(client: ApiClient) {
  return {
    overview: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.get<WikiOverview>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki`, options),
    status: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.get<BaseWikiStatus>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/status`, options),
    build: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.post<WikiBuildResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/build`,
        undefined,
        options,
      ),
    pages: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.get<WikiPageListResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/pages`,
        options,
      ),
    page: (workspaceId: string, pageId: string, options: ApiCallOptions = {}) =>
      client.get<WikiPage>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/pages/${encodeURIComponent(pageId)}`,
        options,
      ),
    entities: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.get<WikiEntityListResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/entities`,
        options,
      ),
    entity: (workspaceId: string, entityId: string, options: ApiCallOptions = {}) =>
      client.get<WikiEntity>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/entities/${encodeURIComponent(entityId)}`,
        options,
      ),
    agenticEntries: (workspaceId: string, includeStale = false, options: ApiCallOptions = {}) =>
      client.get<AgenticWikiEntryListResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/agentic-entries`,
        { ...options, query: { include_stale: includeStale ? true : undefined } },
      ),
    agenticEntry: (workspaceId: string, entryId: string, options: ApiCallOptions = {}) =>
      client.get<AgenticWikiEntry>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/agentic-entries/${encodeURIComponent(entryId)}`,
        options,
      ),
    search: (
      workspaceId: string,
      query: string,
      options: ApiCallOptions & { limit?: number; mode?: WikiSearchMode; includeStale?: boolean } = {},
    ) =>
      client.get<WikiSearchResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/wiki/search`,
        {
          signal: options.signal,
          query: {
            query,
            limit: options.limit,
            mode: options.mode,
            include_stale: options.includeStale ? true : undefined,
          },
        },
      ),
  }
}

export type WikiEndpoints = ReturnType<typeof createWikiEndpoints>
