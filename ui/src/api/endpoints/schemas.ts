import type { ApiCallOptions, ApiClient } from '../client'
import type { SchemaDraftRequest, SchemaResponse, SchemaValidationResponse } from '../types'

/** Schema catalog and Schema creation endpoints. */
export function createSchemaEndpoints(client: ApiClient) {
  return {
    list: (options: ApiCallOptions = {}) => client.get<SchemaResponse[]>('/api/v1/schemas', options),
    get: (schemaId: string, version: string, options: ApiCallOptions = {}) =>
      client.get<SchemaResponse>(
        `/api/v1/schemas/${encodeURIComponent(schemaId)}/versions/${encodeURIComponent(version)}`,
        options,
      ),
    validate: (draft: SchemaDraftRequest, options: ApiCallOptions = {}) =>
      client.post<SchemaValidationResponse>('/api/v1/schemas/validate', draft, options),
    create: (draft: SchemaDraftRequest, options: ApiCallOptions = {}) =>
      client.post<SchemaResponse>('/api/v1/schemas', draft, options),
  }
}

export type SchemaEndpoints = ReturnType<typeof createSchemaEndpoints>
