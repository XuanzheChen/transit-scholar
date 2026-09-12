import type { ApiCallOptions, ApiClient } from '../client'
import type {
  CitationRecord,
  DuplicateDecision,
  DuplicateRelationListResponse,
  DuplicateResolutionResponse,
  EnrichmentResponse,
  MetadataCandidate,
  MetadataUpdateRequest,
  PaperActionResponse,
  PaperDetail,
  PaperFile,
  PaperImportResponse,
  PaperListResponse,
  SecondLayerResponse,
} from '../types'

/** Paper library endpoints (frozen `/api/v1/papers*` contract). */
export function createPaperEndpoints(client: ApiClient) {
  return {
    list: (options: ApiCallOptions & { status?: string; includeDeleted?: boolean; limit?: number; offset?: number } = {}) =>
      client.get<PaperListResponse>('/api/v1/papers', {
        signal: options.signal,
        query: {
          status: options.status,
          include_deleted: options.includeDeleted ? true : undefined,
          limit: options.limit,
          offset: options.offset,
        },
      }),
    detail: (paperId: string, options: ApiCallOptions = {}) =>
      client.get<PaperDetail>(`/api/v1/papers/${encodeURIComponent(paperId)}`, options),
    files: (paperId: string, options: ApiCallOptions = {}) =>
      client.get<PaperFile[]>(`/api/v1/papers/${encodeURIComponent(paperId)}/files`, options),
    secondLayer: (paperId: string, options: ApiCallOptions = {}) =>
      client.get<SecondLayerResponse>(`/api/v1/papers/${encodeURIComponent(paperId)}/second-layer`, options),
    importPdf: (file: File, options: ApiCallOptions = {}) => {
      const form = new FormData()
      form.append('file', file)
      return client.postMultipart<PaperImportResponse>('/api/v1/papers/import', form, options)
    },
    updateMetadata: (paperId: string, payload: MetadataUpdateRequest, options: ApiCallOptions = {}) =>
      client.patch<PaperActionResponse>(`/api/v1/papers/${encodeURIComponent(paperId)}/metadata`, payload, options),
    reconcile: (paperId: string, options: ApiCallOptions = {}) =>
      client.post<SecondLayerResponse>(`/api/v1/papers/${encodeURIComponent(paperId)}/reconcile`, undefined, options),
    metadataCandidates: (paperId: string, options: ApiCallOptions = {}) =>
      client.get<MetadataCandidate[]>(
        `/api/v1/papers/${encodeURIComponent(paperId)}/metadata-candidates`,
        options,
      ),
    citations: (paperId: string, options: ApiCallOptions = {}) =>
      client.get<CitationRecord[]>(`/api/v1/papers/${encodeURIComponent(paperId)}/citations`, options),
    enrichment: (paperId: string, options: ApiCallOptions = {}) =>
      client.get<EnrichmentResponse>(`/api/v1/papers/${encodeURIComponent(paperId)}/enrichment`, options),
    refreshEnrichment: (paperId: string, options: ApiCallOptions = {}) =>
      client.post<EnrichmentResponse>(
        `/api/v1/papers/${encodeURIComponent(paperId)}/enrichment/refresh`,
        undefined,
        options,
      ),
    duplicateRelations: (paperId: string, options: ApiCallOptions = {}) =>
      client.get<DuplicateRelationListResponse>(
        `/api/v1/papers/${encodeURIComponent(paperId)}/duplicate-relations`,
        options,
      ),
    resolveDuplicateRelation: (relationId: string, decision: DuplicateDecision, options: ApiCallOptions = {}) =>
      client.post<DuplicateResolutionResponse>(
        `/api/v1/duplicate-relations/${encodeURIComponent(relationId)}/resolve`,
        { decision },
        options,
      ),
    removeFromLibrary: (paperId: string, options: ApiCallOptions = {}) =>
      client.delete<PaperActionResponse>(`/api/v1/papers/${encodeURIComponent(paperId)}`, options),
    restoreToLibrary: (paperId: string, options: ApiCallOptions = {}) =>
      client.post<PaperActionResponse>(`/api/v1/papers/${encodeURIComponent(paperId)}/restore`, undefined, options),
    /** Same-origin URL for the registered local PDF, opened in the browser viewer. */
    fileContentUrl: (fileId: string) =>
      `${client.getBaseUrl()}/api/v1/files/${encodeURIComponent(fileId)}/content`,
  }
}

export type PaperEndpoints = ReturnType<typeof createPaperEndpoints>
