import type { ApiCallOptions, ApiClient } from '../client'
import type { RunState, TimelineResponse } from '../types'

/** AgentRun status and Timeline endpoints. Polling only; no SSE/WebSocket. */
export function createRunEndpoints(client: ApiClient) {
  return {
    read: (agentRunId: string, options: ApiCallOptions = {}) =>
      client.get<RunState>(`/api/v1/runs/${encodeURIComponent(agentRunId)}`, options),
    timeline: (agentRunId: string, afterSequence = 0, options: ApiCallOptions = {}) =>
      client.get<TimelineResponse>(`/api/v1/runs/${encodeURIComponent(agentRunId)}/timeline`, {
        ...options,
        query: { after_sequence: afterSequence },
      }),
    pause: (agentRunId: string, options: ApiCallOptions = {}) =>
      client.post<RunState>(`/api/v1/runs/${encodeURIComponent(agentRunId)}/pause`, undefined, options),
    resume: (agentRunId: string, options: ApiCallOptions = {}) =>
      client.post<RunState>(`/api/v1/runs/${encodeURIComponent(agentRunId)}/resume`, undefined, options),
  }
}

export type RunEndpoints = ReturnType<typeof createRunEndpoints>
