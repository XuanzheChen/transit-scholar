import type { ApiCallOptions, ApiClient } from '../client'
import type {
  Conversation,
  ConversationCreateRequest,
  ConversationListResponse,
  ConversationSummary,
  Turn,
  TurnCreateRequest,
  TurnSubmissionResponse,
} from '../types'

/** Conversation endpoints. Conversation continuity stays server-side. */
export function createConversationEndpoints(client: ApiClient) {
  return {
    list: (workspaceId: string, options: ApiCallOptions = {}) =>
      client.get<ConversationListResponse>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/conversations`,
        options,
      ),
    create: (workspaceId: string, payload: ConversationCreateRequest = {}, options: ApiCallOptions = {}) =>
      client.post<ConversationSummary>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/conversations`,
        payload,
        options,
      ),
    read: (conversationId: string, options: ApiCallOptions = {}) =>
      client.get<Conversation>(`/api/v1/conversations/${encodeURIComponent(conversationId)}`, options),
    submitTurn: (conversationId: string, payload: TurnCreateRequest, options: ApiCallOptions = {}) =>
      client.post<TurnSubmissionResponse>(
        `/api/v1/conversations/${encodeURIComponent(conversationId)}/turns`,
        payload,
        options,
      ),
    readTurn: (turnId: string, options: ApiCallOptions = {}) =>
      client.get<Turn>(`/api/v1/turns/${encodeURIComponent(turnId)}`, options),
  }
}

export type ConversationEndpoints = ReturnType<typeof createConversationEndpoints>
