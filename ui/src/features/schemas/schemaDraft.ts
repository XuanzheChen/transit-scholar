/**
 * Structured Schema draft model for the Schema Builder.
 *
 * The draft is a purely presentational editing model. It is converted into the
 * frozen Schema definition shape immediately before it is sent to
 * `POST /api/v1/schemas/validate` or `POST /api/v1/schemas`. No validation or
 * immutability rule is decided here: the API remains the single authority for
 * whether a draft is valid and whether a version may be created.
 */
import type { SchemaDraftRequest } from '../../api'

/** Field types accepted by the frozen Schema contract. */
export const SCHEMA_FIELD_TYPES = ['string', 'number', 'boolean', 'enum', 'list', 'object'] as const

export type SchemaFieldType = (typeof SCHEMA_FIELD_TYPES)[number]

export interface SchemaDraftField {
  /** Stable editing key; never sent to the API. */
  key: string
  id: string
  label: string
  question: string
  description: string
  type: SchemaFieldType
  /** Comma-separated options for an `enum` field; empty for other types. */
  options: string
  evidenceRequired: boolean
  allowInference: boolean
}

export interface SchemaDraftSection {
  /** Stable editing key; never sent to the API. */
  key: string
  id: string
  label: string
  fields: SchemaDraftField[]
}

export interface SchemaDraftDefinition {
  schemaId: string
  version: string
  name: string
  description: string
  sections: SchemaDraftSection[]
}

let keySeed = 0

function nextKey(prefix: string): string {
  keySeed += 1
  return `${prefix}-${keySeed}`
}

export function createDraftField(overrides: Partial<Omit<SchemaDraftField, 'key'>> = {}): SchemaDraftField {
  return {
    key: nextKey('field'),
    id: '',
    label: '',
    question: '',
    description: '',
    type: 'string',
    options: '',
    evidenceRequired: true,
    allowInference: true,
    ...overrides,
  }
}

export function createDraftSection(
  overrides: Partial<Omit<SchemaDraftSection, 'key' | 'fields'>> = {},
): SchemaDraftSection {
  return {
    key: nextKey('section'),
    id: '',
    label: '',
    fields: [createDraftField()],
    ...overrides,
  }
}

/** Initial draft: one empty section with one empty field so the form is usable. */
export function createEmptySchemaDraft(): SchemaDraftDefinition {
  return {
    schemaId: '',
    version: '',
    name: '',
    description: '',
    sections: [createDraftSection()],
  }
}

