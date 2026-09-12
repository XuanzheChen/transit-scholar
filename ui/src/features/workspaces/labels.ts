import type { BadgeTone } from '../../components/Badge'
import type { SchemaResponse, Workspace } from '../../api'

/** Stable option key for one Schema definition/version pair. */
export function schemaOptionKey(schema: Pick<SchemaResponse, 'schema_id' | 'version'>): string {
  return `${schema.schema_id}@${schema.version}`
}

/** User-facing label for one Schema definition/version pair. */
export function describeSchemaOption(schema: SchemaResponse): string {
  const label = schema.name ?? schema.schema_id
  return `${label} · version ${schema.version}`
}

/** User-facing Schema description for an open Workspace. */
export function describeWorkspaceSchema(workspace: Workspace): string {
  if (workspace.schema_mode === 'none') {
    return 'No Schema'
  }
  if (workspace.schema_binding) {
    const { schema_id, schema_version } = workspace.schema_binding
    return `${schema_id} · version ${schema_version}`
  }
  return 'Schema bound'
}

/** User-facing Schema description for a settings/context line. */
export function describeWorkspaceSchemaMode(workspace: Workspace): string {
  if (workspace.schema_mode === 'none') {
    return 'No Schema'
  }
  if (workspace.schema_binding) {
    return `${workspace.schema_binding.schema_id} version ${workspace.schema_binding.schema_version}`
  }
  return 'Schema bound'
}

/** Status tone mapping driven by API-reported Workspace status. */
export function workspaceStatusTone(status: string): BadgeTone {
  switch (status) {
    case 'active':
      return 'success'
    case 'archived':
      return 'warning'
    case 'deleted':
      return 'danger'
    default:
      return 'neutral'
  }
}

/*
 * Per-Paper Workspace Schema readiness.
 *
 * The status and code below are exactly what
 * `GET /api/v1/workspaces/{id}/papers/{paper_id}/schema` reports. The frontend
 * never decides readiness for itself: it only labels the API's answer.
 */

/** Status tone mapping driven by API-reported per-Paper Schema readiness. */
export function paperSchemaStatusTone(status: string | null | undefined): BadgeTone {
  switch (status) {
    case 'ready':
      return 'success'
    case 'missing':
      return 'warning'
    case 'disabled':
      return 'neutral'
    default:
      return 'neutral'
  }
}

/** User-facing label for the API-reported per-Paper Schema readiness status. */
export function describePaperSchemaStatus(status: string | null | undefined): string {
  switch (status) {
    case 'ready':
      return 'Schema ready'
    case 'missing':
      return 'Schema not materialized'
    case 'disabled':
      return 'Schema not used'
    case undefined:
    case null:
      return 'Status unknown'
    default:
      return status
  }
}

/**
 * Plain-language explanation of the API's per-Paper Schema response.
 *
 * `null` means the API reported a ready Paper (no code to explain).
 */
export function describePaperSchemaCode(code: string | null | undefined): string | null {
  switch (code) {
    case 'schema_disabled':
      return 'This workspace has no Schema binding, so no paper Schema content is tracked for it.'
    case 'schema_missing':
      return 'No Schema materialization run is stored for this paper yet.'
    case 'schema_binding_mismatch':
      return 'The stored Schema run does not match this workspace\'s Schema binding, so it is not reported as ready.'
    case undefined:
    case null:
      return null
    default:
      return code
  }
}

/**
 * True when the API reported a Schema state that materialization can act on.
 *
 * Only an API-reported `missing` Paper is offered for materialization: a
 * `ready` Paper needs nothing, and a `disabled` Paper belongs to a Workspace
 * without a Schema binding where materialization does not apply.
 */
export function isPaperSchemaMaterializable(status: string | null | undefined): boolean {
  return status === 'missing'
}
