/**
 * Presentation labels for the Schema area.
 *
 * These helpers translate API values into user-facing wording. They never
 * decide Schema validity, immutability, or creation rules — those come from the
 * frozen Schema API.
 */
import type { SchemaResponse } from '../../api'
import { SCHEMA_FIELD_TYPES } from './schemaDraft'
import type { SchemaFieldType } from './schemaDraft'

export const SCHEMA_FIELD_TYPE_LABELS: Record<SchemaFieldType, string> = {
  string: 'Text',
  number: 'Number',
  boolean: 'Yes / no',
  enum: 'Choice from a list',
  list: 'List',
  object: 'Structured object',
}

export const SCHEMA_FIELD_TYPE_OPTIONS = SCHEMA_FIELD_TYPES.map((value) => ({
  value,
  label: SCHEMA_FIELD_TYPE_LABELS[value],
}))

/** Stable identity of one Schema definition + version. */
export function schemaVersionKey(schema: Pick<SchemaResponse, 'schema_id' | 'version'>): string {
  return `${schema.schema_id}@${schema.version}`
}

/** Client-side route of the read-only Schema version detail view. */
export function schemaVersionHref(schema: Pick<SchemaResponse, 'schema_id' | 'version'>): string {
  return `/schemas/${encodeURIComponent(schema.schema_id)}/versions/${encodeURIComponent(schema.version)}`
}

/** User-facing title of a Schema version. */
export function describeSchemaVersion(schema: Pick<SchemaResponse, 'schema_id' | 'name'>): string {
  return schema.name && schema.name.trim() !== '' ? schema.name : schema.schema_id
}

/** Human label for one field type value returned by the API. */
export function describeFieldType(type: string): string {
  return SCHEMA_FIELD_TYPE_LABELS[type as SchemaFieldType] ?? type
}

/** The immutability statement shown while editing and before creation. */
export const SCHEMA_VERSION_IMMUTABLE_STATEMENT =
  'A created Schema version is immutable. It cannot be edited in place, and changes require creating a new version.'

/** The explicit confirmation required immediately before creation. */
export const SCHEMA_VERSION_CONFIRMATION_LABEL =
  'I understand this Schema version cannot be edited after creation, and that later changes require creating a new version.'
