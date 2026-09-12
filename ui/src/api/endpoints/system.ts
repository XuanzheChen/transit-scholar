import type { ApiClient } from '../client'
import type { CapabilityResponse, HealthResponse } from '../types'

/** System-level endpoints: connectivity, capability discovery. */
export function createSystemEndpoints(client: ApiClient) {
  return {
    health: (signal?: AbortSignal) => client.get<HealthResponse>('/api/v1/health', { signal }),
    capabilities: (signal?: AbortSignal) =>
      client.get<CapabilityResponse>('/api/v1/capabilities', { signal }),
  }
}

export type SystemEndpoints = ReturnType<typeof createSystemEndpoints>