function collapsed(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

/** Parse the comma-separated enum options editor value into a clean list. */
export function parseOptions(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

/**
 * Convert the editing model into the frozen Schema definition request shape.
 *
 * Only the fields the frozen contract accepts are produced; `options` is
 * attached only for `enum` fields and `constraints` stays an empty object.
 */
export function toSchemaDraftRequest(draft: SchemaDraftDefinition): SchemaDraftRequest {
  return {
    schema_id: draft.schemaId.trim(),
    version: draft.version.trim(),
    name: collapsed(draft.name),
    description: collapsed(draft.description),
    sections: draft.sections.map((section) => ({
      id: section.id.trim(),
      label: section.label.trim(),
      fields: section.fields.map((field) => {
        const definition: Record<string, unknown> = {
          id: field.id.trim(),
          label: field.label.trim(),
          question: field.question.trim(),
          description: field.description,
          type: field.type,
          constraints: {},
          evidence_required: field.evidenceRequired,
          allow_inference: field.allowInference,
        }
        if (field.type === 'enum') {
          definition.options = parseOptions(field.options)
        }
        return definition
      }),
    })),
  }
}

/* --------------------------------------------------------- draft mutations */

function mapSection(
  draft: SchemaDraftDefinition,
  sectionIndex: number,
  update: (section: SchemaDraftSection) => SchemaDraftSection,
): SchemaDraftDefinition {
  return {
    ...draft,
    sections: draft.sections.map((section, index) => (index === sectionIndex ? update(section) : section)),
  }
}

export function withSectionAdded(draft: SchemaDraftDefinition): SchemaDraftDefinition {
  return { ...draft, sections: [...draft.sections, createDraftSection()] }
}

export function withSectionRemoved(draft: SchemaDraftDefinition, sectionIndex: number): SchemaDraftDefinition {
  if (draft.sections.length <= 1) {
    return draft
  }
  return { ...draft, sections: draft.sections.filter((_, index) => index !== sectionIndex) }
}

export function withSectionUpdated(
  draft: SchemaDraftDefinition,
  sectionIndex: number,
  patch: Partial<Pick<SchemaDraftSection, 'id' | 'label'>>,
): SchemaDraftDefinition {
  return mapSection(draft, sectionIndex, (section) => ({ ...section, ...patch }))
}

export function withFieldAdded(draft: SchemaDraftDefinition, sectionIndex: number): SchemaDraftDefinition {
  return mapSection(draft, sectionIndex, (section) => ({
    ...section,
    fields: [...section.fields, createDraftField()],
  }))
}

export function withFieldRemoved(
  draft: SchemaDraftDefinition,
  sectionIndex: number,
  fieldIndex: number,
): SchemaDraftDefinition {
  return mapSection(draft, sectionIndex, (section) => {
    if (section.fields.length <= 1) {
      return section
    }
    return { ...section, fields: section.fields.filter((_, index) => index !== fieldIndex) }
  })
}

export function withFieldUpdated(
  draft: SchemaDraftDefinition,
  sectionIndex: number,
  fieldIndex: number,
  patch: Partial<Omit<SchemaDraftField, 'key'>>,
): SchemaDraftDefinition {
  return mapSection(draft, sectionIndex, (section) => ({
    ...section,
    fields: section.fields.map((field, index) => (index === fieldIndex ? { ...field, ...patch } : field)),
  }))
}

/* ------------------------------------------------------- validation issues */

/** A validation issue as returned by `POST /api/v1/schemas/validate`. */
export interface SchemaDraftIssue {
  type: string
  loc: (string | number)[]
  message: string
}

export interface IssueLocation {
  sectionIndex: number | null
  fieldIndex: number | null
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

/** Narrow the API issue objects without inventing or reordering information. */
export function toDraftIssues(issues: Record<string, unknown>[]): SchemaDraftIssue[] {
  return issues.map((issue) => {
    const record = asRecord(issue)
    return {
      type: asString(record.type, 'validation_error'),
      loc: Array.isArray(record.loc) ? (record.loc as (string | number)[]) : [],
      message: asString(record.message, 'The Schema draft is not valid.'),
    }
  })
}

/**
 * Map an API issue location such as `["sections", 0, "fields", 1, "label"]`
 * back onto the draft so the issue can be shown beside the relevant input.
 */
export function readIssueLocation(issue: SchemaDraftIssue): IssueLocation {
  const [root, sectionIndex, branch, fieldIndex] = issue.loc
  if (root !== 'sections' || typeof sectionIndex !== 'number') {
    return { sectionIndex: null, fieldIndex: null }
  }
  if (branch === 'fields' && typeof fieldIndex === 'number') {
    return { sectionIndex, fieldIndex }
  }
  return { sectionIndex, fieldIndex: null }
}

export function issuesForSection(issues: SchemaDraftIssue[], sectionIndex: number): SchemaDraftIssue[] {
  return issues.filter((issue) => {
    const location = readIssueLocation(issue)
    return location.sectionIndex === sectionIndex && location.fieldIndex === null
  })
}

export function issuesForField(
  issues: SchemaDraftIssue[],
  sectionIndex: number,
  fieldIndex: number,
): SchemaDraftIssue[] {
  return issues.filter((issue) => {
    const location = readIssueLocation(issue)
    return location.sectionIndex === sectionIndex && location.fieldIndex === fieldIndex
  })
}

/** Issues that cannot be attached to one Section or Field (whole-Schema issues). */
export function schemaLevelIssues(issues: SchemaDraftIssue[]): SchemaDraftIssue[] {
  return issues.filter((issue) => readIssueLocation(issue).sectionIndex === null)
}

const REQUIRED_TYPES = new Set(['missing', 'string_too_short', 'too_short', 'list_too_short'])

/** Human-readable text for one API validation issue. */
export function describeIssue(issue: SchemaDraftIssue): string {
  if (REQUIRED_TYPES.has(issue.type)) {
    return 'This value is required.'
  }
  if (issue.type === 'string_pattern_mismatch') {
    return 'Start with a letter or number; use only letters, numbers, dots, underscores, or hyphens.'
  }
  if (issue.type === 'literal_error') {
    return 'Choose one of the supported field types.'
  }
  if (issue.type === 'invalid_schema_identity') {
    return issue.message
  }
  return issue.message
}

/** Readable location of an issue, for the summary list. */
export function describeIssueLocation(issue: SchemaDraftIssue): string {
  const location = readIssueLocation(issue)
  if (location.sectionIndex === null) {
    return 'Whole Schema'
  }
  if (location.fieldIndex === null) {
    return `Section ${location.sectionIndex + 1}`
  }
  return `Section ${location.sectionIndex + 1} · Field ${location.fieldIndex + 1}`
}

/* -------------------------------------------------- stored definition view */

export interface DefinitionFieldView {
  id: string
  label: string
  question: string
  description: string
  type: string
  options: string[]
  evidenceRequired: boolean
  allowInference: boolean
}

export interface DefinitionSectionView {
  id: string
  label: string
  fields: DefinitionFieldView[]
}

function readField(value: unknown): DefinitionFieldView {
  const record = asRecord(value)
  return {
    id: asString(record.id),
    label: asString(record.label),
    question: asString(record.question),
    description: asString(record.description),
    type: asString(record.type, 'string'),
    options: Array.isArray(record.options) ? record.options.filter((item): item is string => typeof item === 'string') : [],
    evidenceRequired: asBoolean(record.evidence_required),
    allowInference: asBoolean(record.allow_inference, true),
  }
}

/** Normalise a stored Schema definition for read-only rendering. */
export function readDefinitionSections(definition: Record<string, unknown>): DefinitionSectionView[] {
  const sections = Array.isArray(definition.sections) ? definition.sections : []
  return sections.map((value) => {
    const record = asRecord(value)
    const fields = Array.isArray(record.fields) ? record.fields : []
    return {
      id: asString(record.id),
      label: asString(record.label),
      fields: fields.map(readField),
    }
  })
}
