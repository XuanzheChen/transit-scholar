import type { ApiCallOptions, ApiClient } from '../client'
import type {
  PaperSchemaStateResponse,
  SchemaMaterializationResponse,
  Workspace,
  WorkspaceCreateRequest,
  WorkspaceListResponse,
  WorkspacePaper,
  WorkspacePaperListResponse,
  WorkspaceSchemaResponse,
} from '../types'

/** Workspace lifecycle and membership endpoints. */
export function createWorkspaceEndpoints(client: ApiClient) {
  return {
    list: (options: ApiCallOptions = {}) =>
      client.get<WorkspaceListResponse>('/api/v1/workspaces', options),
    get: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.get<Workspace>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}`, options),
    create: (payload: WorkspaceCreateRequest, options: ApiCallOptions = {}) =>
      client.post<Workspace>('/api/v1/workspaces', payload, options),
    archive: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.post<Workspace>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/archive`, undefined, options),
    remove: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.delete<Workspace>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}`, options),
    listPapers: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.get<WorkspacePaperListResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/papers`,
        options,
      ),
    addPaper: (workspaceId: string, paperId: string, options: ApiCallOptions = {}) =>
      client.post<WorkspacePaper>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/papers`,
        { paper_id: paperId },
        options,
      ),
    /** Removes membership only; never deletes the paper from the Library. */
    removePaper: (workspaceId: string, paperId: string, options: ApiCallOptions = {}) =>
      client.delete<Workspace>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/papers/${encodeURIComponent(paperId)}`,
        options,
      ),
    schema: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.get<WorkspaceSchemaResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/schema`,
        options,
      ),
    paperSchema: (workspaceId: string, paperId: string, options: ApiCallOptions = {}) =>
      client.get<PaperSchemaStateResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/papers/${encodeURIComponent(paperId)}/schema`,
        options,
      ),
    materializePaperSchema: (workspaceId: string, paperId: string, options: ApiCallOptions = {}) =>
      client.post<SchemaMaterializationResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/papers/${encodeURIComponent(paperId)}/schema/materialize`,
        undefined,
        options,
      ),
  }
}

export type WorkspaceEndpoints = ReturnType<typeof createWorkspaceEndpoints>
